"""FR-5: gazetteer linking, the all-caps guard, venue precedence and roles.

Precision over recall, abstention over guessing (SCOPE D-3).  Three evidence
classes, in descending strength:

  * **strong patterns** -- cashtags (`$AAPL`), exchange prefixes (`NASDAQ: AAPL`),
    a parenthesized ticker right after a name match: link confidence 0.95;
  * **plain ticker tokens** -- an ALL-CAPS standalone token, and either the asset
    is not ambiguity-flagged or a context keyword appears in the same sentence:
    0.85;
  * **name/alias matches** -- case-sensitive per gazetteer entry; a flagged asset
    additionally needs a same-sentence context keyword: 0.8 with context, 0.7
    unflagged.

The all-caps guard disables the plain-ticker class entirely inside wire-style
headlines and makes alias matching case-insensitive-but-context-required there.
Roles are then assigned from the trigger sentence, with venue spans consumed
before subject resolution and an explicit abstention whenever M&A roles or a
listing subject do not resolve uniquely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..datasets import Datasets
from ..models import Article, Asset, Event, EventLink, EventType, LinkRole
from .extract import ExtractedEvent, TriggerMatch, build_span, contains_phrase, phrase_matcher
from .normalize import Sentence, split_sentences

STRONG_CONFIDENCE = 0.95
TICKER_CONFIDENCE = 0.85
ALIAS_WITH_CONTEXT_CONFIDENCE = 0.80
ALIAS_PLAIN_CONFIDENCE = 0.70
ALL_CAPS_CONFIDENCE = 0.70

CAPS_RATIO_THRESHOLD = 0.70
CAPS_MIN_TOKENS = 4

NOTE_MNA_ROLE_UNRESOLVED = "link:mna_role_unresolved"
NOTE_AMBIGUOUS_SUBJECT = "link:ambiguous_subject"

_CASHTAG_RE = re.compile(r"\$([A-Z][A-Z0-9.]{0,9})\b")
_EXCHANGE_PREFIX_RE = re.compile(
    r"\b(?:NASDAQ|NYSE|NYSE ARCA|AMEX|CBOE|OTC)\s*:\s*([A-Z][A-Z0-9.]{0,9})\b"
)
_PAREN_TICKER_RE = re.compile(r"\(\s*(?:[A-Z]{2,6}\s*:\s*)?([A-Z][A-Z0-9.]{0,9})\s*\)")
_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z.'-]*")
_UPPER_TOKEN_RE = re.compile(r"(?<![\w$.])([A-Z][A-Z0-9]{1,8}(?:\.[A-Z0-9]{1,4})?)(?![\w])")

_SYMMETRIC_MARKERS = ("merger between", "merger of equals", "combination with", "merge with")
_BID_FOR_RE = re.compile(
    r"\b(?:takeover bid|buyout offer|unsolicited offer|tender offer|bid|offer)\s+for\b",
    re.IGNORECASE,
)
_BY_RE = re.compile(r"\bby\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Mention:
    """One gazetteer hit in one article, with the evidence class that produced it."""

    article_id: str
    asset_id: str
    start: int
    end: int
    confidence: float
    evidence_class: str
    sentence: Sentence

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)


# --------------------------------------------------------------------------- #
# All-caps guard
# --------------------------------------------------------------------------- #


def caps_ratio(sentence_text: str) -> tuple[float, int]:
    """Share of alphabetic tokens of length >= 2 that are entirely uppercase, and their count."""
    tokens = [t for t in _ALPHA_TOKEN_RE.findall(sentence_text) if len(t) >= 2]
    if not tokens:
        return 0.0, 0
    upper = sum(1 for t in tokens if t.isupper())
    return upper / len(tokens), upper


def is_all_caps_sentence(sentence_text: str) -> bool:
    """FR-5's wire-headline guard: >= 70 % uppercase over >= 4 such tokens."""
    ratio, upper_count = caps_ratio(sentence_text)
    return ratio >= CAPS_RATIO_THRESHOLD and upper_count >= CAPS_MIN_TOKENS


def has_context(sentence_text: str, asset: Asset) -> bool:
    return any(contains_phrase(sentence_text, kw) for kw in asset.context_keywords)


# --------------------------------------------------------------------------- #
# Gazetteer scan
# --------------------------------------------------------------------------- #


