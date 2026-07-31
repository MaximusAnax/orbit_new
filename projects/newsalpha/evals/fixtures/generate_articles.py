#!/usr/bin/env python3
"""Seeded generator for the NewsAlpha article corpus (EVALS.md "Fixture strategy").

Ground truth comes from this script's *construction parameters* -- never from the
system under evaluation.  Nothing here imports `newsalpha`: the FR-1 normalization
rule, the FR-2 shingle tokenization and the gazetteer surface scan used for the
hygiene assertions are re-implemented locally (a handful of lines each), so a bug
in the engine cannot quietly rewrite the labels it is graded against.

Two disjoint paraphrase families realize every truth event:

* **DEV** -- the family patterns may be authored against (32 truth events);
* **VAL** -- the family the gates score (76 truth events, plus all 40
  hand-authored adversarial articles, all 50 trap mentions and all 40 role
  decisions).

`--check-disjoint` asserts that the trigger-bearing word 3-grams of the two
families are disjoint, that their distractor pools share no sentence, and that
the committed cluster Jaccard margins hold (intra >= 0.72, inter <= 0.45).

Usage
-----
    python generate_articles.py                  # write articles.jsonl + articles_truth.json
    python generate_articles.py --check-disjoint # family separation only
    python generate_articles.py --regen-check    # re-derive and diff against the committed files
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent.parent / "data"

SEED = 20260731
DAY0 = date(2026, 1, 1)
#: Truth events occupy days 100-125 of the 252-day market series.  The span sits
#: inside the product's default 30-day active window (FR-15) so the committed
#: corpus is analysable from a single `as_of`, and it leaves >= 40 bars of
#: head-room on both sides for the +/-[20, 60]-bar placebo displacement and the
#: 20-bar horizons (EVALS.md "Files").
FIRST_EVENT_DAY = 100
LAST_EVENT_DAY = 125
AS_OF_DAY = 127
SERIES_DAYS = 252
ARRIVAL_BATCHES = 5

T1_DOMAINS = (
    "businesswire.example",
    "globenewswire.example",
    "prnewswire.example",
    "ir.example",
    "sec.example",
    "coinbase.example",
    "binance.example",
    "cftc.example",
)
T2_DOMAINS = (
    "reuters.example",
    "bloomberg.example",
    "wsj.example",
    "ft.example",
    "cnbc.example",
    "coindesk.example",
    "theblock.example",
    "cointelegraph.example",
    "ap.example",
    "dowjones.example",
)
T3_DOMAINS = (
    "marketchatter.example",
    "tokenwire.example",
    "dailyfin.example",
    "chainbeat.example",
    "streetnotes.example",
)
DOMAINS_BY_TIER = {"t1": T1_DOMAINS, "t2": T2_DOMAINS, "t3": T3_DOMAINS}

FIRMS = (
    "Kestrel Analytics", "Harborline Research", "Northgate Partners", "Vellum Capital",
    "Brightmoor Advisors", "Ashgrove Securities", "Pellmore Group", "Wexley Quantitative",
    "Dunmoor Research", "Larkfield Associates", "Estridge Analytics", "Marlowe Bennett",
)
CITIES = (
    "Ashford", "Marlow", "Kentbridge", "Wexley", "Dunmoor", "Calderfield",
    "Ostend Bay", "Thornbury", "Redhaven", "Pellmore", "Larkfield", "Brightmoor",
)
ANALYSTS = (
    "Priya Raghunathan", "Tomas Ekstrom", "Ingrid Halvorsen", "Miles Okonkwo",
    "Yuki Tanabe", "Rafael Duarte", "Nadia Farouk", "Soren Lindqvist",
    "Hannah Brightwell", "Owen Castellano", "Lena Vasquez", "Dmitri Anselm",
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
QUARTERS = ("first", "second", "third", "fourth")
AGENCIES = ("SEC", "CFTC", "DOJ", "FTC", "FDA")

# --------------------------------------------------------------------------- #
# Local re-implementations (independent of `newsalpha`)
# --------------------------------------------------------------------------- #

_WS_RE = re.compile(r"\s+")
_SHINGLE_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789$")
_WORD_RE = re.compile(r"[a-z0-9$]+")
_UPPER_TOKEN_RE = re.compile(r"(?<![\w$.])([A-Z][A-Z0-9]{1,8}(?:\.[A-Z0-9]{1,4})?)(?![\w])")


def normalize(text: str) -> str:
    """FR-1 normalization for markup-free text: NFKC + whitespace collapse."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def shingle_tokens(text: str) -> list[str]:
    """FR-2's pinned tokenization, re-implemented locally."""
    out: list[str] = []
    pending = False
    for char in text.casefold():
        if char in _SHINGLE_ALLOWED:
            if pending and out:
                out.append(" ")
            pending = False
            out.append(char)
        else:
            pending = True
    return "".join(out).split(" ") if out else []


def shingles(title: str, body: str) -> frozenset[tuple[str, ...]]:
    toks = shingle_tokens(f"{title} {body[:400]}")
    if not toks:
        return frozenset()
    if len(toks) < 5:
        return frozenset({tuple(toks)})
    return frozenset(tuple(toks[i : i + 5]) for i in range(len(toks) - 4))


