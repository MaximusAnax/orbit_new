"""TickerPress eval runner — prints the scorecard, exits 0 iff every gate passes.

Hermetic by construction: committed fixtures, ``FixtureFeedSource`` over
``file://`` paths, offline notifiers, ``FixedClock("2026-03-02T13:00:00Z")``,
no network and no randomness in the pipeline.

    python evals/run.py                 # scorecard + JSON summary
    python evals/run.py --dump PATH     # one pipeline run, serialized (M5)
    python evals/run.py --write-golden  # refresh evals/fixtures/golden/*.sha256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tickerpress.adapters.clock import FixedClock  # noqa: E402
from tickerpress.adapters.feeds_fixture import FixtureFeedSource  # noqa: E402
from tickerpress.adapters.notify import ComposedMessage, DeliveryError  # noqa: E402
from tickerpress.engine import dedup  # noqa: E402
from tickerpress.engine.models import (  # noqa: E402
    Channel,
    DeliveryKind,
    DeliveryStatus,
    parse_iso_utc,
)
from tickerpress.resources import LEXICON_FILES, data_root, load_lexicons  # noqa: E402
from tickerpress.services import (  # noqa: E402
    DigestResult,
    NotifierRegistry,
    TickerPressService,
    load_watchlist,
)
from tickerpress.store import InMemoryRepository  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR))

from metrics import (  # noqa: E402
    EvalReport,
    Fixtures,
    MetricResult,
    clustering_scores,
    designed_trap_groups,
    f1_scores,
    file_sha256,
    lexicon_document_frequency,
    load_fixtures,
    naive_appearances,
    naive_story_of,
    relevance_ordering,
    u_amb_pairs,
)

NOW = "2026-03-02T13:00:00Z"
DIGEST_AT = parse_iso_utc("2026-03-02T13:05:00Z")
FIXTURES = EVALS_DIR / "fixtures"
GOLDEN = FIXTURES / "golden"

#: EVALS §5. Every threshold is asserted on a live-computed value.
GATES = {
    "M1_mention_f1": 0.92,
    "M1_amb": 0.85,
    "M1_amb_base": 0.82,
    "M1_amb_abl": 0.75,
    "M2_precision": 0.95,
    "M2_recall": 0.85,
    "M2_trap_merges": 0,
    "M2_near_band": 4,
    "M3_relevance_ordering": 0.90,
    "M3_cov_pairs": 55,
    "M3_cov_traps": 12,
    "M4_exactly_once": 1.0,
    "M5_determinism": 1.0,
    "M6_lex": 1.0,
}

#: EVALS §5 margin assertions: gate − live naive baseline.
NAIVE_MARGINS = {
    "M1_mention_f1": 0.10,
    "M1_amb": 0.10,
    "M1_amb_base": 0.10,
    "M1_amb_abl": 0.10,
    "M3_relevance_ordering": 0.10,
    "M2_recall": 0.50,
}

#: EVALS §4 asserted fixture floors — checked before any metric is computed.
FLOORS = {
    "archived_items": 108,
    "base_articles": 68,
    "positive_pairs": 88,
    "positive_pairs_base": 50,
    "u_amb": 58,
    "u_amb_base": 40,
    "u_amb_positives": 34,
    "u_amb_weak_only_positives": 26,
    "u_amb_negatives": 18,
    "cue_free_max": 5,
    "true_same_story_pairs": 72,
    "no_link_pairs": 15,
    "no_link_near": 5,
}


class RecordingNotifier:
    """Offline notifier used for every channel; optionally fails on demand."""

    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[ComposedMessage] = []
        self.fail = fail

    def deliver(self, message: ComposedMessage) -> None:
        if self.fail:
            raise DeliveryError("eval stub notifier refuses to deliver")
        self.messages.append(message)


@dataclass
class Run:
    """One end-to-end pipeline pass over the fixture corpus."""

    service: TickerPressService
    notifier: RecordingNotifier
    ingest_runs: list
    digest: DigestResult | None = None

    def close(self) -> None:
        self.service.repository.close()


def build_service(
    fixtures: Fixtures, *, ablate: bool = False, notifier: RecordingNotifier | None = None
) -> tuple[TickerPressService, RecordingNotifier]:
    companies = [dict(company) for company in fixtures.watchlist["companies"]]
    if ablate:
        for company in companies:
            company["context_terms"] = []
            company["anti_terms"] = []
    clock = FixedClock(NOW)
    recorder = notifier or RecordingNotifier()
    registry = NotifierRegistry(outbox_dir=Path(tempfile.mkdtemp(prefix="tickerpress-outbox-")))
    for channel in Channel:
        registry.register(channel, recorder)
    service = TickerPressService(
        InMemoryRepository(),
        clock=clock,
        feed_source=FixtureFeedSource(base_dir=FIXTURES / "feeds"),
        notifiers=registry,
        lexicons=load_lexicons(),
    )
    load_watchlist(service, companies, clock.now())
    for feed in fixtures.corpus["feeds"]:
        service.add_feed(feed["name"], f"file://{FIXTURES / 'feeds' / feed['file']}", now=clock.now())
    return service, recorder


def pipeline(
    fixtures: Fixtures,
    *,
    ablate: bool = False,
    alert_channel: Channel = Channel.FILE,
    notifier: RecordingNotifier | None = None,
) -> Run:
    service, recorder = build_service(fixtures, ablate=ablate, notifier=notifier)
    run = service.ingest(now=service.clock.now(), deliver_alerts=True, alert_channel=alert_channel)
    return Run(service=service, notifier=recorder, ingest_runs=[run])


# ---------------------------------------------------------------------------
# observations pulled out of a finished run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observations:
    article_ids: list[int]
    base_article_ids: list[int]
    texts: dict[int, str]
    canonical_urls: dict[int, str]
    story_of: dict[int, int]
    appearances: dict[tuple[int, str], int]
    predicted: set[tuple[int, str]]


def observe(run: Run) -> Observations:
    repository = run.service.repository
    articles = list(repository.iter_articles())
    texts = {
        int(article.id or 0): " ".join(
            part for part in (article.title, article.summary, article.content or "") if part
        )
        for article in articles
    }
    appearances = {
        (row.article_id, row.company_ticker): row.relevance for row in repository.list_appearances()
    }
    return Observations(
        article_ids=[int(article.id or 0) for article in articles],
        base_article_ids=[],
        texts=texts,
        canonical_urls={int(article.id or 0): article.canonical_url for article in articles},
        story_of={int(article.id or 0): article.story_id for article in articles},
        appearances=appearances,
        predicted=set(appearances),
    )


def check_floors(fixtures: Fixtures, observed: Observations) -> list[str]:
    """EVALS §4: a violation is an eval error, not a gate failure."""

    base_ids = set(fixtures.base_article_ids.values())
    universe = [(a, t) for a in observed.article_ids for t in fixtures.tickers]
    positives = [pair for pair in universe if fixtures.truth(*pair) != "absent"]
    subset = u_amb_pairs(observed.texts, fixtures.weak_surfaces)
    subset_positive = [pair for pair in subset if fixtures.truth(*pair) != "absent"]
    weak_only = [
        pair
        for pair in subset_positive
        if (fixtures.label_of(*pair) or {}).get("weak_only")
    ]
    cue_free = [row for row in fixtures.labels if row.get("cue_free")]
    problems: list[str] = []

    def require(name: str, value: int, floor: int, exact: bool = False) -> None:
        ok = value == floor if exact else value >= floor
        if not ok:
            problems.append(f"{name}: {value} (floor {'=' if exact else '>='} {floor})")

    require("archived items", len(observed.article_ids), FLOORS["archived_items"], exact=True)
    require("base articles", len(base_ids), FLOORS["base_articles"], exact=True)
    require("positive pairs", len(positives), FLOORS["positive_pairs"])
    require(
        "positive pairs (base)",
        len([p for p in positives if p[0] in base_ids]),
        FLOORS["positive_pairs_base"],
    )
    require("|U_amb|", len(subset), FLOORS["u_amb"])
    require("|U_amb ∩ base|", len([p for p in subset if p[0] in base_ids]), FLOORS["u_amb_base"])
    require("U_amb positives", len(subset_positive), FLOORS["u_amb_positives"])
    require("U_amb weak-only positives", len(weak_only), FLOORS["u_amb_weak_only_positives"])
    require(
        "U_amb negatives", len(subset) - len(subset_positive), FLOORS["u_amb_negatives"]
    )
    if len(cue_free) > FLOORS["cue_free_max"]:
        problems.append(f"cue_free positives: {len(cue_free)} (max {FLOORS['cue_free_max']})")
    require("no-link pairs", len(fixtures.no_link), FLOORS["no_link_pairs"], exact=True)
    require(
        "no-link near band",
        len([pair for pair in fixtures.no_link if pair["band"] == "near"]),
        FLOORS["no_link_near"],
        exact=True,
    )
    return problems


# ---------------------------------------------------------------------------
# M4 — the exactly-once scenario
# ---------------------------------------------------------------------------


def _digest_item_groups(fixtures: Fixtures, result: DigestResult, run: Run) -> set[tuple[str, int]]:
    return {
        (item.company_ticker, fixtures.story_groups[item.article_id])
        for item in result.items
    }


def _expected_set(fixtures: Fixtures, key: str) -> set[tuple[str, int]]:
    return {(row["ticker"], row["group_id"]) for row in fixtures.expected_digest[key]}


def scenario_exactly_once(fixtures: Fixtures) -> tuple[float, list[tuple[str, bool, str]]]:
    checks: list[tuple[str, bool, str]] = []

    # --- step 1: fresh store, ingest with file alerts, then the file digest ---
    run = pipeline(fixtures, alert_channel=Channel.FILE)
    alerts = [
        message for message in run.notifier.messages if message.kind is DeliveryKind.ALERT
    ]
    digest = run.service.run_digest(Channel.FILE, now=DIGEST_AT)
    run.digest = digest
    deliveries = run.service.repository.list_deliveries(channel=Channel.FILE)
    digests = [d for d in deliveries if d.kind is DeliveryKind.DIGEST]
    item_groups = _digest_item_groups(fixtures, digest, run)
    expected = _expected_set(fixtures, "expected")
    checks.append(
        (
            "1. one digest whose item set equals labels/expected_digest.json",
            len(digests) == 1 and item_groups == expected and bool(item_groups),
            f"{len(digests)} digest(s), {len(item_groups)} items, "
            f"missing={sorted(expected - item_groups)} extra={sorted(item_groups - expected)}",
        )
    )

    # --- step 3: the alert flow (checked on the same store) ---
    alerted = _expected_set(fixtures, "alerted")
    alert_pairs = {
        (item.ticker, fixtures.story_groups[item.article_id])
        for message in alerts
        for item in message.items
    }
    tsla_in_digest = {pair for pair in item_groups if pair[0] == "TSLA"}
    checks.append(
        (
            "3. alert fired once during ingest and is absent from that channel's digest",
            alert_pairs == alerted and len(alerts) == len(alerted) and not (alerted & tsla_in_digest),
            f"alerts={len(alerts)} pairs={sorted(alert_pairs)}",
        )
    )

    # --- step 2: re-ingest the same bytes, then digest again ---
    second = run.service.ingest(now=run.service.clock.now(), deliver_alerts=True, alert_channel=Channel.FILE)
    again = run.service.run_digest(Channel.FILE, now=DIGEST_AT)
    checks.append(
        (
            "2. re-ingest archives nothing and the next digest sends nothing",
            second.articles_new == 0
            and again.empty
            and len(run.service.repository.list_deliveries(channel=Channel.FILE)) == len(deliveries),
            f"articles_new={second.articles_new}, digest_empty={again.empty}",
        )
    )

    # --- step 5: the partial unique index is live -------------------------
    counted = [
        item
        for item in run.service.repository.list_delivery_items(channel=Channel.FILE)
        if item.counted
    ]
    violated = False
    if counted:
        sample = counted[0]
        try:
            with run.service.repository.transaction():
                run.service.repository._execute(
                    "INSERT INTO delivery_items (delivery_id, channel, company_ticker, story_id,"
                    " article_id, relevance, counted) VALUES (?,?,?,?,?,?,1)",
                    (
                        sample.delivery_id,
                        sample.channel.value,
                        sample.company_ticker,
                        sample.story_id,
                        sample.article_id,
                        sample.relevance,
                    ),
                )
        except sqlite3.IntegrityError:
            violated = True
    checks.append(
        (
            "5. a second counted ledger row for the same (channel, company, story) raises",
            violated,
            f"{len(counted)} counted items",
        )
    )
    run.close()

    # --- step 4: a failed send releases its stories -----------------------
    failing = pipeline(fixtures, alert_channel=Channel.FILE, notifier=RecordingNotifier(fail=True))
    failed = failing.service.run_digest(Channel.FILE, now=DIGEST_AT)
    failed_delivery = failed.delivery
    uncounted = [
        item
        for item in failing.service.repository.list_delivery_items(channel=Channel.FILE)
        if item.counted
    ]
    failing.service.notifiers.register(Channel.FILE, RecordingNotifier())
    retried = failing.service.run_digest(Channel.FILE, now=DIGEST_AT)
    retried_groups = _digest_item_groups(fixtures, retried, failing)
    third = failing.service.run_digest(Channel.FILE, now=DIGEST_AT)
    # The alert failed too, so its story was never counted and is re-eligible:
    # the retry legitimately carries the expected set *plus* the alerted pair.
    expected_after_failure = expected | _expected_set(fixtures, "alerted")
    checks.append(
        (
            "4. a failed send releases its stories; the retry delivers them once",
            failed_delivery is not None
            and failed_delivery.status is DeliveryStatus.FAILED
            and not uncounted
            and retried_groups == expected_after_failure
            and third.empty,
            f"failed={failed_delivery.status.value if failed_delivery else None}, "
            f"retried={len(retried_groups)} items, third_empty={third.empty}",
        )
    )
    failing.close()

    # --- step 6: a dry run is inert ---------------------------------------
    dry = pipeline(fixtures, alert_channel=Channel.FILE)
    before = {
        int(delivery.id or 0)
        for delivery in dry.service.repository.list_deliveries(channel=Channel.FILE)
    }
    dry_result = dry.service.run_digest(Channel.FILE, now=DIGEST_AT, dry_run=True)
    rows = [
        delivery
        for delivery in dry.service.repository.list_deliveries(channel=Channel.FILE)
        if int(delivery.id or 0) not in before
    ]
    items = [
        item
        for item in dry.service.repository.list_delivery_items(channel=Channel.FILE)
        if item.delivery_id not in before
    ]
    real = dry.service.run_digest(Channel.FILE, now=DIGEST_AT)
    checks.append(
        (
            "6. dry run renders a body, persists nothing, consumes nothing",
            bool(dry_result.body)
            and not rows
            and not items
            and _digest_item_groups(fixtures, real, dry) == expected,
            f"body={len(dry_result.body or '')} chars, rows={len(rows)}, items={len(items)}",
        )
    )
    dry.close()

    score = 1.0 if all(ok for _, ok, _ in checks) else 0.0
    return score, checks


# ---------------------------------------------------------------------------
# M5 — determinism across processes
# ---------------------------------------------------------------------------


def dump_state(fixtures: Fixtures) -> dict:
    """The serialized artifacts M5 compares (and the golden hashes cover)."""

    run = pipeline(fixtures, alert_channel=Channel.FILE)
    digest = run.service.run_digest(Channel.FILE, now=DIGEST_AT)
    repository = run.service.repository
    articles = [
        {
            "id": article.id,
            "feed_id": article.feed_id,
            "guid": article.item_guid,
            "canonical_url": article.canonical_url,
            "title": article.title,
            "story_id": article.story_id,
            "dedup_similarity": article.dedup_similarity,
            "content_token_count": article.content_token_count,
        }
        for article in repository.iter_articles()
    ]
    mentions = [
        {
            "article_id": mention.article_id,
            "ticker": mention.company_ticker,
            "field": mention.field.value,
            "start": mention.char_start,
            "surface": mention.surface,
            "matched_via": mention.matched_via.value,
            "score": mention.score,
            "accepted": mention.accepted,
            "features": mention.features,
        }
        for article in articles
        for mention in repository.list_mentions(int(article["id"]))
    ]
    appearances = [
        {
            "article_id": row.article_id,
            "ticker": row.company_ticker,
            "mention_count": row.mention_count,
            "title_hit": row.title_hit,
            "lede_hit": row.lede_hit,
            "relevance": row.relevance,
        }
        for row in repository.list_appearances()
    ]
    stories = [
        {
            "id": story.id,
            "first_published_at": story.first_published_at.isoformat(),
            "representative_article_id": story.representative_article_id,
        }
        for story in repository.list_stories()
    ]
    explains = []
    for article_id in (1, 40, 90):
        explanation = run.service.explain(article_id)
        explains.append(
            {
                "article_id": article_id,
                "story_id": explanation.story.id,
                "similarity": explanation.dedup_similarity,
                "candidates": [
                    {
                        "ticker": item.mention.company_ticker,
                        "surface": item.mention.surface,
                        "score": item.mention.score,
                        "accepted": item.mention.accepted,
                    }
                    for item in explanation.candidates
                ],
            }
        )
    bodies = [
        delivery.body_text
        for delivery in sorted(repository.list_deliveries(), key=lambda d: int(d.id or 0))
    ]
    payload = {
        "articles": articles,
        "stories": stories,
        "mentions": mentions,
        "appearances": appearances,
        "bodies": bodies,
        "explain": explains,
        "digest_body": digest.body,
    }
    run.close()
    return payload


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def golden_hashes(payload: dict) -> dict[str, str]:
    archive = {
        "articles": payload["articles"],
        "mentions": payload["mentions"],
        "appearances": payload["appearances"],
    }
    return {
        "digest_body": hashlib.sha256((payload["digest_body"] or "").encode()).hexdigest(),
        "archive_dump": hashlib.sha256(_canonical(archive).encode()).hexdigest(),
    }


def subprocess_dump(hash_seed: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        target = Path(handle.name)
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--dump", str(target)],
        check=True,
        env=env,
        capture_output=True,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    target.unlink(missing_ok=True)
    return payload


def determinism_check(write_golden: bool = False) -> tuple[float, list[str]]:
    notes: list[str] = []
    first = subprocess_dump("0")
    second = subprocess_dump("12345")
    identical = _canonical(first) == _canonical(second)
    notes.append(
        f"cross-process dumps (PYTHONHASHSEED 0 vs 12345): {'identical' if identical else 'DIFFER'}"
    )
    hashes = golden_hashes(first)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    golden_ok = True
    for name, digest in sorted(hashes.items()):
        path = GOLDEN / f"{name}.sha256"
        if write_golden or not path.exists():
            path.write_text(digest + "\n", encoding="utf-8")
            notes.append(f"golden {name}: written ({digest[:16]}…)")
            continue
        stored = path.read_text(encoding="utf-8").strip()
        if stored != digest:
            golden_ok = False
            notes.append(f"golden {name}: MISMATCH stored={stored[:16]}… live={digest[:16]}…")
        else:
            notes.append(f"golden {name}: matches ({digest[:16]}…)")
    return (1.0 if identical and golden_ok else 0.0), notes


# ---------------------------------------------------------------------------
# the evaluation itself
# ---------------------------------------------------------------------------


def evaluate(*, write_golden: bool = False) -> EvalReport:
    fixtures = load_fixtures(FIXTURES)
    report = EvalReport()

    main = pipeline(fixtures, alert_channel=Channel.FILE)
    observed = observe(main)
    problems = check_floors(fixtures, observed)
    if problems:
        raise SystemExit("fixture floors violated (EVALS §4):\n  " + "\n  ".join(problems))

    base_ids = set(fixtures.base_article_ids.values())
    universe = [(a, t) for a in observed.article_ids for t in fixtures.tickers]
    truth = {pair: fixtures.truth(*pair) for pair in universe}
    subset = u_amb_pairs(observed.texts, fixtures.weak_surfaces)
    subset_base = {pair for pair in subset if pair[0] in base_ids}

    # --- M1 family --------------------------------------------------------
    precision, recall, f1, tp, fp, fn = f1_scores(universe, truth, observed.predicted)
    report.add(
        MetricResult.at_least(
            "M1 mention_f1",
            f1,
            GATES["M1_mention_f1"],
            f"P={precision:.3f} R={recall:.3f} TP={tp} FP={fp} FN={fn}",
        )
    )
    amb = f1_scores(subset, truth, observed.predicted)
    report.add(
        MetricResult.at_least(
            "M1_amb ambiguous_f1",
            amb[2],
            GATES["M1_amb"],
            f"P={amb[0]:.3f} R={amb[1]:.3f} |U_amb|={len(subset)} TP={amb[3]} FP={amb[4]} FN={amb[5]}",
        )
    )
    amb_base = f1_scores(subset_base, truth, observed.predicted)
    report.add(
        MetricResult.at_least(
            "M1_amb_base",
            amb_base[2],
            GATES["M1_amb_base"],
            f"P={amb_base[0]:.3f} R={amb_base[1]:.3f} |U_amb∩base|={len(subset_base)}",
        )
    )

    ablated = pipeline(fixtures, ablate=True, alert_channel=Channel.CONSOLE)
    ablated_observed = observe(ablated)
    abl = f1_scores(subset, truth, ablated_observed.predicted)
    report.add(
        MetricResult.at_least(
            "M1_amb_abl (no terms)",
            abl[2],
            GATES["M1_amb_abl"],
            f"P={abl[0]:.3f} R={abl[1]:.3f} TP={abl[3]} FP={abl[4]} FN={abl[5]}",
        )
    )
    ablated.close()

    # --- M2 ---------------------------------------------------------------
    pairs = clustering_scores(observed.article_ids, fixtures.story_groups, observed.story_of)
    report.add(
        MetricResult.at_least(
            "M2 dedup P_pair",
            pairs.precision,
            GATES["M2_precision"],
            f"{pairs.correct}/{pairs.predicted} predicted pairs correct",
        )
    )
    report.add(
        MetricResult.at_least(
            "M2 dedup R_pair",
            pairs.recall,
            GATES["M2_recall"],
            f"{pairs.correct}/{pairs.true_pairs} true pairs recovered (F1={pairs.f1:.3f})",
        )
    )

    shingles = {
        article_id: dedup.shingles_from_texts(
            article.title, article.summary, article.content, main.service.lexicons
        )
        for article_id, article in (
            (int(a.id or 0), a) for a in main.service.repository.iter_articles()
        )
    }
    no_link_js: list[tuple[str, int, int, float, bool]] = []
    merged = 0
    near_ok = 0
    for pair in fixtures.no_link:
        left = fixtures.base_article_ids[pair["a"]]
        right = fixtures.base_article_ids[pair["b"]]
        similarity = dedup.jaccard(shingles[left], shingles[right])
        same = observed.story_of[left] == observed.story_of[right]
        merged += int(same)
        if pair["band"] == "near" and similarity >= 0.40:
            near_ok += 1
        no_link_js.append((pair["band"], pair["a"], pair["b"], similarity, same))
    report.add(
        MetricResult.at_most(
            "M2_trap merged no-link",
            merged,
            GATES["M2_trap_merges"],
            f"{len(fixtures.no_link)} hand-vetted pairs",
        )
    )
    report.add(
        MetricResult.at_least(
            "M2_near J>=0.40",
            near_ok,
            GATES["M2_near_band"],
            "of 5 near-band pairs, all still below τ=0.60",
        )
    )

    # --- M3 ---------------------------------------------------------------
    ordering = relevance_ordering(fixtures, observed.appearances)
    designed = designed_trap_groups(fixtures)
    coverage_ok = ordering.pairs >= GATES["M3_cov_pairs"] and designed <= ordering.trap_groups_present
    report.add(
        MetricResult.at_least(
            "M3 relevance_ordering",
            ordering.value if coverage_ok else 0.0,
            GATES["M3_relevance_ordering"],
            f"|O|={ordering.pairs}, {len(ordering.inversions)} mis-ordered"
            + ("" if coverage_ok else " — FAILED by M3_cov"),
        )
    )
    report.add(
        MetricResult.at_least("M3_cov |O|", ordering.pairs, GATES["M3_cov_pairs"], "observed pairs")
    )
    report.add(
        MetricResult.at_least(
            "M3_cov traps in O",
            len(designed & ordering.trap_groups_present),
            GATES["M3_cov_traps"],
            f"designed={sorted(designed)}",
        )
    )

    # --- M4, M5, M6 -------------------------------------------------------
    m4, checks = scenario_exactly_once(fixtures)
    report.add(
        MetricResult.at_least(
            "M4 delivery_exactly_once",
            m4,
            GATES["M4_exactly_once"],
            "; ".join(name for name, ok, _ in checks if not ok) or "all six checks pass",
        )
    )

    m5, m5_notes = determinism_check(write_golden=write_golden)
    report.add(MetricResult.at_least("M5 determinism", m5, GATES["M5_determinism"], "; ".join(m5_notes)))

    lexicons = load_lexicons()
    manifest_ok = True
    for file_name in sorted(set(LEXICON_FILES.values())):
        digest = file_sha256(data_root() / file_name)
        if fixtures.lexicon_manifest["sha256"].get(file_name) != digest:
            manifest_ok = False
    base_texts = {
        article_id: observed.texts[article_id]
        for article_id in observed.article_ids
        if article_id in base_ids
    }
    frequency = lexicon_document_frequency(
        sorted(lexicons.corporate_cues | lexicons.anti_cues), base_texts
    )
    singletons = sorted(entry for entry, count in frequency.items() if count == 1)
    report.add(
        MetricResult.at_least(
            "M6_lex specificity",
            1.0 if manifest_ok and not singletons else 0.0,
            GATES["M6_lex"],
            f"manifest_ok={manifest_ok}, singletons={singletons[:8] or 'none'}"
            + (f" (+{len(singletons) - 8} more)" if len(singletons) > 8 else ""),
        )
    )

    # --- naive baseline, margins, report-only outputs ----------------------
    naive_counts = naive_appearances(fixtures, observed.texts)
    naive_predicted = set(naive_counts)
    naive_story = naive_story_of(observed.canonical_urls)
    naive_pairs = clustering_scores(observed.article_ids, fixtures.story_groups, naive_story)
    naive_ordering = relevance_ordering(fixtures, naive_counts)
    naive = {
        "M1 mention_f1": f1_scores(universe, truth, naive_predicted)[2],
        "M1_amb ambiguous_f1": f1_scores(subset, truth, naive_predicted)[2],
        "M1_amb_base": f1_scores(subset_base, truth, naive_predicted)[2],
        "M1_amb_abl (no terms)": f1_scores(subset, truth, naive_predicted)[2],
        "M2 dedup P_pair": naive_pairs.precision,
        "M2 dedup R_pair": naive_pairs.recall,
        "M3 relevance_ordering": naive_ordering.value,
    }
    margins = {
        "M1 mention_f1": NAIVE_MARGINS["M1_mention_f1"],
        "M1_amb ambiguous_f1": NAIVE_MARGINS["M1_amb"],
        "M1_amb_base": NAIVE_MARGINS["M1_amb_base"],
        "M1_amb_abl (no terms)": NAIVE_MARGINS["M1_amb_abl"],
        "M3 relevance_ordering": NAIVE_MARGINS["M3_relevance_ordering"],
        "M2 dedup R_pair": NAIVE_MARGINS["M2_recall"],
    }
    for name, required in sorted(margins.items()):
        gate = report.by_name(name).gate
        report.add(
            MetricResult.at_least(
                f"margin {name}",
                gate - naive[name],
                required,
                f"gate {gate:.2f} − naive {naive[name]:.3f}",
            )
        )

    report.extras["naive"] = {name: round(value, 4) for name, value in naive.items()}
    report.extras["naive_deltas"] = {
        name: round(report.by_name(name).value - value, 4) for name, value in naive.items()
    }
    report.extras["fixture_counts"] = {
        "archived_items": len(observed.article_ids),
        "base_articles": len(base_ids),
        "positive_pairs": sum(1 for pair in universe if truth[pair] != "absent"),
        "u_amb": len(subset),
        "u_amb_base": len(subset_base),
        "true_same_story_pairs": pairs.true_pairs,
    }
    report.extras["m4_checks"] = [
        {"check": name, "passed": ok, "detail": detail} for name, ok, detail in checks
    ]
    report.extras["no_link_similarity"] = [
        {"band": band, "a": a, "b": b, "J": round(j, 4), "merged": same}
        for band, a, b, j, same in no_link_js
    ]

    # per-company precision/recall table (report only)
    per_company = {}
    for ticker in fixtures.tickers:
        company_pairs = [pair for pair in universe if pair[1] == ticker]
        p, r, cf1, ctp, cfp, cfn = f1_scores(company_pairs, truth, observed.predicted)
        per_company[ticker] = {
            "precision": round(p, 3),
            "recall": round(r, 3),
            "f1": round(cf1, 3),
            "tp": ctp,
            "fp": cfp,
            "fn": cfn,
        }
    report.extras["per_company"] = per_company
    report.extras["m3_strict"] = round(_m3_strict(fixtures, observed), 4)
    report.extras["weak_surface_drift"] = _weak_surface_drift(fixtures, lexicons)
    report.extras["base_level_m1"] = round(
        f1_scores([(a, t) for a in sorted(base_ids) for t in fixtures.tickers], truth, observed.predicted)[2],
        4,
    )
    main.close()
    return report


def _m3_strict(fixtures: Fixtures, observed: Observations) -> float:
    """Report-only: the same score over *label* pairs, missing appearance = 0."""

    total = 0
    credit = 0.0
    by_ticker: dict[str, list[int]] = {}
    for article_id in observed.article_ids:
        for ticker in fixtures.tickers:
            if fixtures.truth(article_id, ticker) != "absent":
                by_ticker.setdefault(ticker, []).append(article_id)
    for ticker, ids in sorted(by_ticker.items()):
        for left in ids:
            for right in ids:
                if left >= right:
                    continue
                from metrics import TIER_ORDER

                tier_left = TIER_ORDER[fixtures.truth(left, ticker)]
                tier_right = TIER_ORDER[fixtures.truth(right, ticker)]
                if tier_left == tier_right:
                    continue
                high, low = (left, right) if tier_left > tier_right else (right, left)
                total += 1
                high_rel = observed.appearances.get((high, ticker))
                low_rel = observed.appearances.get((low, ticker))
                if high_rel is None or low_rel is None:
                    continue
                credit += 1.0 if high_rel > low_rel else (0.5 if high_rel == low_rel else 0.0)
    return credit / total if total else 0.0


def _weak_surface_drift(fixtures: Fixtures, lexicons) -> dict:
    """Report-only diff between the frozen inventory and the live lexicon."""

    frozen_collisions = {
        ticker
        for ticker, surfaces in fixtures.weak_surfaces.items()
        if ticker in surfaces
    }
    live_collisions = {
        ticker for ticker in fixtures.tickers if ticker.casefold() in lexicons.common_words
    }
    return {
        "frozen_collision_tickers": sorted(frozen_collisions),
        "live_collision_tickers": sorted(live_collisions),
        "drift": sorted(frozen_collisions ^ live_collisions),
    }


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def print_report(report: EvalReport) -> None:
    print("=" * 96)
    print("TickerPress — eval scorecard   (fixtures: 108 archived items, 68 base, 12 companies)")
    print("=" * 96)
    for metric in report.metrics:
        print(metric.line())
    print("-" * 96)
    counts = report.extras["fixture_counts"]
    print(
        "  fixture counts: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    print("  naive baseline (live): " + ", ".join(
        f"{name.split()[0]}={value:.3f}" for name, value in sorted(report.extras["naive"].items())
    ))
    print("  naive deltas:          " + ", ".join(
        f"{name.split()[0]}={value:+.3f}"
        for name, value in sorted(report.extras["naive_deltas"].items())
    ))
    print(f"  base-level M1 (report only): {report.extras['base_level_m1']:.3f}")
    print(f"  M3_strict (report only):     {report.extras['m3_strict']:.3f}")
    print(f"  weak-surface drift:          {report.extras['weak_surface_drift']['drift'] or 'none'}")
    print("  per-company precision/recall:")
    for ticker, row in sorted(report.extras["per_company"].items()):
        print(
            f"    {ticker:<6} P={row['precision']:.3f} R={row['recall']:.3f} "
            f"F1={row['f1']:.3f}  TP={row['tp']:>3} FP={row['fp']:>2} FN={row['fn']:>2}"
        )
    print("  M4 scenario:")
    for check in report.extras["m4_checks"]:
        print(f"    [{'PASS' if check['passed'] else 'FAIL'}] {check['check']} — {check['detail']}")
    print("  no-link pair resemblance (τ = 0.60):")
    for row in report.extras["no_link_similarity"]:
        print(
            f"    {row['band']:<5} base {row['a']:>3}–{row['b']:<3} J={row['J']:.3f} "
            f"merged={row['merged']}"
        )
    print("-" * 96)
    print(json.dumps(report.as_json()["metrics"], indent=2))
    print("=" * 96)
    print("RESULT:", "PASS" if report.passed else "FAIL")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the TickerPress eval suite.")
    parser.add_argument("--dump", type=Path, help="write one serialized pipeline run and exit")
    parser.add_argument("--write-golden", action="store_true", help="refresh the golden hashes")
    parser.add_argument("--json", action="store_true", help="print only the JSON summary")
    args = parser.parse_args(argv)

    if args.dump is not None:
        payload = dump_state(load_fixtures(FIXTURES))
        args.dump.write_text(_canonical(payload), encoding="utf-8")
        return 0

    report = evaluate(write_golden=args.write_golden)
    if args.json:
        print(json.dumps(report.as_json(), indent=2))
    else:
        print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