def _symbol_index(datasets: Datasets) -> dict[str, Asset]:
    cached = datasets.gazetteer_cache.get("symbols")
    if cached is None:
        cached = {a.symbol: a for a in datasets.linkable_assets()}
        datasets.gazetteer_cache["symbols"] = cached
    return cached


def _alias_entries(datasets: Datasets) -> list[tuple[str, Asset]]:
    """(alias, asset) pairs, longest surface first, so longest-match wins."""
    cached = datasets.gazetteer_cache.get("aliases")
    if cached is None:
        entries: list[tuple[str, Asset]] = []
        for asset in datasets.linkable_assets():
            entries.append((asset.name, asset))
            for alias in asset.aliases:
                entries.append((alias, asset))
        entries.sort(key=lambda pair: (-len(pair[0]), pair[0]))
        cached = entries
        datasets.gazetteer_cache["aliases"] = cached
    return cached


def scan_mentions(article: Article, datasets: Datasets) -> list[Mention]:
    """Longest-match gazetteer scan over the whole article (FR-5).

    Overlapping candidate mentions are resolved by (longest span, highest
    confidence, earliest start); the surviving mentions never overlap.

    Memoized per article id, which is a pure function of the article's content
    hash (DATA_MODEL.md's id scheme): the same id can only ever carry the same
    analysis text, so the memo cannot change an answer.  A cluster's articles are
    re-scanned once per event without it, and the active-window recompute repeats
    the whole pass on every ingest.  Callers must not mutate the returned list.
    """
    cache: dict[str, list[Mention]] = datasets.gazetteer_cache.setdefault("mentions", {})
    cached = cache.get(article.id)
    if cached is None:
        cached = _scan_mentions(article, datasets)
        cache[article.id] = cached
    return cached


def _scan_mentions(article: Article, datasets: Datasets) -> list[Mention]:
    text = article.analysis_text
    sentences = split_sentences(text, datasets.lexicons.abbreviations)
    symbols = _symbol_index(datasets)
    aliases = _alias_entries(datasets)
    candidates: list[Mention] = []

    for sentence in sentences:
        all_caps = is_all_caps_sentence(sentence.text)
        offset = sentence.start
        body = sentence.text

        # (asset, relative start, relative end, confidence, evidence class)
        raw: list[tuple[Asset, int, int, float, str]] = []

        # -- strong patterns: unaffected by the all-caps guard -----------------
        for match in _CASHTAG_RE.finditer(body):
            asset = symbols.get(match.group(1))
            if asset is not None:
                raw.append((asset, match.start(), match.end(), STRONG_CONFIDENCE, "cashtag"))
        for match in _EXCHANGE_PREFIX_RE.finditer(body):
            asset = symbols.get(match.group(1))
            if asset is not None:
                raw.append(
                    (asset, match.start(), match.end(), STRONG_CONFIDENCE, "exchange_prefix")
                )

        # -- name / alias matches ---------------------------------------------
        alias_hits: list[tuple[Asset, int, int]] = []
        for surface, asset in aliases:
            for start, end in _alias_positions(body, surface, case_insensitive=all_caps):
                if all_caps:
                    # Inside a wire headline *every* asset needs same-sentence context.
                    if not _all_caps_context(body, asset):
                        continue
                    confidence, klass = ALL_CAPS_CONFIDENCE, "alias_all_caps"
                elif asset.ambiguous:
                    if not has_context(body, asset):
                        continue
                    confidence, klass = ALIAS_WITH_CONTEXT_CONFIDENCE, "alias_context"
                else:
                    confidence, klass = ALIAS_PLAIN_CONFIDENCE, "alias"
                alias_hits.append((asset, start, end))
                raw.append((asset, start, end, confidence, klass))

        # -- parenthesized ticker right after a name match ---------------------
        for match in _PAREN_TICKER_RE.finditer(body):
            asset = symbols.get(match.group(1))
            if asset is None:
                continue
            preceded_by_name = any(
                hit_asset.id == asset.id and hit_end <= match.start() + 1
                for hit_asset, _, hit_end in alias_hits
            )
            if preceded_by_name:
                raw.append((asset, match.start(), match.end(), STRONG_CONFIDENCE, "paren_ticker"))

        # -- plain ticker tokens: disabled entirely inside all-caps sentences ---
        if not all_caps:
            for match in _UPPER_TOKEN_RE.finditer(body):
                asset = symbols.get(match.group(1))
                if asset is None:
                    continue
                if asset.ambiguous and not has_context(body, asset):
                    continue
                raw.append((asset, match.start(1), match.end(1), TICKER_CONFIDENCE, "ticker"))

        for asset, start, end, confidence, klass in raw:
            candidates.append(
                Mention(
                    article_id=article.id,
                    asset_id=asset.id,
                    start=offset + start,
                    end=offset + end,
                    confidence=confidence,
                    evidence_class=klass,
                    sentence=sentence,
                )
            )

    return _resolve_overlaps(candidates)