def jaccard(a: frozenset[Any], b: frozenset[Any]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 1.0


def day_iso(offset: int) -> str:
    return (DAY0 + timedelta(days=offset)).isoformat()


def stamp(offset: int, hour: int, minute: int = 0) -> str:
    moment = datetime(DAY0.year, DAY0.month, DAY0.day) + timedelta(
        days=offset, hours=hour, minutes=minute
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Gazetteer (read from the committed dataset -- surfaces must match exactly)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GazAsset:
    id: str
    kind: str
    symbol: str
    name: str
    aliases: tuple[str, ...]
    ambiguous: bool


def load_gazetteer() -> dict[str, GazAsset]:
    raw = json.loads((DATA_DIR / "assets.json").read_text(encoding="utf-8"))["assets"]
    out: dict[str, GazAsset] = {}
    for row in raw:
        if row["kind"] == "index":
            continue
        out[row["id"]] = GazAsset(
            id=row["id"],
            kind=row["kind"],
            symbol=row["symbol"],
            name=row["name"],
            aliases=tuple(row.get("aliases", ())),
            ambiguous=bool(row.get("ambiguous", False)),
        )
    return out


def load_trigger_lexemes() -> list[str]:
    raw = json.loads((DATA_DIR / "patterns.json").read_text(encoding="utf-8"))
    return sorted({t for pattern in raw["patterns"] for t in pattern["triggers"]})


def phrase_re(phrase: str) -> re.Pattern[str]:
    body = r"[\s\-]+".join(re.escape(part) for part in phrase.split())
    return re.compile(rf"(?<![\w\-]){body}(?![\w\-])", re.IGNORECASE)


def surface_re(surface: str) -> re.Pattern[str]:
    parts = r"[\s\-]+".join(re.escape(p) for p in surface.split())
    return re.compile(rf"(?<![\w$]){parts}(?![\w])")


def scan_surfaces(text: str, gazetteer: dict[str, GazAsset]) -> set[str]:
    """Gazetteer assets whose name/alias/symbol occurs literally in `text`.

    Deliberately over-eager (no context rules, no all-caps guard): this is a
    fixture *hygiene* check -- "did a ticker leak into a distractor?" -- and is
    also the naive case-sensitive matcher the M2b baseline is built from.
    """
    hits: list[tuple[int, int, int, str]] = []  # (-length, start, order, asset_id)
    for order, asset in enumerate(gazetteer.values()):
        for surface in (asset.name, *asset.aliases):
            for match in surface_re(surface).finditer(text):
                hits.append((-(match.end() - match.start()), match.start(), order, asset.id))
    found: set[str] = set()
    taken: list[tuple[int, int]] = []
    for negative_length, start, _order, asset_id in sorted(hits):
        end = start - negative_length
        # Longest match wins, exactly as FR-5 resolves overlapping surfaces, so
        # "Ethereum Classic" does not also register "Ethereum".
        if any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((start, end))
        found.add(asset_id)
    by_symbol = {asset.symbol: asset.id for asset in gazetteer.values()}
    caps = all_caps_spans(text)
    for match in _UPPER_TOKEN_RE.finditer(text):
        # FR-5 disables the plain-ticker evidence class entirely inside wire-style
        # all-caps headlines, so a bare uppercase token there is not a leak.
        if any(lo <= match.start() < hi for lo, hi in caps):
            continue
        if match.group(1) in by_symbol:
            found.add(by_symbol[match.group(1)])
    for match in re.finditer(r"\$([A-Z][A-Z0-9.]{0,9})\b", text):
        if match.group(1) in by_symbol:
            found.add(by_symbol[match.group(1)])
    return found


_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z.'-]*")
_CHUNK_RE = re.compile(r"[^\n.!?]+")


def all_caps_spans(text: str) -> list[tuple[int, int]]:
    """Char ranges of wire-style all-caps chunks (>= 70 % caps over >= 4 tokens)."""
    spans: list[tuple[int, int]] = []
    for chunk in _CHUNK_RE.finditer(text):
        tokens = [t for t in _ALPHA_TOKEN_RE.findall(chunk.group(0)) if len(t) >= 2]
        if not tokens:
            continue
        upper = sum(1 for t in tokens if t.isupper())
        if upper >= 4 and upper / len(tokens) >= 0.70:
            spans.append((chunk.start(), chunk.end()))
    return spans


# --------------------------------------------------------------------------- #
# Paraphrase families
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Frame:
    trigger: str
    title: str
    lead: str
    detail: str


def F(trigger: str, title: str, lead: str, detail: str) -> Frame:
    return Frame(trigger=trigger, title=title, lead=lead, detail=detail)


#: (event_type, family, variant) -> frames.  DEV and VAL never share a trigger
#: lexeme, and the frames differ in voice, argument order and numeric placement --
#: which is what makes the trigger-bearing 3-gram sets disjoint.
FRAMES: dict[tuple[str, str, str], tuple[Frame, ...]] = {
    ("earnings_surprise", "dev", "beat"): (
        F(
            "beat estimates",
            "{subject_plain} closes its {quarter}-quarter books in {city}",
            "{subject} beat estimates for the {quarter} quarter{pct_clause}, the company said on "
            "{weekday} in a statement issued from {city}.",
            "Management pointed to firmer pricing in two reporting segments and to a {n1} "
            "basis-point improvement in gross margin over the year-ago period.",
        ),
        F(
            "beat expectations",
            "A firmer {quarter}-quarter scorecard from {subject_plain}",
            "{subject} beat expectations for the {quarter} quarter{pct_clause}, according to the "
            "release circulated on {weekday} from {city}.",
            "The filing showed order backlog near {n1},{n2}00 units and a modest reduction in "
            "working capital against the prior period.",
        ),
    ),
    ("earnings_surprise", "dev", "miss"): (
        F(
            "missed estimates",
            "{subject_plain} reports a softer {quarter} quarter in {city}",
            "{subject} missed estimates for the {quarter} quarter{pct_clause}, the company said on "
            "{weekday} in a statement issued from {city}.",
            "The shortfall was concentrated in one reporting segment, where volumes fell about "
            "{n1} units short of the internal plan.",
        ),
        F(
            "missed expectations",
            "A weaker {quarter}-quarter scorecard from {subject_plain}",
            "{subject} missed expectations for the {quarter} quarter{pct_clause}, according to the "
            "release circulated on {weekday} from {city}.",
            "Inventory rose to roughly {n1},{n2}00 units and days-on-hand lengthened against the "
            "prior period.",
        ),
    ),
    ("earnings_surprise", "val", "beat"): (
        F(
            "topped expectations",
            "In {city}, a stronger print lands from {subject_plain}",
            "Per-share profit at {subject} topped expectations{pct_clause} once one-off items were "
            "stripped out, {analyst} of {firm} wrote after the {quarter}-quarter print.",
            "Segment disclosure put the {city} region ahead of plan, with roughly {n1},{n2}00 "
            "active accounts added over the period.",
        ),
        F(
            "above consensus",
            "A brighter reading out of {city} for {subject_plain}",
            "Quarterly profit at {subject} came in above consensus{pct_clause}, ahead of the mark "
            "{analyst} of {firm} had circulated before the {quarter}-quarter release.",
            "Cash generation near {n1},{n2}00 thousand covered the period's capital programme with "
            "room to spare.",
        ),
    ),
    ("earnings_surprise", "val", "miss"): (
        F(
            "fell short of expectations",
            "In {city}, a thinner print lands from {subject_plain}",
            "Per-share profit at {subject} fell short of expectations{pct_clause} once one-off "
            "items were stripped out, {analyst} of {firm} wrote after the {quarter}-quarter print.",
            "Segment disclosure put the {city} region behind plan, with roughly {n1},{n2}00 active "
            "accounts lost over the period.",
        ),
        F(
            "below consensus",
            "A dimmer reading out of {city} for {subject_plain}",
            "Quarterly profit at {subject} came in below consensus{pct_clause}, behind the mark "
            "{analyst} of {firm} had circulated before the {quarter}-quarter release.",
            "Cash generation near {n1},{n2}00 thousand left the period's capital programme only "
            "barely covered.",
        ),
    ),
    ("guidance_change", "dev", "raise"): (
        F(
            "raised its guidance",
            "{subject_plain} lifts the bar for the year in {city}",
            "{subject} raised its guidance for the full year on {weekday}, citing firmer demand "
            "across its {city} operations.",
            "The revised range implies roughly {n1},{n2}00 thousand of incremental operating "
            "profit against the plan published in the spring.",
        ),
    ),
    ("guidance_change", "dev", "cut"): (
        F(
            "cut its guidance",
            "{subject_plain} trims the year ahead in {city}",
            "{subject} cut its guidance for the full year on {weekday}, pointing to softer volumes "
            "across its {city} operations.",
            "The revised range implies roughly {n1},{n2}00 thousand less operating profit than the "
            "plan published in the spring.",
        ),
    ),
    ("guidance_change", "dev", "withdraw"): (
        F(
            "withdrew its guidance",
            "{subject_plain} pauses the year ahead in {city}",
            "{subject} withdrew its guidance for the full year on {weekday} while it completes a "
            "review of its {city} operations.",
            "The company said a fresh range would follow once the review is complete and declined "
            "to put a date on it, {n1} weeks after the last update.",
        ),
    ),
    ("guidance_change", "val", "raise"): (
        F(
            "lifted its outlook",
            "After a firmer run of orders, {subject_plain} moves the year",
            "With order intake running ahead of plan, {subject} lifted its outlook for the year, "
            "{analyst} at {firm} noted on {weekday}.",
            "The move follows two periods in which reported volumes tracked roughly {n1} % above "
            "the internal budget in the {city} region.",
        ),
    ),
    ("guidance_change", "val", "cut"): (
        F(
            "trimmed its outlook",
            "After a softer run of orders, {subject_plain} moves the year",
            "With order intake running behind plan, {subject} trimmed its outlook for the year, "
            "{analyst} at {firm} noted on {weekday}.",
            "The move follows two periods in which reported volumes tracked roughly {n1} % below "
            "the internal budget in the {city} region.",
        ),
    ),
    ("guidance_change", "val", "withdraw"): (
        F(
            "suspended its outlook",
            "Pending a review, {subject_plain} pauses the year",
            "Pending the outcome of an internal review, {subject} suspended its outlook for the "
            "year, {analyst} at {firm} noted on {weekday}.",
            "No replacement range was offered, and the {city} team declined to say when one would "
            "follow after {n1} weeks of work.",
        ),
    ),
    ("mna", "dev", "confirmed"): (
        F(
            "agreed to acquire",
            "{acquirer_plain} and {target_plain}: the {city} transaction in outline",
            "{acquirer} agreed to acquire {target}{deal_clause}, the two boards said on {weekday}.",
            "Completion is targeted for the fourth quarter and is subject to the customary "
            "antitrust review in the {city} jurisdiction, which usually runs {n1} months.",
        ),
    ),
    ("mna", "val", "confirmed"): (
        F(
            "to be acquired by",
            "Terms filed in {city} cover the {target_plain} transaction",
            "{target} is to be acquired by {acquirer}{deal_clause}, the two boards said on "
            "{weekday}.",
            "The filing sets a break fee and puts completion in the fourth quarter, subject to the "
            "customary review in the {city} jurisdiction over about {n1} months.",
        ),
        F(
            "tender offer",
            "A cash route opens in {city} toward {target_plain}",
            "{acquirer} launched a tender offer for {target}{deal_clause}, according to the filing "
            "lodged on {weekday}.",
            "The offer runs for twenty business days and carries a minimum condition of {n1} % of "
            "the outstanding shares.",
        ),
    ),
    ("mna", "val", "rumored"): (
        F(
            "will acquire",
            "An approach is described in {city} around {target_plain}",
            "Sources say {acquirer} will acquire {target}{deal_clause}, though neither side has "
            "confirmed the discussions.",
            "No filing has been made and the {city} press office declined to comment when reached "
            "on {weekday}, {n1} hours after the item first ran.",
        ),
    ),
    ("regulatory_action", "dev", "adverse"): (
        F(
            "sued",
            "A courtroom step in {city} touching {subject_plain}",
            "The {agency} sued {subject} over disclosures tied to its {city} unit{amount_clause}.",
            "The complaint runs to {n1} pages and names two former officers alongside the company "
            "itself.",
        ),
        F(
            "fined",
            "A penalty notice in {city} touching {subject_plain}",
            "The {agency} fined {subject} over record-keeping failures at its {city} "
            "unit{amount_clause}.",
            "The order sets a {n1}-day remediation window and requires an independent consultant "
            "to review controls.",
        ),
    ),
    ("regulatory_action", "dev", "favorable"): (
        F(
            "approved",
            "A clearance step in {city} touching {subject_plain}",
            "The {agency} approved the {city} application filed by {subject}, closing a review "
            "that had run for two quarters.",
            "The decision covers {n1} of the {n2} products in the original submission and carries "
            "standard reporting conditions.",
        ),
    ),
    ("regulatory_action", "val", "adverse"): (
        F(
            "opened an investigation into",
            "A file is opened in {city} on {subject_plain}",
            "Officials at the {agency} opened an investigation into {subject} after complaints "
            "from {city} customers{amount_clause}.",
            "Investigators have asked for records covering {n1} months and have not set a deadline "
            "for their findings.",
        ),
        F(
            "ordered to pay",
            "A settlement figure lands in {city} for {subject_plain}",
            "Following a review of its {city} operations, {subject} was ordered to pay a sum set "
            "by the {agency}{amount_clause}.",
            "The order also installs a monitor for {n1} months and bars two practices the review "
            "had flagged.",
        ),
    ),
    ("regulatory_action", "val", "favorable"): (
        F(
            "closed its investigation into",
            "A file is shut in {city} on {subject_plain}",
            "Officials at the {agency} closed its investigation into {subject} without enforcement "
            "action, ending a review opened in the {city} district last year.",
            "The notice runs to {n1} pages and records no findings against the company or its "
            "officers.",
        ),
        F(
            "granted approval to",
            "A clearance lands in {city} for {subject_plain}",
            "Officials at the {agency} granted approval to {subject} for the {city} programme, "
            "ending a review that had run for two quarters.",
            "The clearance covers {n1} of the {n2} items in the original submission and carries "
            "standard reporting conditions.",
        ),
    ),
    ("listing", "dev", "confirmed"): (
        F(
            "will list",
            "A new pair opens in the {city} session",
            "{venue} will list {subject} for spot trading from {weekday}, the venue told users in "
            "a notice.",
            "Order books open in stages, with post-only mode running for the first {n1} minutes of "
            "the {city} session.",
        ),
        F(
            "listed on",
            "A fresh venue for {subject_plain} from the {city} desk",
            "{subject} will be listed on {venue} beginning {weekday}, according to the notice the "
            "venue published.",
            "Deposits open {n1} hours ahead of the first auction in {city}, and withdrawals follow "
            "a day later.",
        ),
    ),
    ("listing", "val", "confirmed"): (
        F(
            "is now available on",
            "Spot access widens for {subject_plain} out of {city}",
            "{subject} is now available on {venue} for spot trading, the venue told customers in a "
            "notice posted on {weekday}.",
            "Access is staged by region, and the venue said roughly {n1},{n2}00 accounts were "
            "queued for the first {city} session.",
        ),
        F(
            "adds support for",
            "A venue widens its book in {city}",
            "From {weekday}, {venue} adds support for {subject} across its retail and "
            "institutional books.",
            "The venue said roughly {n1},{n2}00 accounts had pre-registered for access in the "
            "{city} region.",
        ),
    ),
    ("listing", "val", "index"): (
        F(
            "will join the S&P 500",
            "An index seat opens in {city} for {subject_plain}",
            "{subject} will join the S&P 500 at the next scheduled rebalance, the index provider "
            "said on {weekday}.",
            "Funds tracking the benchmark will need to source roughly {n1},{n2}00 thousand shares "
            "over the reconstitution window.",
        ),
    ),
    ("delisting", "dev", "confirmed"): (
        F(
            "will delist",
            "A pair closes in the {city} session",
            "{venue} will delist {subject} from {weekday}, citing thin liquidity in the pair.",
            "Open orders are cancelled at the {city} close and balances remain withdrawable for "
            "{n1} days afterwards.",
        ),
        F(
            "delisted",
            "A venue closes its book on {subject_plain} in {city}",
            "{subject} will be delisted from {venue} at the end of the month, according to the "
            "notice the venue published.",
            "Trading stops at the {city} close and balances remain withdrawable for {n1} days "
            "afterwards.",
        ),
    ),
    ("delisting", "val", "confirmed"): (
        F(
            "will remove support for",
            "Access narrows for {subject_plain} out of {city}",
            "From {weekday}, {venue} will remove support for {subject} across its retail and "
            "institutional books.",
            "The venue said roughly {n1},{n2}00 accounts hold a balance in the {city} region and "
            "have thirty days to move it.",
        ),
        F(
            "suspend trading in",
            "A venue halts its book in {city}",
            "{venue} will suspend trading in {subject} from {weekday}, pending a review of market "
            "quality.",
            "Withdrawals stay open throughout, and the venue said a decision would follow within "
            "{n1} days in the {city} region.",
        ),
    ),
    ("delisting", "val", "index"): (
        F(
            "removed from the S&P 500",
            "An index seat closes in {city} for {subject_plain}",
            "{subject} will be removed from the S&P 500 at the next scheduled rebalance, the index "
            "provider said on {weekday}.",
            "Funds tracking the benchmark will need to place roughly {n1},{n2}00 thousand shares "
            "over the reconstitution window.",
        ),
    ),
    ("hack_exploit", "dev", "confirmed"): (
        F(
            "was exploited",
            "An incident report lands in {city} on {subject_plain}",
            "The {subject} {vector_word} was exploited{amount_clause} in the early hours of "
            "{weekday}, researchers at {firm} said.",
            "On-chain traces put the outflow across {n1} addresses, with the first transfer "
            "clearing shortly after midnight in {city}.",
        ),
        F(
            "drained",
            "An outflow is traced in {city} from {subject_plain}",
            "An attacker drained the {subject} {vector_word}{amount_clause} on {weekday}, "
            "researchers at {firm} said.",
            "The contract had been audited twice and held roughly {n1},{n2}00 thousand in deposits "
            "before the incident in {city}.",
        ),
    ),
    ("hack_exploit", "val", "confirmed"): (
        F(
            "suffered a hack",
            "{subject_plain} publishes a first account out of {city}",
            "Overnight on {weekday}, {subject} suffered a hack that reached a {vector_word} and "
            "cost holders{amount_clause}, according to {firm}.",
            "A first account published by the team put the affected balances across {n1} addresses "
            "and promised a fuller write-up from {city}.",
        ),
        F(
            "attackers stole",
            "A first account of the {city} incident at {subject_plain}",
            "Attackers stole{amount_clause} from {subject} using a {vector_word} sequence early on "
            "{weekday}, according to {firm}.",
            "The sequence ran across {n1} blocks and was closed out before the {city} team could "
            "pause the market.",
        ),
    ),
    ("hack_exploit", "val", "equity"): (
        F(
            "security breach",
            "An overnight incident is logged in {city} at {subject_plain}",
            "A security breach at {subject} exposed customer records through a {vector_word} "
            "campaign detected on {weekday}{amount_clause}, according to {firm}.",
            "The company said {n1} of its {n2} regional systems were isolated within the hour and "
            "that {city} operations stayed open.",
        ),
    ),
}

DISTRACTORS: dict[str, tuple[str, ...]] = {
    "dev": (
        "Trading volume in the name ran {n1} % above its {n2}-day average during the {city} session.",
        "{analyst}, who covers the sector at {firm}, described the reaction as consistent with the "
        "last several reporting periods.",
        "The company employs roughly {n1},{n2}00 people and files its next periodic report in the "
        "autumn from {city}.",
        "A note from {firm} put realised volatility over the trailing month near {n1} % against a "
        "{n2} % long-run average.",
        "Desks in {city} reported two-way interest through the afternoon without a clear "
        "directional bias, {analyst} said.",
        "{analyst} of {firm} said the {city} desk had fielded {n1} client calls on the story before "
        "lunch.",
        "The disclosure runs to {n1} pages and repeats language used in the previous periodic "
        "filing lodged from {city}.",
        "Implied volatility for the front month settled near {n1} %, roughly {n2} points above its "
        "trailing average, {firm} said.",
        "A summary circulated by {firm} counted {n1},{n2}00 open positions across listed venues in "
        "{city}.",
        "The {city} office said staffing in the affected unit had been steady for {n1} quarters, "
        "{analyst} reported.",
        "Turnover in the pair reached about {n1},{n2}00 thousand over the twenty-four hours that "
        "followed in {city}.",
        "{analyst} at {firm} noted the read-across to peers looked limited on the numbers "
        "disclosed so far in {city}.",
    ),
    "val": (
        "In {city}, desks logged {n1} % more two-way interest than a typical morning, according to "
        "{firm}.",
        "A memo circulated by {firm} put trailing {n2}-day realised volatility for the name near "
        "{n1} % in {city}.",
        "Roughly {n1},{n2}00 retail accounts touched the name during the twenty-four hours that "
        "followed, {analyst} wrote from {city}.",
        "Staff in the {city} office referred questions to an external line and offered no further "
        "detail, {analyst} of {firm} reported.",
        "{analyst} put the read-across to comparable names at fewer than {n1} basis points on the "
        "numbers disclosed in {city}.",
        "Order books thinned into the {city} afternoon before recovering roughly {n1} % of the "
        "displayed depth, {firm} said.",
        "The filing appendix lists {n1} counterparties and {n2} jurisdictions touched by the "
        "arrangement, {analyst} noted.",
        "A same-day summary from {firm} counted {n1},{n2}00 messages across public channels on the "
        "topic in {city}.",
        "Front-month implied volatility printed near {n1} %, some {n2} points wide of its trailing "
        "median, {firm} wrote.",
        "The {city} team has published quarterly updates on this programme for {n1} consecutive "
        "periods, {analyst} said.",
        "Custody balances tracked by {firm} moved by roughly {n1},{n2}00 thousand over the {city} "
        "session.",
        "{analyst} said client questions had focused on timing rather than on the size of the "
        "number reported from {city}.",
    ),
}

TAILS: tuple[str, ...] = (
    "Coverage of the story continued into the following session.",
    "A longer write-up was promised for later in the week.",
    "The wire carried an updated version shortly afterwards.",
    "Editors added background from earlier reporting to the item.",
    "The bulletin was distributed to subscribers in two regions.",
    "A shorter summary ran on the evening list.",
    "The desk flagged the item for follow-up in the morning file.",
    "An earlier version of this item carried a different headline.",
)


# --------------------------------------------------------------------------- #
# Deterministic value streams (no RNG object: every draw is a pure function of
# the event index, so re-ordering the plan cannot silently reshuffle the corpus)
# --------------------------------------------------------------------------- #


def spread(index: int, salt: int, modulus: int) -> int:
    """A decorrelated but fully deterministic index into a pool.

    A linear stride collides on a fixed period (every 12th article drew the same
    analyst, city and distractor pair), which pushed unrelated articles over the
    committed 0.45 inter-cluster Jaccard ceiling.  Hashing removes the period
    without introducing an RNG stream whose draws depend on call order.
    """
    digest = hashlib.sha256(f"{SEED}|{salt}|{index}".encode()).hexdigest()
    return int(digest[:8], 16) % modulus


def pick(pool: tuple[str, ...], index: int, salt: int) -> str:
    return pool[spread(index, salt, len(pool))]


def flavor(index: int) -> dict[str, str]:
    return {
        "firm": pick(FIRMS, index, 1),
        "city": pick(CITIES, index, 5),
        "analyst": pick(ANALYSTS, index, 9),
        "weekday": pick(WEEKDAYS, index, 2),
        "quarter": pick(QUARTERS, index, 3),
        "agency": pick(AGENCIES, index, 4),
        "n1": str(11 + spread(index, 11, 79)),
        "n2": str(1 + spread(index, 12, 9)),
    }


# --------------------------------------------------------------------------- #
# Truth records
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class TruthEvent:
    key: str
    event_type: str
    family: str
    stage: str
    attributes: dict[str, Any]
    links: list[dict[str, str]]
    article_external_ids: list[str] = field(default_factory=list)
    event_date: str = ""
    observed_at: str = ""
    tier_class: str = "t2"
    single_source_t3: bool = False
    origin: str = "generated"


@dataclass(slots=True)
class Article:
    external_id: str
    source_domain: str
    published_at: str
    title: str
    body: str
    family: str
    arrival_batch: int = 0

    def row(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "url": f"https://{self.source_domain}/{self.external_id}",
            "source_domain": self.source_domain,
            "published_at": self.published_at,
            "fetched_at": self.published_at,
            "title": self.title,
            "body": self.body,
            "family": self.family,
        }

    def analysis_text(self) -> str:
        return f"{normalize(self.title)}\n{normalize(self.body)}"


# --------------------------------------------------------------------------- #
# Asset allocation
# --------------------------------------------------------------------------- #

#: Assets reserved for the hand-authored adversarial articles, so no generated
#: event ever plants an effect on a trap asset.
RESERVED = {
    "eq:ADBE", "eq:OKTA", "eq:CRM", "eq:DDOG", "eq:PANW", "eq:ZS", "eq:UAL", "eq:DAL",
    "eq:MAR", "eq:HLT", "eq:LOW", "eq:TJX", "eq:EA", "eq:TTWO", "eq:SNOW", "eq:NET",
    "eq:LYFT", "eq:UBER", "eq:GS", "eq:SCHW", "eq:ARM", "eq:LCID", "eq:META", "eq:ORCL",
    "eq:SHEL", "eq:V", "eq:AAPL", "eq:COIN", "eq:NDAQ", "eq:ICE", "eq:ALL", "eq:KEY",
    "cx:SUI", "cx:SEI", "cx:NEAR", "cx:APE", "cx:ONE", "cx:ICP", "cx:OP", "cx:ARB",
}


def venue_colliding(gazetteer: dict[str, GazAsset]) -> set[str]:
    """Assets whose surface contains a venue-lexicon surface (e.g. "Binance Coin").

    FR-5 scans the venue lexicon first and *consumes* its spans, so such an asset
    can never be resolved as a listing subject.  Keeping them out of the generated
    pools means every venue-vs-subject decision in the fixture is a decision the
    engine is actually allowed to get right; the deliberate venue/asset collisions
    (Coinbase, Nasdaq, NYSE) live in the hand-authored set instead.
    """
    raw = json.loads((DATA_DIR / "patterns.json").read_text(encoding="utf-8"))
    venues = list(raw["venue_lexicon"])
    blocked: set[str] = set()
    for asset in gazetteer.values():
        for surface in (asset.name, asset.symbol, *asset.aliases):
            if any(phrase_re(venue).search(surface) for venue in venues):
                blocked.add(asset.id)
                break
    return blocked


class AssetAllocator:
    """Hands out a distinct gazetteer asset per signal-bearing slot.

    One asset per event keeps the planted market effects separable: two events on
    the same asset inside the 26-day corpus window would overlap in the bars the
    backtest reads, and the truth table could no longer say what the correct sign
    was.
    """

    def __init__(self, gazetteer: dict[str, GazAsset]) -> None:
        blocked = RESERVED | venue_colliding(gazetteer)
        self._pools = {
            "equity": [
                a.id
                for a in gazetteer.values()
                if a.kind == "equity" and not a.ambiguous and a.id not in blocked
            ],
            "crypto": [
                a.id
                for a in gazetteer.values()
                if a.kind == "crypto" and not a.ambiguous and a.id not in blocked
            ],
        }
        for pool in self._pools.values():
            pool.sort()
        self._cursor = {"equity": 0, "crypto": 0}

    def take(self, kind: str) -> str:
        pool = self._pools[kind]
        index = self._cursor[kind]
        if index >= len(pool):
            raise RuntimeError(f"fixture asset pool exhausted for {kind}")
        self._cursor[kind] += 1
        return pool[index]


# --------------------------------------------------------------------------- #
# Generated corpus
# --------------------------------------------------------------------------- #

PROFILE_SPEC: dict[str, dict[str, Any]] = {
    "t1x3": {"tier": "t1", "articles": 3, "link": "strong", "attrs": "full"},
    "t2x2": {"tier": "t2", "articles": 2, "link": "ticker", "attrs": "full"},
    "t1x1": {"tier": "t1", "articles": 1, "link": "strong", "attrs": "full"},
    "t2x1": {"tier": "t2", "articles": 1, "link": "ticker", "attrs": "partial"},
    "t3x1": {"tier": "t3", "articles": 1, "link": "alias", "attrs": "partial"},
}

#: 90 generated events: 27 x t1x3 + 9 x t2x2 (multi-source) + 18 x t1x1 +
#: 18 x t2x1 + 18 x t3x1 (single-source).  The mix is chosen so the three
#: fixed-edge confidence buckets EVALS.md's M6 uses each hold well over the
#: 20-signal occupancy floor.  The t3x1 events are the "low-tier sources report
#: things that do not pan out" population the planted-effect table gives a 50 %
#: chance of no effect.
PROFILE_SEQUENCE: list[str] = (
    ["t1x3", "t2x1", "t1x1", "t3x1", "t1x3", "t2x2", "t2x1", "t3x1", "t1x3", "t1x1"] * 9
)[:90]

#: (family, variant, count) blocks per event type, in generation order.
GENERATED_PLAN: dict[str, list[tuple[str, str, int]]] = {
    "earnings_surprise": [
        ("dev", "beat", 4), ("dev", "miss", 2), ("val", "beat", 6), ("val", "miss", 5),
    ],
    "guidance_change": [
        ("dev", "raise", 3), ("dev", "cut", 1), ("dev", "withdraw", 1),
        ("val", "raise", 4), ("val", "cut", 4), ("val", "withdraw", 1),
    ],
    "mna": [("dev", "confirmed", 2), ("val", "confirmed", 5), ("val", "rumored", 3)],
    "regulatory_action": [
        ("dev", "adverse", 3), ("dev", "favorable", 2),
        ("val", "adverse", 5), ("val", "favorable", 5),
    ],
    "listing": [("dev", "confirmed", 5), ("val", "confirmed", 2), ("val", "index", 3)],
    "delisting": [("dev", "confirmed", 4), ("val", "confirmed", 5), ("val", "index", 2)],
    "hack_exploit": [("dev", "confirmed", 5), ("val", "confirmed", 6), ("val", "equity", 2)],
}

#: asset kind per generated event of each type, in generation order.
KIND_PLAN: dict[str, list[str]] = {
    "earnings_surprise": ["equity"] * 17,
    "guidance_change": ["equity"] * 14,
    "mna": ["equity"] * 10,
    "regulatory_action": (
        ["equity", "crypto", "equity", "equity", "crypto"]
        + ["crypto", "equity", "crypto", "equity", "crypto"]
        + ["equity", "crypto", "equity", "crypto", "equity"]
    ),
    "listing": ["crypto"] * 7 + ["equity"] * 3,
    "delisting": ["crypto"] * 9 + ["equity"] * 2,
    "hack_exploit": ["crypto"] * 11 + ["equity"] * 2,
}

NON_ASSET_VENUES = ("Binance", "Kraken", "OKX", "Bybit", "Bitstamp", "Gemini")


def asset_surface(asset: GazAsset, style: str) -> tuple[str, str]:
    """(surface written into the trigger sentence, plain surface for headlines)."""
    plain = asset.aliases[0] if asset.aliases else asset.name
    if style == "strong":
        return f"{asset.name} ({asset.symbol})", plain
    if style == "ticker":
        return asset.symbol, plain
    return plain, plain


REG_AMOUNT_TRIGGERS = frozenset({"sued", "fined", "opened an investigation into", "ordered to pay"})

VECTOR_BY_TRIGGER = {
    "was exploited": "bridge",
    "drained": "smart contract",
    "suffered a hack": "hot wallet",
    "attackers stole": "flash loan",
    "security breach": "phishing",
}


def clauses(
    event_type: str, attrs: str, index: int, trigger: str
) -> tuple[dict[str, str], dict[str, Any]]:
    """Slot text plus the truth attributes it plants."""
    pct = f"{2 + (index * 3) % 9}.{(index * 7) % 10}"
    amount_value = (1 + (index % 9)) * 100_000_000 + (index % 7) * 10_000_000
    amount_text = f"${amount_value / 1_000_000_000:.2f} billion"
    amount_truth = round(float(f"{amount_value / 1_000_000_000:.2f}") * 1e9, 2)
    premium = 20 + index % 15
    slots = {"pct_clause": "", "deal_clause": "", "amount_clause": "", "vector_word": ""}
    truth: dict[str, Any] = {}

    if event_type == "earnings_surprise":
        if attrs == "full":
            slots["pct_clause"] = f" by {pct} %"
            truth["surprise_pct"] = float(pct)
    elif event_type == "mna":
        slots["deal_clause"] = f" in a transaction valued at {amount_text}"
        truth["deal_value_usd"] = amount_truth
        if attrs == "full":
            slots["deal_clause"] += f", a {premium} % premium to the previous close"
            truth["premium_pct"] = float(premium)
    elif event_type == "regulatory_action":
        # Only the adverse frames carry a monetary slot; a favorable clearance has
        # no penalty to state, so no `amount_usd` is planted for those triggers.
        if attrs == "full" and trigger in REG_AMOUNT_TRIGGERS:
            slots["amount_clause"] = f", with {amount_text} at stake"
            truth["amount_usd"] = amount_truth
    elif event_type == "hack_exploit":
        # Hack frames always carry both declared attributes: the alternative wording
        # for a missing amount reads as broken English in three of the five frames.
        vector = VECTOR_BY_TRIGGER[trigger]
        slots["vector_word"] = vector
        truth["vector"] = vector
        slots["amount_clause"] = f" {amount_text}"
        truth["amount_usd"] = amount_truth
    return slots, truth


def build_generated(
    gazetteer: dict[str, GazAsset], allocator: AssetAllocator
) -> tuple[list[Article], list[TruthEvent]]:
    articles: list[Article] = []
    events: list[TruthEvent] = []
    index = 0
    day_cursor = 0

    for event_type in (
        "earnings_surprise",
        "guidance_change",
        "mna",
        "regulatory_action",
        "listing",
        "delisting",
        "hack_exploit",
    ):
        kinds = KIND_PLAN[event_type]
        within_type = 0
        for family, variant, count in GENERATED_PLAN[event_type]:
            frames = FRAMES[(event_type, family, variant)]
            for _ in range(count):
                kind = kinds[within_type]
                frame = frames[within_type % len(frames)]
                profile = PROFILE_SPEC[PROFILE_SEQUENCE[index]]
                day = FIRST_EVENT_DAY + (day_cursor * 7 + index // 13) % (
                    LAST_EVENT_DAY - FIRST_EVENT_DAY + 1
                )
                day_cursor += 1
                events.append(
                    _emit_generated_event(
                        articles=articles,
                        gazetteer=gazetteer,
                        allocator=allocator,
                        event_type=event_type,
                        family=family,
                        variant=variant,
                        frame=frame,
                        profile=profile,
                        kind=kind,
                        index=index,
                        day=day,
                    )
                )
                index += 1
                within_type += 1
    return articles, events


def _emit_generated_event(
    *,
    articles: list[Article],
    gazetteer: dict[str, GazAsset],
    allocator: AssetAllocator,
    event_type: str,
    family: str,
    variant: str,
    frame: Frame,
    profile: dict[str, Any],
    kind: str,
    index: int,
    day: int,
) -> TruthEvent:
    slots = flavor(index)
    clause_slots, truth_attrs = clauses(event_type, profile["attrs"], index, frame.trigger)
    slots.update(clause_slots)

    links: list[dict[str, str]] = []
    if event_type == "mna":
        acquirer = gazetteer[allocator.take(kind)]
        target = gazetteer[allocator.take(kind)]
        a_surface, a_plain = asset_surface(acquirer, profile["link"])
        t_surface, t_plain = asset_surface(target, profile["link"])
        slots.update(
            acquirer=a_surface, acquirer_plain=a_plain, target=t_surface, target_plain=t_plain
        )
        links = [
            {"asset_id": acquirer.id, "role": "acquirer"},
            {"asset_id": target.id, "role": "target"},
        ]
        stage = "rumored" if variant == "rumored" else "confirmed"
    else:
        subject = gazetteer[allocator.take(kind)]
        s_surface, s_plain = asset_surface(subject, profile["link"])
        slots.update(subject=s_surface, subject_plain=s_plain)
        links = [{"asset_id": subject.id, "role": "subject"}]
        stage = "confirmed"

    if event_type in ("listing", "delisting"):
        if variant == "index":
            truth_attrs["venue"] = "S&P 500"
        else:
            venue = NON_ASSET_VENUES[index % len(NON_ASSET_VENUES)]
            slots["venue"] = venue
            truth_attrs["venue"] = venue

    if event_type == "earnings_surprise":
        truth_attrs["polarity"] = variant
    elif event_type == "guidance_change":
        truth_attrs["polarity"] = variant
    elif event_type == "regulatory_action":
        truth_attrs["polarity"] = "adverse" if variant == "adverse" else "favorable"
        truth_attrs["agency"] = slots["agency"]

    title = frame.title.format_map(slots)
    lead = frame.lead.format_map(slots)
    detail = frame.detail.format_map(slots)
    pool = DISTRACTORS[family]
    d1 = pool[spread(index, 21, len(pool))].format_map(slots)
    d2 = pool[(spread(index, 22, len(pool) - 1) + spread(index, 21, len(pool)) + 1) % len(pool)].format_map(slots)
    head = f"{lead} {detail} {d1} {d2}"
    while len(head) < 430:
        head = f"{head} {pool[spread(index, len(head), len(pool))].format_map(slots)}"

    tier = profile["tier"]
    domains = DOMAINS_BY_TIER[tier]
    n_articles = profile["articles"]
    key = f"ev-{event_type[:4]}-{index:03d}"
    external_ids: list[str] = []
    hours = (9, 13, 19)
    for member in range(n_articles):
        domain = domains[(index * 3 + member * 5) % len(domains)]
        member_head = head
        if member:
            # One substituted weekday keeps intra-cluster Jaccard near 0.87 --
            # comfortably above the committed 0.72 margin -- while the diverging
            # tail past 400 characters gives every member a distinct content hash.
            member_head = head.replace(slots["weekday"], WEEKDAYS[(index + member) % 5], 1)
        tail = " ".join(
            TAILS[(index + member * 3 + k) % len(TAILS)] for k in range(1 + member)
        )
        external_id = f"{key}-{member}"
        external_ids.append(external_id)
        articles.append(
            Article(
                external_id=external_id,
                source_domain=domain,
                published_at=stamp(day, hours[member % 3], (index * 7) % 60),
                title=title,
                body=f"{member_head} {tail}",
                family=family,
            )
        )

    members = [a for a in articles if a.external_id in external_ids]
    return TruthEvent(
        key=key,
        event_type=event_type,
        family=family,
        stage=stage,
        attributes=truth_attrs,
        links=links,
        article_external_ids=external_ids,
        event_date=min(a.published_at for a in members)[:10],
        observed_at=max(a.published_at for a in members),
        tier_class=tier,
        single_source_t3=tier == "t3",
    )


# --------------------------------------------------------------------------- #
# Hand-authored adversarial corpus (40 articles, all VAL)
# --------------------------------------------------------------------------- #
#
# 18 carry a truth event, 22 are no-event traps.  Every mention whose surface form
# collides with a gazetteer alias is annotated with the asset a naive
# case-insensitive matcher would link (`collides_with`) and the asset the system
# *should* link (`asset_id`, null for a no-link trap).


def hand(
    key: str,
    domain: str,
    day: int,
    hour: int,
    title: str,
    body: str,
    *,
    event: dict[str, Any] | None = None,
    traps: tuple[tuple[str, int, str | None, str, str], ...] = (),
) -> dict[str, Any]:
    """(surface, occurrence, asset_id-or-None, collides_with, category) per trap."""
    return {
        "key": key,
        "domain": domain,
        "day": day,
        "hour": hour,
        "title": title,
        "body": body,
        "event": event,
        "traps": traps,
    }


HAND_ARTICLES: tuple[dict[str, Any], ...] = (
    # -- 18 event-bearing ------------------------------------------------- #
    hand(
        "h-mna-denied-1", "reuters.example", 101, 9,
        "Adobe pushes back on a weekend report from Ashford",
        "Adobe denied reports that it is in talks to acquire Okta, calling the story inaccurate in "
        "a statement issued on Sunday. Adobe Inc. (ADBE) said no approach had been made and that "
        "it would not comment further on market speculation. Desks in Ashford had moved both names "
        "on Friday afternoon after the original item circulated. Kestrel Analytics said the denial "
        "was unusually specific, and noted that no filing had followed it.",
        event={
            "event_type": "mna", "stage": "denied", "attributes": {},
            "links": [
                {"asset_id": "eq:ADBE", "role": "acquirer"},
                {"asset_id": "eq:OKTA", "role": "target"},
            ],
        },
        traps=(("(ADBE)", 0, "eq:ADBE", "eq:ADBE", "paren_ticker"),),
    ),
    hand(
        "h-mna-denied-2", "bloomberg.example", 103, 10,
        "Salesforce rejects a weekend report on a monitoring vendor",
        "Salesforce denied reports that it is in talks to acquire Datadog, describing the weekend "
        "story as inaccurate. Salesforce, Inc. (CRM) said it had made no approach and would not "
        "comment on speculation. Trading desks in Marlow had marked both names higher on Friday "
        "afternoon. Harborline Research said the wording left little room for a later reversal.",
        event={
            "event_type": "mna", "stage": "denied", "attributes": {},
            "links": [
                {"asset_id": "eq:CRM", "role": "acquirer"},
                {"asset_id": "eq:DDOG", "role": "target"},
            ],
        },
        traps=(("(CRM)", 0, "eq:CRM", "eq:CRM", "paren_ticker"),),
    ),
    hand(
        "h-mna-denied-3", "ft.example", 106, 11,
        "Palo Alto Networks disputes a weekend item out of Kentbridge",
        "Palo Alto Networks denied reports that it is in talks to acquire Zscaler, saying no "
        "approach had been made. The company added that it would not comment further on "
        "speculation of this kind. Desks in Kentbridge had marked both names on Friday afternoon "
        "before the statement landed. Northgate Partners said the wording was unusually direct for "
        "a company of that size.",
        event={
            "event_type": "mna", "stage": "denied", "attributes": {},
            "links": [
                {"asset_id": "eq:PANW", "role": "acquirer"},
                {"asset_id": "eq:ZS", "role": "target"},
            ],
        },
    ),
    hand(
        "h-mna-sym-1", "wsj.example", 108, 9,
        "A combination in the air over Wexley",
        "A merger between United Airlines and Delta Air Lines would face a long antitrust review, "
        "people familiar with the matter said on Tuesday. Neither carrier has filed anything and "
        "both declined to comment when reached in Wexley. Vellum Capital said the regulatory path "
        "alone would run for years. Two of the people cautioned that no bankers had been formally "
        "mandated for the work.",
        event={
            "event_type": "mna", "stage": "rumored", "attributes": {},
            "links": [
                {"asset_id": "eq:UAL", "role": "mentioned"},
                {"asset_id": "eq:DAL", "role": "mentioned"},
            ],
            "symmetric": True,
        },
    ),
    hand(
        "h-mna-sym-2", "ap.example", 110, 12,
        "Two hotel groups outline a joint structure in Dunmoor",
        "Marriott and Hilton announced a merger of equals that would create the largest operator "
        "in the segment, the two companies said on Wednesday. The structure gives each side an "
        "equal number of board seats and no cash changes hands. Brightmoor Advisors said the "
        "antitrust review would be the gating item in Dunmoor. Both companies expect to file the "
        "transaction documents within a month.",
        event={
            "event_type": "mna", "stage": "confirmed", "attributes": {},
            "links": [
                {"asset_id": "eq:MAR", "role": "mentioned"},
                {"asset_id": "eq:HLT", "role": "mentioned"},
            ],
            "symmetric": True,
        },
    ),
    hand(
        "h-mna-sym-3", "cnbc.example", 112, 14,
        "Two studios confirm documentation-stage discussions in Redhaven",
        "Electronic Arts-Take-Two Interactive merger talks have reached the documentation stage, "
        "the two companies confirmed on Wednesday. Neither side has set a price and both cautioned "
        "that no agreement is certain. Ashgrove Securities said the overlap in live-service titles "
        "would draw the closest scrutiny in Redhaven. The companies will not comment further until "
        "documents are filed.",
        event={
            "event_type": "mna", "stage": "confirmed", "attributes": {},
            "links": [
                {"asset_id": "eq:EA", "role": "mentioned"},
                {"asset_id": "eq:TTWO", "role": "mentioned"},
            ],
            "symmetric": True,
        },
    ),
    hand(
        "h-mna-sym-4", "dowjones.example", 114, 10,
        "A retail pairing is described out of Thornbury",
        "Lowe's is reportedly exploring a combination with TJX, according to two people briefed on "
        "the discussions. Neither retailer has filed anything and both declined to comment when "
        "reached in Thornbury. Pellmore Group said the store overlap looked manageable at first "
        "read. No bankers have been formally mandated, one of the people said.",
        event={
            "event_type": "mna", "stage": "rumored", "attributes": {},
            "links": [
                {"asset_id": "eq:LOW", "role": "mentioned"},
                {"asset_id": "eq:TJX", "role": "mentioned"},
            ],
            "symmetric": True,
        },
    ),
    hand(
        "h-mna-rumor-1", "bloomberg.example", 116, 8,
        "An approach is described in Larkfield around a network vendor",
        "Snowflake is in talks to acquire Cloudflare in what would be its largest transaction, "
        "three people familiar with the matter said. No filing has been made and neither company "
        "would comment when reached in Larkfield. Dunmoor Research said the price discussed sat "
        "well above the last funding mark. The people cautioned that the discussions could still "
        "collapse before terms are agreed.",
        event={
            "event_type": "mna", "stage": "rumored", "attributes": {},
            "links": [
                {"asset_id": "eq:SNOW", "role": "acquirer"},
                {"asset_id": "eq:NET", "role": "target"},
            ],
        },
    ),
    hand(
        "h-mna-passive-1", "businesswire.example", 118, 12,
        "Terms are filed in Brightmoor for a ride-hailing transaction",
        "Lyft is to be acquired by Uber for $9.40 billion in cash and stock, the two boards said "
        "on Monday. The filing sets a break fee and puts completion in the fourth quarter. "
        "Larkfield Associates said the antitrust review in Brightmoor would be the gating item. "
        "Both companies said integration planning would begin immediately after the vote.",
        event={
            "event_type": "mna", "stage": "confirmed",
            "attributes": {"deal_value_usd": 9400000000.0},
            "links": [
                {"asset_id": "eq:UBER", "role": "acquirer"},
                {"asset_id": "eq:LYFT", "role": "target"},
            ],
        },
    ),
    hand(
        "h-mna-bid-1", "globenewswire.example", 120, 9,
        "A cash route opens in Ostend Bay toward a brokerage",
        "Goldman Sachs launched a takeover bid for Charles Schwab at a 22 % premium to the "
        "previous close, according to the filing. The offer runs for twenty business days and "
        "carries a minimum tender condition. Estridge Analytics said the funding package looked "
        "fully committed in Ostend Bay. Both boards said they would respond formally within ten "
        "days of receipt.",
        event={
            "event_type": "mna", "stage": "confirmed",
            "attributes": {"premium_pct": 22.0},
            "links": [
                {"asset_id": "eq:GS", "role": "acquirer"},
                {"asset_id": "eq:SCHW", "role": "target"},
            ],
        },
    ),
    hand(
        "h-list-venue-1", "theblock.example", 102, 13,
        "A new spot pair opens for a layer-1 token in Ashford",
        "Sui is now available on Coinbase for spot trading, the exchange told users on Thursday. "
        "Access is staged by region and post-only mode runs for the first thirty minutes. Wexley "
        "Quantitative said depth in the pair looked thin at the open in Ashford. The exchange said "
        "withdrawals would follow a day later for all regions.",
        event={
            "event_type": "listing", "stage": "confirmed",
            "attributes": {"venue": "Coinbase"},
            "links": [
                {"asset_id": "cx:SUI", "role": "subject"},
                {"asset_id": "eq:COIN", "role": "venue"},
            ],
            "venue_decision": True,
        },
    ),
    hand(
        "h-list-venue-2", "coindesk.example", 105, 15,
        "Another spot pair opens in Marlow",
        "Sei is now available on Coinbase for spot trading from Friday, the exchange said in a "
        "notice. Shares of $COIN were little changed in the session that followed. Marlowe Bennett "
        "said listing notices no longer move the venue's own stock much in Marlow. The exchange "
        "said deposits had opened a day earlier for eligible accounts.",
        event={
            "event_type": "listing", "stage": "confirmed",
            "attributes": {"venue": "Coinbase"},
            "links": [
                {"asset_id": "cx:SEI", "role": "subject"},
                {"asset_id": "eq:COIN", "role": "venue"},
            ],
            "venue_decision": True,
        },
        traps=(("$COIN", 0, "eq:COIN", "eq:COIN", "cashtag"),),
    ),
    hand(
        "h-list-venue-3", "prnewswire.example", 107, 11,
        "ARM HOLDINGS WILL BEGIN TRADING ON NASDAQ FROM JUNE 3 AFTER FINAL FILING",
        "The company filed its final prospectus a week earlier and confirmed the timetable on "
        "Tuesday. Kestrel Analytics said the free float would be small at the open in Kentbridge. "
        "Trading is expected to begin at the regular opening auction. The venue published its own "
        "notice on the same morning.",
        event={
            "event_type": "listing", "stage": "confirmed", "attributes": {},
            "links": [
                {"asset_id": "eq:ARM", "role": "subject"},
                {"asset_id": "eq:NDAQ", "role": "venue"},
            ],
            "venue_decision": True,
        },
        traps=(("ARM HOLDINGS", 0, "eq:ARM", "eq:ARM", "all_caps_context"),),
    ),
    hand(
        "h-delist-venue-1", "wsj.example", 109, 16,
        "A venue closes its book on an electric-vehicle maker in Calderfield",
        "NYSE will suspend trading in Lucid Group at the end of the month after a review of "
        "continued trading standards. Quotes stop at the Calderfield close and shareholders keep "
        "their positions. Harborline Research said the move followed several quarters below the "
        "minimum price threshold. The company said it would seek an over-the-counter quotation "
        "instead.",
        event={
            "event_type": "delisting", "stage": "confirmed",
            "attributes": {"venue": "NYSE"},
            "links": [
                {"asset_id": "eq:LCID", "role": "subject"},
                {"asset_id": "eq:ICE", "role": "venue"},
            ],
            "venue_decision": True,
        },
    ),
    hand(
        "h-list-caps-1", "binance.example", 111, 8,
        "NEAR PROTOCOL TOKEN GOES LIVE ON BINANCE FOR SPOT TRADING",
        "The venue said deposits opened on Thursday and that withdrawals would follow a day later. "
        "Order books were scheduled to open in stages through the Redhaven session. Ashgrove "
        "Securities said depth looked reasonable in the first hour of quoting. The core team "
        "confirmed the schedule in a post of its own.",
        event={
            "event_type": "listing", "stage": "confirmed", "attributes": {},
            "links": [{"asset_id": "cx:NEAR", "role": "subject"}],
            "venue_decision": True,
        },
        traps=(("NEAR PROTOCOL", 0, "cx:NEAR", "cx:NEAR", "all_caps_context"),),
    ),
    hand(
        "h-reg-caps-1", "sec.example", 113, 14,
        "SEC OPENED AN INVESTIGATION INTO INTERNET COMPUTER FOUNDATION OVER TOKEN SALES",
        "The complaint runs to ninety pages and names two former officers of the foundation. "
        "Vellum Capital said the filing in Thornbury had been expected for months. Neither side "
        "has proposed a schedule for the first hearing. Lawyers for the foundation declined to "
        "comment when reached on Friday.",
        event={
            "event_type": "regulatory_action", "stage": "confirmed",
            "attributes": {"polarity": "adverse", "agency": "SEC"},
            "links": [{"asset_id": "cx:ICP", "role": "subject"}],
        },
        traps=(("INTERNET COMPUTER", 0, "cx:ICP", "cx:ICP", "all_caps_context"),),
    ),
    hand(
        "h-hack-1", "cointelegraph.example", 115, 7,
        "An incident report lands in Pellmore on a rollup",
        "Attackers siphoned $47 million from the Arbitrum bridge in the early hours of Saturday, "
        "researchers at Dunmoor Research said. On-chain traces put the outflow across nine "
        "addresses. A growth hack shared at a Pellmore conference near the end of last season had "
        "used similar tooling for benign purposes. The team said a fuller write-up would follow "
        "within days.",
        event={
            "event_type": "hack_exploit", "stage": "confirmed",
            "attributes": {"amount_usd": 47000000.0, "vector": "bridge"},
            "links": [{"asset_id": "cx:ARB", "role": "subject"}],
        },
        traps=(("near the end", 0, None, "cx:NEAR", "metaphor_adjacent"),),
    ),
    hand(
        "h-earn-1", "reuters.example", 117, 13,
        "A stronger print lands from a social platform in Redhaven",
        "Meta Platforms shares rose after quarterly profit topped expectations by 6.4 %, the "
        "company said on Thursday. Segment disclosure put losses in the newer division slightly "
        "narrower than the prior period. Estridge Analytics said the Redhaven desk had fielded "
        "heavy client interest all morning. The company also confirmed its capital plan for the "
        "year ahead.",
        event={
            "event_type": "earnings_surprise", "stage": "confirmed",
            "attributes": {"polarity": "beat", "surprise_pct": 6.4},
            "links": [{"asset_id": "eq:META", "role": "subject"}],
        },
        traps=(("Meta Platforms", 0, "eq:META", "eq:META", "company_name"),),
    ),
    # -- 22 no-event traps ------------------------------------------------- #
    hand(
        "t-near-1", "marketchatter.example", 104, 17,
        "A quiet session in Ashford as volumes drift",
        "Volumes drifted lower near the end of the Ashford session, with two-way interest thin "
        "through the afternoon. Kestrel Analytics said desks had been positioning near the end of "
        "the quarter rather than reacting to anything specific. No single sector led the tape. The "
        "bulletin was distributed to subscribers in two regions.",
        traps=(
            ("near the end", 0, None, "cx:NEAR", "common_word"),
            ("near the end", 1, None, "cx:NEAR", "common_word"),
        ),
    ),
    hand(
        "t-apple-1", "dailyfin.example", 104, 18,
        "A commodity note from Marlow on orchard output",
        "Apple growers in the Marlow valley expect a larger crop this year, according to a trade "
        "body survey. One packer said an apple picked on a cool night keeps far longer in storage. "
        "Harborline Research does not cover the sector. The wire carried an updated version "
        "shortly afterwards.",
        traps=(
            ("Apple", 0, None, "eq:AAPL", "common_word"),
            ("apple", 0, None, "eq:AAPL", "common_word"),
        ),
    ),
    hand(
        "t-metaphor-1", "streetnotes.example", 105, 9,
        "A weekend build event in Kentbridge draws students",
        "A hackathon in Kentbridge drew two hundred students over the weekend, organisers said. "
        "One team rebuilt a checkout flow inside a shell script in under six hours. Northgate "
        "Partners sponsored the prize fund. A shorter summary ran on the evening list.",
        traps=(("shell", 0, None, "eq:SHEL", "metaphor_adjacent"),),
    ),
    hand(
        "t-metaphor-2", "chainbeat.example", 106, 9,
        "A marketing note out of Wexley on funnel design",
        "A growth hack described at a Wexley workshop shortened onboarding by two steps, the "
        "organisers said. The team called the approach meta but useful, and published a short "
        "write-up. Vellum Capital sponsored the venue for the day. Editors added background from "
        "earlier reporting to the item.",
        traps=(("meta", 0, None, "eq:META", "metaphor_adjacent"),),
    ),
    hand(
        "t-metaphor-3", "tokenwire.example", 107, 9,
        "A productivity column from Dunmoor",
        "A life hack shared in a Dunmoor newsletter suggests batching messages twice a day. The "
        "author described the calendar as an oracle for the week ahead rather than a schedule. "
        "Brightmoor Advisors reprinted the column in full. The desk flagged the item for follow-up "
        "in the morning file.",
        traps=(("oracle", 0, None, "eq:ORCL", "metaphor_adjacent"),),
    ),
    hand(
        "t-lower-1", "marketchatter.example", 108, 17,
        "A civic round-up from Calderfield",
        "Applicants for a work visa in Calderfield now wait eleven weeks, the ministry said. "
        "Officials described the schedule as a meta problem for the department rather than a "
        "policy shift. A local oracle of sorts, the town clerk, keeps the waiting list by hand. "
        "Volunteers tidied the shell of a building on the same street, and a second visa category "
        "was left unaffected.",
        traps=(
            ("visa", 0, None, "eq:V", "common_word"),
            ("meta", 0, None, "eq:META", "common_word"),
            ("oracle", 0, None, "eq:ORCL", "common_word"),
            ("shell", 0, None, "eq:SHEL", "common_word"),
            ("visa", 1, None, "eq:V", "common_word"),
        ),
    ),
    hand(
        "t-one-1", "dailyfin.example", 109, 18,
        "A session wrap from Ostend Bay",
        "One of the quieter sessions of the month closed with breadth roughly even in Ostend Bay. "
        "At one point the tape looked set to break lower, but the move faded before the close. "
        "Pellmore Group said one of the larger desks had stepped back for the week. A longer "
        "write-up was promised for later in the week.",
        traps=(
            ("One of the", 0, None, "cx:ONE", "common_word"),
            ("At one point", 0, None, "cx:ONE", "common_word"),
            ("one of the", 0, None, "cx:ONE", "common_word"),
        ),
    ),
    hand(
        "t-coin-1", "streetnotes.example", 110, 17,
        "A column from Thornbury on decision-making",
        "Treating the outcome as a coin flip understates the information already available, the "
        "columnist argued from Thornbury. The other side of the coin is that most desks price the "
        "risk long before it is written down. Wexley Quantitative reprinted an extract. The "
        "bulletin was distributed to subscribers in two regions.",
        traps=(
            ("coin flip", 0, None, "eq:COIN", "common_word"),
            ("the coin", 0, None, "eq:COIN", "common_word"),
        ),
    ),
    hand(
        "t-caps-1", "ap.example", 111, 17,
        "OIL PRICES NEAR A THREE MONTH HIGH AS SUPPLY TIGHTENS IN ASHFORD",
        "Traders in Ashford pointed to steady demand and slower output growth through the quarter. "
        "Kestrel Analytics said the move had been building for two weeks. Inventories drew for a "
        "third consecutive week. A shorter summary ran on the evening list.",
        traps=(("NEAR", 0, None, "cx:NEAR", "all_caps_common"),),
    ),
    hand(
        "t-caps-2", "ap.example", 112, 17,
        "ONE MORE WEEK OF DEBATE BEFORE THE MARLOW BUDGET VOTE",
        "Officials in Marlow said the timetable had slipped by a week. Harborline Research said "
        "the outcome mattered little for the tape. Two committees still have to report. Coverage "
        "of the story continued into the following session.",
        traps=(("ONE", 0, None, "cx:ONE", "all_caps_common"),),
    ),
    hand(
        "t-caps-3", "ap.example", 113, 17,
        "APE CONSERVATION FUNDING RISES IN THE KENTBRIDGE REGION",
        "Conservation groups in Kentbridge said grants had doubled since the last funding cycle. "
        "Northgate Partners has no position in the sector. Field teams reported steadier "
        "population counts. A longer write-up was promised for later in the week.",
        traps=(("APE", 0, None, "cx:APE", "all_caps_common"),),
    ),
    hand(
        "t-caps-4", "ap.example", 114, 17,
        "COIN COLLECTORS GATHER IN WEXLEY FOR THE SPRING FAIR",
        "Dealers in Wexley reported strong attendance across the weekend. Vellum Capital does not "
        "cover collectibles. Several lots sold above the guide price. The wire carried an updated "
        "version shortly afterwards.",
        traps=(("COIN", 0, None, "eq:COIN", "all_caps_common"),),
    ),
    hand(
        "t-caps-5", "ap.example", 115, 17,
        "NEAR RECORD RAINFALL SOAKS THE DUNMOOR VALLEY OVERNIGHT",
        "Local officials in Dunmoor said drainage held through the night. Brightmoor Advisors "
        "closed its office for the morning. Roads reopened by midday. Editors added background "
        "from earlier reporting to the item.",
        traps=(("NEAR", 0, None, "cx:NEAR", "all_caps_common"),),
    ),
    hand(
        "t-quoted-1", "chainbeat.example", 116, 17,
        "A software release note from Redhaven",
        'The update renames the "ALL" filter and moves the "KEY" shortcut into a submenu, the '
        "vendor said from Redhaven. Ashgrove Securities does not cover the vendor. Users can "
        "revert to the previous layout at any time. The desk flagged the item for follow-up in the "
        "morning file.",
        traps=(
            ('"ALL"', 0, None, "eq:ALL", "quoted_product"),
            ('"KEY"', 0, None, "eq:KEY", "quoted_product"),
        ),
    ),
    hand(
        "t-icp-1", "streetnotes.example", 117, 17,
        "A marketing framework note from Larkfield",
        "Sales teams in Larkfield increasingly build an ICP before writing any outbound copy, the "
        "trainer said. A second ICP is often kept for enterprise accounts. Pellmore Group "
        "circulated the deck internally. A shorter summary ran on the evening list.",
        traps=(
            ("ICP", 0, None, "cx:ICP", "acronym"),
            ("ICP", 1, None, "cx:ICP", "acronym"),
        ),
    ),
    hand(
        "t-icp-2", "dailyfin.example", 118, 17,
        "A public-health briefing from Brightmoor",
        "The ICP guidance was updated for the third time this year, officials in Brightmoor said. "
        "Estridge Analytics does not cover the sector. Hospitals have until the autumn to comply. "
        "The bulletin was distributed to subscribers in two regions.",
        traps=(("ICP", 0, None, "cx:ICP", "acronym"),),
    ),
    hand(
        "t-near-link-1", "coindesk.example", 119, 17,
        "A validator update from the Ashford desk",
        "NEAR Protocol validators completed a scheduled upgrade on Tuesday, the core team said. "
        "Staking rewards on NEAR were unchanged through the transition, according to Kestrel "
        "Analytics. No incident was reported in Ashford. A longer write-up was promised for later "
        "in the week.",
        traps=(
            ("NEAR Protocol", 0, "cx:NEAR", "cx:NEAR", "context_alias"),
            ("NEAR were", 0, "cx:NEAR", "cx:NEAR", "context_alias"),
        ),
    ),
    hand(
        "t-near-ape-1", "theblock.example", 120, 17,
        "A token round-up from the Marlow desk",
        "Governance activity across the APE token rose ahead of the vote, according to Harborline "
        "Research. Staking flows into NEAR were steady over the same window in Marlow. Neither "
        "community has scheduled a further ballot. Coverage of the story continued into the "
        "following session.",
        traps=(
            ("APE token", 0, "cx:APE", "cx:APE", "context_alias"),
            ("NEAR were", 0, "cx:NEAR", "cx:NEAR", "context_alias"),
        ),
    ),
    hand(
        "t-ape-1", "cointelegraph.example", 121, 17,
        "A DAO calendar note from Kentbridge",
        "The APE treasury proposal reached quorum on Wednesday, the DAO said. A second APE vote on "
        "grants opens next week, the DAO added, according to Northgate Partners. Turnout in "
        "Kentbridge was described as high. A shorter summary ran on the evening list.",
        traps=(
            ("APE treasury", 0, "cx:APE", "cx:APE", "context_alias"),
            ("APE vote", 0, "cx:APE", "cx:APE", "context_alias"),
        ),
    ),
    hand(
        "t-meta-1", "cnbc.example", 122, 17,
        "A platform round-up from the Thornbury desk",
        "Meta shares were steady through the Thornbury session, according to Vellum Capital. Meta "
        "Platforms shares were unchanged after the company confirmed its capital plan. Oracle Corp "
        "shares also drifted in a narrow range. Shares of $COIN were little changed on the same "
        "screen.",
        traps=(
            ("Meta shares", 0, "eq:META", "eq:META", "context_alias"),
            ("Meta Platforms", 0, "eq:META", "eq:META", "context_alias"),
            ("Oracle Corp", 0, "eq:ORCL", "eq:ORCL", "context_alias"),
            ("$COIN", 0, "eq:COIN", "eq:COIN", "cashtag"),
        ),
    ),
    hand(
        "t-shell-1", "ft.example", 123, 17,
        "A cross-market note from the Ostend Bay desk",
        "Oracle Corp cloud bookings were flat in the quarter, according to Brightmoor Advisors. "
        "Shell plc said refinery runs in the region held steady, with barrels from two fields "
        "unchanged. A second update from Shell plc is expected after the energy conference. No "
        "company made a filing during the session.",
        traps=(
            ("Oracle Corp", 0, "eq:ORCL", "eq:ORCL", "context_alias"),
            ("Shell plc", 0, "eq:SHEL", "eq:SHEL", "context_alias"),
            ("Shell plc", 1, "eq:SHEL", "eq:SHEL", "context_alias"),
        ),
    ),
    hand(
        "t-aapl-1", "bloomberg.example", 124, 17,
        "A screen check from the Redhaven desk",
        "Quotes for NASDAQ: AAPL were unchanged through the morning, according to Larkfield "
        "Associates. A later check on NASDAQ: AAPL showed the same print into the afternoon. "
        "Estridge Analytics said the screen had not refreshed in Redhaven. The desk flagged the "
        "item for follow-up in the morning file.",
        traps=(
            ("NASDAQ: AAPL", 0, "eq:AAPL", "eq:AAPL", "exchange_prefix"),
            ("NASDAQ: AAPL", 1, "eq:AAPL", "eq:AAPL", "exchange_prefix"),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Generated no-event articles (30)
# --------------------------------------------------------------------------- #

NO_EVENT_FRAMES: dict[str, tuple[Frame, ...]] = {
    "dev": (
        F(
            "",
            "{subject_plain} sets a date for its {quarter}-quarter update",
            "{subject} reports its {quarter}-quarter figures on {weekday}, the company confirmed "
            "in a short calendar notice.",
            "The consensus range compiled by {firm} spans a narrow band, and {analyst} said the "
            "{city} desk expects little movement before the print.",
        ),
        F(
            "",
            "A quiet tape for {subject_plain} through the {city} session",
            "{subject} traded in a narrow range through the {city} session on {weekday}, with no "
            "company news on the wire.",
            "{analyst} at {firm} said positioning rather than fundamentals explained the {n1} % "
            "range over the past fortnight.",
        ),
    ),
    "val": (
        F(
            "",
            "A facility opens in {city} for {subject_plain}",
            "{subject} opened a distribution facility in {city} on {weekday}, the company said in "
            "a short note to staff.",
            "{analyst} of {firm} put the capital cost near {n1},{n2}00 thousand and said the site "
            "would employ several hundred people.",
        ),
        F(
            "",
            "A sector note from {firm} touches {subject_plain}",
            "{subject} featured in a sector review circulated by {firm} on {weekday}, alongside "
            "several peers.",
            "{analyst} wrote that the {city} valuation gap had narrowed by roughly {n1} % without "
            "any change in disclosure.",
        ),
    ),
}


def build_no_event(
    gazetteer: dict[str, GazAsset], allocator: AssetAllocator, start_index: int
) -> tuple[list[Article], list[str]]:
    articles: list[Article] = []
    used: list[str] = []
    for offset in range(30):
        index = start_index + offset
        family = "dev" if offset % 2 == 0 else "val"
        frame = NO_EVENT_FRAMES[family][(offset // 2) % 2]
        kind = "crypto" if offset % 5 == 0 else "equity"
        asset = gazetteer[allocator.take(kind)]
        used.append(asset.id)
        slots = flavor(index)
        surface, plain = asset_surface(asset, ("ticker", "alias", "strong")[offset % 3])
        slots.update(subject=surface, subject_plain=plain)
        pool = DISTRACTORS[family]
        title = frame.title.format_map(slots)
        head = " ".join(
            [
                frame.lead.format_map(slots),
                frame.detail.format_map(slots),
                pool[spread(index, 21, len(pool))].format_map(slots),
                pool[
                    (spread(index, 22, len(pool) - 1) + spread(index, 21, len(pool)) + 1)
                    % len(pool)
                ].format_map(slots),
            ]
        )
        while len(head) < 430:
            head = f"{head} {pool[spread(index, len(head), len(pool))].format_map(slots)}"
        domain = (T2_DOMAINS + T3_DOMAINS)[index % (len(T2_DOMAINS) + len(T3_DOMAINS))]
        day = FIRST_EVENT_DAY + (offset * 5 + 3) % (LAST_EVENT_DAY - FIRST_EVENT_DAY + 1)
        articles.append(
            Article(
                external_id=f"ne-{offset:03d}",
                source_domain=domain,
                published_at=stamp(day, 6 + offset % 3, (offset * 11) % 60),
                title=title,
                body=f"{head} {TAILS[offset % len(TAILS)]}",
                family=family,
            )
        )
    return articles, used


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_corpus() -> dict[str, Any]:
    gazetteer = load_gazetteer()
    allocator = AssetAllocator(gazetteer)

    articles, events = build_generated(gazetteer, allocator)
    generated_count = len(articles)

    trap_mentions: list[dict[str, Any]] = []
    hand_assets: set[str] = set()
    for spec in HAND_ARTICLES:
        article = Article(
            external_id=spec["key"],
            source_domain=spec["domain"],
            published_at=stamp(spec["day"], spec["hour"], 30),
            title=spec["title"],
            body=spec["body"],
            family="val",
        )
        articles.append(article)
        text = article.analysis_text()
        for surface, occurrence, asset_id, collides, category in spec["traps"]:
            start = -1
            for _ in range(occurrence + 1):
                start = text.find(surface, start + 1)
                if start < 0:
                    raise RuntimeError(
                        f"{spec['key']}: trap surface {surface!r} occurrence {occurrence} not found"
                    )
            trap_mentions.append(
                {
                    "id": f"{spec['key']}#{len(trap_mentions)}",
                    "external_id": spec["key"],
                    "surface": surface,
                    "start": start,
                    "end": start + len(surface),
                    "asset_id": asset_id,
                    "collides_with": collides,
                    "category": category,
                }
            )
            hand_assets.add(collides)
        if spec["event"] is not None:
            payload = spec["event"]
            events.append(
                TruthEvent(
                    key=spec["key"],
                    event_type=payload["event_type"],
                    family="val",
                    stage=payload["stage"],
                    attributes=dict(payload["attributes"]),
                    links=[dict(link) for link in payload["links"]],
                    article_external_ids=[spec["key"]],
                    event_date=article.published_at[:10],
                    observed_at=article.published_at,
                    tier_class=_tier_of(spec["domain"]),
                    single_source_t3=_tier_of(spec["domain"]) == "t3",
                    origin="hand",
                )
            )
            hand_assets.update(link["asset_id"] for link in payload["links"])

    no_event_articles, no_event_assets = build_no_event(gazetteer, allocator, len(events) + 500)
    articles.extend(no_event_articles)

    articles.sort(key=lambda a: (a.published_at, a.external_id))
    _assign_batches(articles, events)

    assets_used = sorted(
        {link["asset_id"] for event in events for link in event.links}
        | hand_assets
        | set(no_event_assets)
    )
    kinds = {asset_id: gazetteer[asset_id].kind for asset_id in assets_used}

    truth_links = _truth_links(events)
    role_decisions = _role_decisions(events)
    margins = _jaccard_margins(articles, events)

    return {
        "articles": articles,
        "events": events,
        "trap_mentions": trap_mentions,
        "assets_used": assets_used,
        "asset_kinds": kinds,
        "truth_links": truth_links,
        "role_decisions": role_decisions,
        "margins": margins,
        "generated_count": generated_count,
        "gazetteer": gazetteer,
    }


def _tier_of(domain: str) -> str:
    for tier, domains in DOMAINS_BY_TIER.items():
        if domain in domains:
            return tier
    return "t3"


def _assign_batches(articles: list[Article], events: list[TruthEvent]) -> None:
    """Five chronological arrival batches, with two deliberate out-of-order arrivals.

    D1 (FR-14) needs a corpus where the *arrival* order differs from the
    publication order: one Reuters follow-up that lands after a later-published
    blog post, and one late corroboration whose article only shows up in a
    subsequent run and must raise its signal's confidence through a revision.
    """
    total = len(articles)
    for position, article in enumerate(articles):
        article.arrival_batch = min(position * ARRIVAL_BATCHES // total, ARRIVAL_BATCHES - 1)

    by_id = {article.external_id: article for article in articles}
    late = next(
        event
        for event in events
        if len(event.article_external_ids) == 3
        and by_id[event.article_external_ids[0]].arrival_batch <= ARRIVAL_BATCHES - 2
    )
    for external_id in late.article_external_ids[1:]:
        by_id[external_id].arrival_batch = min(
            by_id[external_id].arrival_batch + 1, ARRIVAL_BATCHES - 1
        )
    out_of_order = next(
        article
        for article in articles
        if article.source_domain == "reuters.example" and article.arrival_batch <= 1
    )
    out_of_order.arrival_batch = 3


def _truth_links(events: list[TruthEvent]) -> list[dict[str, str]]:
    return [
        {"event_key": event.key, "asset_id": link["asset_id"], "role": link["role"]}
        for event in events
        for link in event.links
    ]


def _role_decisions(events: list[TruthEvent]) -> list[dict[str, Any]]:
    """40 VAL decisions: 28 acquirer/target, 4 abstentions, 8 venue-vs-subject."""
    decisions: list[dict[str, Any]] = []
    for event in events:
        if event.family != "val":
            continue
        if event.event_type == "mna":
            roles = {link["role"] for link in event.links}
            if roles == {"acquirer", "target"}:
                for link in event.links:
                    decisions.append(
                        {
                            "id": f"role-{event.key}-{link['role']}",
                            "event_key": event.key,
                            "kind": "mna_role",
                            "asset_id": link["asset_id"],
                            "expected_role": link["role"],
                        }
                    )
            else:
                decisions.append(
                    {
                        "id": f"role-{event.key}-abstain",
                        "event_key": event.key,
                        "kind": "abstention",
                        "asset_ids": [link["asset_id"] for link in event.links],
                    }
                )
        elif event.event_type in ("listing", "delisting"):
            venue = next((link for link in event.links if link["role"] == "venue"), None)
            subject = next((link for link in event.links if link["role"] == "subject"), None)
            if subject is None:
                continue
            decisions.append(
                {
                    "id": f"role-{event.key}-venue",
                    "event_key": event.key,
                    "kind": "venue",
                    "subject_asset_id": subject["asset_id"],
                    "venue_asset_id": venue["asset_id"] if venue else None,
                }
            )
    venue_decisions = [d for d in decisions if d["kind"] == "venue"]
    keep = [d for d in venue_decisions if d["venue_asset_id"]][:4]
    keep += [d for d in venue_decisions if not d["venue_asset_id"]][: 8 - len(keep)]
    kept_ids = {d["id"] for d in keep}
    return [d for d in decisions if d["kind"] != "venue" or d["id"] in kept_ids]


def _jaccard_margins(articles: list[Article], events: list[TruthEvent]) -> dict[str, float]:
    cluster_of: dict[str, str] = {}
    for event in events:
        for external_id in event.article_external_ids:
            cluster_of[external_id] = event.key
    signature = {
        article.external_id: shingles(normalize(article.title), normalize(article.body))
        for article in articles
    }
    intra_min, inter_max = 1.0, 0.0
    ordered = list(articles)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            score = jaccard(signature[left.external_id], signature[right.external_id])
            same = (
                left.external_id in cluster_of
                and cluster_of.get(left.external_id) == cluster_of.get(right.external_id)
            )
            if same:
                intra_min = min(intra_min, score)
            else:
                inter_max = max(inter_max, score)
    return {"intra_min": round(intra_min, 4), "inter_max": round(inter_max, 4)}


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_hygiene(corpus: dict[str, Any]) -> None:
    """No unintended gazetteer surface may appear in any article (EVALS ground truth)."""
    gazetteer = corpus["gazetteer"]
    allowed: dict[str, set[str]] = {}
    for event in corpus["events"]:
        for external_id in event.article_external_ids:
            allowed.setdefault(external_id, set()).update(
                link["asset_id"] for link in event.links
            )
    for trap in corpus["trap_mentions"]:
        allowed.setdefault(trap["external_id"], set()).add(trap["collides_with"])
        if trap["asset_id"]:
            allowed[trap["external_id"]].add(trap["asset_id"])
    for article in corpus["articles"]:
        found = scan_surfaces(article.analysis_text(), gazetteer)
        expected = allowed.get(article.external_id, set())
        if article.external_id.startswith("ne-"):
            expected = found  # no-event articles carry exactly one deliberate mention
            if len(found) != 1:
                raise AssertionError(f"{article.external_id}: expected 1 surface, got {found}")
        stray = found - expected
        if stray:
            raise AssertionError(f"{article.external_id}: unintended gazetteer surfaces {stray}")


def check_no_stray_triggers(corpus: dict[str, Any]) -> None:
    """A no-event article must contain no trigger lexeme at all."""
    lexemes = load_trigger_lexemes()
    matchers = [(lexeme, phrase_re(lexeme)) for lexeme in lexemes]
    event_ids = {
        external_id
        for event in corpus["events"]
        for external_id in event.article_external_ids
    }
    for article in corpus["articles"]:
        if article.external_id in event_ids:
            continue
        text = article.analysis_text()
        for lexeme, matcher in matchers:
            if matcher.search(text):
                raise AssertionError(
                    f"{article.external_id}: no-event article contains trigger {lexeme!r}"
                )


def check_disjoint(corpus: dict[str, Any]) -> dict[str, Any]:
    """Trigger-bearing 3-grams and distractor pools must not cross the DEV/VAL line."""
    lexemes = load_trigger_lexemes()
    matchers = [(lexeme, phrase_re(lexeme)) for lexeme in lexemes]
    by_family: dict[str, set[tuple[str, ...]]] = {"dev": set(), "val": set()}
    article_of = {a.external_id: a for a in corpus["articles"]}

    for event in corpus["events"]:
        for external_id in event.article_external_ids:
            article = article_of[external_id]
            text = article.analysis_text()
            hits: list[tuple[int, int]] = []
            for _, matcher in matchers:
                hits.extend((m.start(), m.end()) for m in matcher.finditer(text))
            if not hits:
                continue
            words = list(_WORD_RE.finditer(text.casefold()))
            for start, end in hits:
                inside = {
                    index
                    for index, word in enumerate(words)
                    if word.start() < end and start < word.end()
                }
                if not inside:
                    continue
                for i in range(max(min(inside) - 2, 0), min(max(inside) + 1, len(words) - 2)):
                    gram = tuple(words[j].group(0) for j in range(i, i + 3))
                    by_family[event.family].add(gram)

    overlap = by_family["dev"] & by_family["val"]
    if overlap:
        raise AssertionError(f"trigger 3-grams shared across families: {sorted(overlap)[:5]}")
    shared = set(DISTRACTORS["dev"]) & set(DISTRACTORS["val"])
    if shared:
        raise AssertionError(f"distractor pools share sentences: {sorted(shared)[:3]}")

    margins = corpus["margins"]
    if margins["intra_min"] < 0.72:
        raise AssertionError(f"intra-cluster Jaccard margin {margins['intra_min']} < 0.72")
    if margins["inter_max"] > 0.45:
        raise AssertionError(f"inter-cluster Jaccard margin {margins['inter_max']} > 0.45")
    return {
        "dev_trigger_ngrams": len(by_family["dev"]),
        "val_trigger_ngrams": len(by_family["val"]),
        "shared_trigger_ngrams": 0,
        **margins,
    }


def check_counts(corpus: dict[str, Any]) -> dict[str, Any]:
    events = corpus["events"]
    by_type: dict[str, int] = {}
    by_family: dict[str, int] = {"dev": 0, "val": 0}
    for event in events:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
        by_family[event.family] += 1
    traps = corpus["trap_mentions"]
    link_traps = sum(1 for t in traps if t["asset_id"])
    no_link_traps = len(traps) - link_traps
    roles = corpus["role_decisions"]
    counts = {
        "articles": len(corpus["articles"]),
        "events": len(events),
        "events_by_type": dict(sorted(by_type.items())),
        "events_by_family": by_family,
        "trap_mentions": len(traps),
        "trap_link": link_traps,
        "trap_no_link": no_link_traps,
        "role_decisions": len(roles),
        "role_mna": sum(1 for d in roles if d["kind"] == "mna_role"),
        "role_abstention": sum(1 for d in roles if d["kind"] == "abstention"),
        "role_venue": sum(1 for d in roles if d["kind"] == "venue"),
    }
    expected = {
        "events": 108,
        "trap_mentions": 50,
        "trap_link": 22,
        "trap_no_link": 28,
        "role_decisions": 40,
        "role_mna": 28,
        "role_abstention": 4,
        "role_venue": 8,
    }
    for name, want in expected.items():
        if counts[name] != want:
            raise AssertionError(f"composition check: {name} = {counts[name]}, expected {want}")
    if by_family != {"dev": 32, "val": 76}:
        raise AssertionError(f"DEV/VAL split is {by_family}, expected 32/76")
    return counts


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def render(corpus: dict[str, Any]) -> tuple[str, str]:
    lines = [
        json.dumps(article.row(), sort_keys=True, ensure_ascii=False)
        for article in corpus["articles"]
    ]
    articles_jsonl = "\n".join(lines) + "\n"

    truth = {
        "seed": SEED,
        "day_zero": DAY0.isoformat(),
        "series_days": SERIES_DAYS,
        "first_event_day": FIRST_EVENT_DAY,
        "last_event_day": LAST_EVENT_DAY,
        "as_of": stamp(AS_OF_DAY, 7),
        "arrival_batches": {
            article.external_id: article.arrival_batch for article in corpus["articles"]
        },
        "families": {article.external_id: article.family for article in corpus["articles"]},
        "assets_used": corpus["assets_used"],
        "asset_kinds": corpus["asset_kinds"],
        "events": [asdict(event) for event in corpus["events"]],
        "truth_links": corpus["truth_links"],
        "trap_mentions": corpus["trap_mentions"],
        "role_decisions": corpus["role_decisions"],
        "jaccard_margins": corpus["margins"],
        "counts": corpus["counts"],
        "disjointness": corpus["disjointness"],
    }
    return articles_jsonl, json.dumps(truth, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--check-disjoint", action="store_true")
    parser.add_argument("--regen-check", action="store_true")
    args = parser.parse_args(argv)
    if args.seed != SEED:
        raise SystemExit(f"only the committed seed {SEED} reproduces the fixtures")

    corpus = build_corpus()
    check_hygiene(corpus)
    check_no_stray_triggers(corpus)
    corpus["disjointness"] = check_disjoint(corpus)
    corpus["counts"] = check_counts(corpus)
    articles_jsonl, truth_json = render(corpus)

    if args.check_disjoint:
        print(json.dumps(corpus["disjointness"], indent=2, sort_keys=True))
        return 0

    articles_path = HERE / "articles.jsonl"
    truth_path = HERE / "articles_truth.json"
    if args.regen_check:
        for path, want in ((articles_path, articles_jsonl), (truth_path, truth_json)):
            if not path.exists():
                print(f"MISSING {path}", file=sys.stderr)
                return 1
            if path.read_text(encoding="utf-8") != want:
                print(f"DIFF {path}", file=sys.stderr)
                return 1
        print("fixtures reproduce byte-identically")
        return 0

    articles_path.write_text(articles_jsonl, encoding="utf-8")
    truth_path.write_text(truth_json, encoding="utf-8")
    print(json.dumps(corpus["counts"], indent=2, sort_keys=True))
    print(json.dumps(corpus["disjointness"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