def _all_caps_context(body: str, asset: Asset) -> bool:
    """Same-sentence context evidence for an asset inside an all-caps sentence.

    Flagged assets use their own `context_keywords`; unflagged assets need the
    other half of their own name (e.g. "APPLE INC" or "SOLANA NETWORK") or a
    cashtag/exchange prefix, which the strong classes already handle.  A bare
    surface form in a wire headline is never enough (FR-5).
    """
    if asset.context_keywords:
        return has_context(body, asset)
    extra_terms = [t for t in asset.name.replace(",", " ").split() if len(t) > 2]
    surfaces = {asset.name.casefold(), *(a.casefold() for a in asset.aliases)}
    for term in extra_terms:
        if term.casefold() in surfaces:
            continue
        if contains_phrase(body, term):
            return True
    return False


_ALIAS_RE_CACHE: dict[tuple[str, bool], re.Pattern[str]] = {}


def _alias_matcher(surface: str, *, case_insensitive: bool) -> re.Pattern[str]:
    key = (surface, case_insensitive)
    matcher = _ALIAS_RE_CACHE.get(key)
    if matcher is None:
        flags = re.IGNORECASE if case_insensitive else 0
        parts = r"[\s\-]+".join(re.escape(p) for p in surface.split())
        matcher = re.compile(rf"(?<![\w$]){parts}(?![\w])", flags)
        _ALIAS_RE_CACHE[key] = matcher
    return matcher


def _alias_positions(body: str, surface: str, *, case_insensitive: bool) -> list[tuple[int, int]]:
    """Word-boundary positions of `surface`, case-sensitively unless told otherwise."""
    if not surface:
        return []
    matcher = _alias_matcher(surface, case_insensitive=case_insensitive)
    return [(m.start(), m.end()) for m in matcher.finditer(body)]


def _resolve_overlaps(candidates: list[Mention]) -> list[Mention]:
    """Longest match wins; ties broken by confidence, then position, then asset id."""
    ordered = sorted(
        candidates,
        key=lambda m: (-(m.end - m.start), -m.confidence, m.start, m.asset_id),
    )
    taken: list[Mention] = []
    for candidate in ordered:
        if any(candidate.start < t.end and t.start < candidate.end for t in taken):
            continue
        taken.append(candidate)
    return sorted(taken, key=lambda m: (m.start, m.asset_id))


# --------------------------------------------------------------------------- #
# Venue lexicon
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VenueHit:
    surface: str
    asset_id: str | None
    start: int
    end: int


def scan_venues(text: str, datasets: Datasets, within: Sentence | None = None) -> list[VenueHit]:
    """Venue surfaces, scanned before any role assignment so their spans are consumed."""
    hits: list[VenueHit] = []
    lo, hi = (within.start, within.end) if within is not None else (0, len(text))
    for surface, asset_id in sorted(
        datasets.lexicons.venue_lexicon.items(), key=lambda kv: (-len(kv[0]), kv[0])
    ):
        for match in phrase_matcher(surface).finditer(text, lo, hi):
            if any(match.start() < h.end and h.start < match.end() for h in hits):
                continue
            hits.append(
                VenueHit(surface=surface, asset_id=asset_id, start=match.start(), end=match.end())
            )
    return sorted(hits, key=lambda h: h.start)


# --------------------------------------------------------------------------- #
# Role assignment
# --------------------------------------------------------------------------- #


def link_event(
    extracted: ExtractedEvent,
    articles: dict[str, Article],
    datasets: Datasets,
) -> tuple[list[EventLink], list[str]]:
    """Assign roles for one event. Returns (links, notes)."""
    event = extracted.event
    mentions: dict[str, list[Mention]] = {}
    for article_id in sorted({t.article_id for t in extracted.triggers}):
        mentions[article_id] = scan_mentions(articles[article_id], datasets)
    # Mentions from every article in the event's evidence, so `mentioned` links are
    # complete even when only one article carries the trigger.
    for span in event.evidence:
        if span.article_id not in mentions:
            mentions[span.article_id] = scan_mentions(articles[span.article_id], datasets)

    primary = _primary_trigger(extracted, mentions)
    trigger_article = articles[primary.article_id]
    trigger_mentions = _mentions_in(mentions[primary.article_id], primary)

    venue_spans: dict[str, tuple[str, int, int]] = {}
    if event.event_type is EventType.mna:
        roles, notes = _mna_roles(primary, trigger_mentions)
    elif event.event_type in (EventType.listing, EventType.delisting):
        roles, notes, venue_spans = _listing_roles(
            primary, trigger_mentions, trigger_article, datasets
        )
    else:
        roles = {m.asset_id: LinkRole.subject for m in trigger_mentions}
        notes = []

    best: dict[str, Mention] = {}
    for article_mentions in mentions.values():
        for mention in article_mentions:
            current = best.get(mention.asset_id)
            if current is None or (mention.confidence, -mention.start) > (
                current.confidence,
                -current.start,
            ):
                best[mention.asset_id] = mention

    links: list[EventLink] = []
    for asset_id in sorted(set(best) | set(venue_spans)):
        role = roles.get(asset_id, LinkRole.mentioned)
        if asset_id in venue_spans:
            # The venue lexicon match *is* the evidence for a venue link, whether or
            # not the gazetteer scan also produced a mention for that asset.
            article_id, start, end = venue_spans[asset_id]
            confidence = STRONG_CONFIDENCE
        else:
            mention = best[asset_id]
            article_id, start, end = mention.article_id, mention.start, mention.end
            confidence = mention.confidence
        links.append(
            EventLink(
                event_id=event.id,
                asset_id=asset_id,
                role=role,
                link_confidence=confidence,
                evidence=build_span(articles[article_id], start, end),
            )
        )
    return links, notes


def _mentions_in(article_mentions: list[Mention], trigger: TriggerMatch) -> list[Mention]:
    return [m for m in article_mentions if trigger.sentence.start <= m.start < trigger.sentence.end]


def _primary_trigger(extracted: ExtractedEvent, mentions: dict[str, list[Mention]]) -> TriggerMatch:
    """The trigger match role assignment reads.

    A cluster usually carries the same trigger in a headline and in the opening
    sentence; the headline often names fewer parties ("Delisting notice" vs "Lucid
    Group will be delisted from Nasdaq").  The sentence naming the most distinct
    assets is therefore preferred, with (published_at, article_id, offset) breaking
    every tie -- so the choice stays a pure function of the input set.
    """

    def sort_key(trigger: TriggerMatch) -> tuple:
        distinct = len({m.asset_id for m in _mentions_in(mentions[trigger.article_id], trigger)})
        return (
            -distinct,
            trigger.published_at,
            trigger.article_id,
            trigger.sentence.start,
            trigger.trigger_start,
        )

    return min(extracted.triggers, key=sort_key)


def _distinct(mentions: list[Mention]) -> list[str]:
    seen: list[str] = []
    for mention in mentions:
        if mention.asset_id not in seen:
            seen.append(mention.asset_id)
    return seen


def _mna_roles(
    primary: TriggerMatch, trigger_mentions: list[Mention]
) -> tuple[dict[str, LinkRole], list[str]]:
    """FR-5's acquirer/target resolution, with an explicit abstention fallback.

    Guessing is forbidden: a role error is a sign error on a high-magnitude prior
    (SCOPE D-3), so anything that does not resolve *both* parties uniquely assigns
    `mentioned` to every party and emits no signal.
    """
    sentence = primary.sentence
    text = sentence.text
    rel_trigger_start = primary.trigger_start - sentence.start
    rel_trigger_end = primary.trigger_end - sentence.start

    if any(contains_phrase(text, marker) for marker in _SYMMETRIC_MARKERS):
        return {}, [NOTE_MNA_ROLE_UNRESOLVED]

    before = [m for m in trigger_mentions if m.end - sentence.start <= rel_trigger_start]
    after = [m for m in trigger_mentions if m.start - sentence.start >= rel_trigger_end]

    bid_match = _BID_FOR_RE.search(text)
    if bid_match is not None:
        bid_end = bid_match.end()
        target_side = [m for m in trigger_mentions if m.start - sentence.start >= bid_end]
        acquirer_side = [m for m in trigger_mentions if m.end - sentence.start <= bid_match.start()]
        targets = _distinct(target_side)
        acquirers = _distinct(acquirer_side)
        if len(targets) == 1 and len(acquirers) == 1 and targets[0] != acquirers[0]:
            return {acquirers[0]: LinkRole.acquirer, targets[0]: LinkRole.target}, []
        return {}, [NOTE_MNA_ROLE_UNRESOLVED]

    after_text = text[rel_trigger_end:]
    passive = primary.trigger_text.casefold().rstrip().endswith("by") or bool(
        re.match(r"\s+by\b", after_text, re.IGNORECASE)
    )
    if passive:
        by_match = _BY_RE.search(text, rel_trigger_start)
        by_end = by_match.end() if by_match else rel_trigger_end
        acquirer_side = [m for m in trigger_mentions if m.start - sentence.start >= by_end]
        acquirers = _distinct(acquirer_side)
        targets = _distinct(before)
        if len(acquirers) == 1 and len(targets) == 1 and acquirers[0] != targets[0]:
            return {acquirers[0]: LinkRole.acquirer, targets[0]: LinkRole.target}, []
        return {}, [NOTE_MNA_ROLE_UNRESOLVED]

    acquirers = _distinct(before)
    targets = _distinct(after)
    if len(acquirers) == 1 and len(targets) == 1 and acquirers[0] != targets[0]:
        return {acquirers[0]: LinkRole.acquirer, targets[0]: LinkRole.target}, []
    return {}, [NOTE_MNA_ROLE_UNRESOLVED]


def _listing_roles(
    primary: TriggerMatch,
    trigger_mentions: list[Mention],
    article: Article,
    datasets: Datasets,
) -> tuple[dict[str, LinkRole], list[str], dict[str, tuple[str, int, int]]]:
    """Venue precedence, then a unique remaining subject -- otherwise abstain (FR-5).

    The venue lexicon is scanned first and its spans are *consumed*: no other role
    may claim an overlapping span.  A venue surface that is itself a gazetteer asset
    (Coinbase -> `eq:COIN`) is linked with `role = venue`, which never signals.
    """
    text = article.analysis_text
    venue_hits = scan_venues(text, datasets, within=primary.sentence)
    roles: dict[str, LinkRole] = {}
    venue_spans: dict[str, tuple[str, int, int]] = {}
    consumed: list[tuple[int, int]] = [(h.start, h.end) for h in venue_hits]
    for hit in venue_hits:
        if hit.asset_id is not None and hit.asset_id not in roles:
            roles[hit.asset_id] = LinkRole.venue
            venue_spans[hit.asset_id] = (article.id, hit.start, hit.end)

    remaining = [
        m
        for m in trigger_mentions
        if m.asset_id not in roles
        and not any(m.start < end and start < m.end for start, end in consumed)
    ]
    subjects = _distinct(remaining)
    if len(subjects) == 1:
        roles[subjects[0]] = LinkRole.subject
        return roles, [], venue_spans
    return roles, [NOTE_AMBIGUOUS_SUBJECT], venue_spans


def apply_links(
    extracted: ExtractedEvent,
    articles: dict[str, Article],
    datasets: Datasets,
) -> tuple[Event, list[EventLink]]:
    """Link one event and fold the abstention notes back onto it."""
    links, notes = link_event(extracted, articles, datasets)
    event = extracted.event
    if notes:
        event = event.model_copy(update={"notes": tuple([*event.notes, *notes])})
    return event, links


__all__ = [
    "ALIAS_PLAIN_CONFIDENCE",
    "ALIAS_WITH_CONTEXT_CONFIDENCE",
    "ALL_CAPS_CONFIDENCE",
    "NOTE_AMBIGUOUS_SUBJECT",
    "NOTE_MNA_ROLE_UNRESOLVED",
    "STRONG_CONFIDENCE",
    "TICKER_CONFIDENCE",
    "Mention",
    "VenueHit",
    "apply_links",
    "caps_ratio",
    "is_all_caps_sentence",
    "link_event",
    "scan_mentions",
    "scan_venues",
]
