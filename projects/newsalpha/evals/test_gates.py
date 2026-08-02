"""Gate assertions for the NewsAlpha eval suite (EVALS.md "Naive baselines and gates").

One test per gate, named after the FR it defends.  A gate failing here fails
`uv run pytest newsalpha/`, which is the point: the thresholds are enforced, not
advisory.  The same numbers are printed by `evals/run.py`.

`metrics.py` is loaded by path under a unique module name so that several
projects' eval suites can share one workspace-wide pytest run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent


def _load_metrics() -> Any:
    spec = importlib.util.spec_from_file_location("newsalpha_eval_metrics", HERE / "metrics.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = sys.modules.get("newsalpha_eval_metrics") or _load_metrics()


# --------------------------------------------------------------------------- #
# M1 -- extraction (FR-4)
# --------------------------------------------------------------------------- #


def test_gate_m1a_event_typing_fr4():
    score = M.m1a("val").macro_f1
    assert score >= 0.80, f"M1a macro-F1 on VAL is {score:.4f}, gate >= 0.80"


def test_gate_m1a_floor_fr4():
    floor = M.m1a("val").floor
    assert floor >= 0.65, f"M1a per-type floor is {floor:.4f}, gate >= 0.65"


def test_gate_m1a_transfer_fr4():
    gap = M.m1a("dev").macro_f1 - M.m1a("val").macro_f1
    assert gap <= 0.10, f"M1a transfer gap is {gap:.4f}, gate <= 0.10 (fixture overfitting)"


def test_gate_m1b_attributes_conditional_fr4():
    conditional, _unconditional, failures = M.m1b_m1c("val")
    assert conditional >= 0.85, f"M1b is {conditional:.4f}, gate >= 0.85; failures={failures[:3]}"


def test_gate_m1c_attributes_unconditional_fr4():
    _conditional, unconditional, failures = M.m1b_m1c("val")
    assert unconditional >= 0.70, f"M1c is {unconditional:.4f}, gate >= 0.70; {failures[:3]}"


# --------------------------------------------------------------------------- #
# M2 / M3 -- linking and roles (FR-5)
# --------------------------------------------------------------------------- #


def test_gate_m2a_link_f1_fr5():
    score, counts = M.m2a("val")
    assert score >= 0.85, f"M2a link F1 is {score:.4f}, gate >= 0.85; {counts}"


def test_gate_m2b_link_traps_fr5():
    score, wrong = M.m2b()
    assert score >= 0.90, f"M2b trap accuracy is {score:.4f}, gate >= 0.90; wrong={wrong[:3]}"


def test_gate_m3_roles_fr5():
    score, wrong = M.m3()
    assert score >= 0.85, f"M3 role accuracy is {score:.4f}, gate >= 0.85; wrong={wrong[:3]}"


# --------------------------------------------------------------------------- #
# M4 - M7 -- signal quality (FR-6 / FR-10)
# --------------------------------------------------------------------------- #


def test_gate_m4_hit_rate_fr6_fr10():
    score, per_seed = M.m4()
    assert score >= 0.72, f"M4 hit rate is {score:.4f}, gate >= 0.72; per seed {per_seed}"


def test_gate_m4_transfer_fr6():
    gap, dev, val = M.m4_transfer()
    assert gap <= 0.10, (
        f"M4 transfer gap is {gap:.4f} (DEV {dev:.4f} / VAL {val:.4f}), gate <= 0.10"
    )


def test_gate_m5_ic_fr6():
    score, per_seed = M.m5()
    assert score >= 0.35, f"M5 IC is {score:.4f}, gate >= 0.35; per seed {per_seed}"


def test_gate_m6_calibration_fr6():
    score, per_seed, _occupancy = M.m6()
    assert score >= 0.12, f"M6 separation is {score:.4f}, gate >= 0.12; per seed {per_seed}"


def test_gate_m6_bucket_occupancy_fr6():
    _score, _per_seed, occupancy = M.m6()
    assert min(occupancy.values()) >= 20, (
        f"M6 bucket occupancy is {occupancy}, gate >= 20 per bucket -- a confidence "
        "function that refuses to spread mass across buckets is itself a calibration failure"
    )


def test_gate_m7_placebo_fr10():
    m7a, m7b, hits, ics = M.m7()
    assert len(hits) == len(M.market_seeds()) * len(M.PLACEBO_SEEDS) == len(ics)
    assert m7a <= 0.035, f"M7a placebo |mean hit - 0.5| is {m7a:.4f}, gate <= 0.035"
    assert m7b <= 0.06, f"M7b placebo |mean IC| is {m7b:.4f}, gate <= 0.06"


def test_gate_announcement_bar_capture_fr10():
    """The planted announcement jump must never fall inside an evaluated window."""
    rate, captured, considered = M.announcement_capture_rate()
    assert considered > 0
    assert rate == 0.0, f"{captured} of {considered} windows captured an announcement bar"


# --------------------------------------------------------------------------- #
# M8 -- framing safeguard (FR-7)
# --------------------------------------------------------------------------- #


def test_gate_m8_framing_fr7():
    score, outcomes = M.m8()
    wrong = [o for o in outcomes if not o.correct]
    assert score == 1.0, f"M8 verdict accuracy is {score:.4f}, gate = 1.0; wrong={wrong[:3]}"


def test_gate_m8_lexicon_superset_fr7():
    shipped = {term.casefold() for term in M.datasets().lexicons.forbidden_lexicon}
    missing = [t for t in M.REFERENCE_FORBIDDEN_LEXICON if t.casefold() not in shipped]
    assert not missing, f"patterns.json.forbidden_lexicon is missing {missing}"


# --------------------------------------------------------------------------- #
# G1 / G2 -- denominator integrity (FR-10)
# --------------------------------------------------------------------------- #


def test_gate_g1_n_directional_fr10():
    assert M.g1() >= 100, f"G1 N_directional is {M.g1()}, gate >= 100 (abstention dodge)"


def test_gate_g2_exclusion_rate_fr10():
    rate, reasons = M.g2()
    assert rate <= 0.05, f"G2 exclusion rate is {rate:.4f}, gate <= 0.05; by reason {reasons}"


def test_gate_expected_abstentions_are_the_documented_seven_fr5_fr6():
    expected, other, notes = M.abstentions()
    assert expected == 7, (
        f"expected abstentions {expected} != 7 (4 symmetric M&A + 3 denied): {notes}"
    )
    assert other == 0, f"unexpected abstentions: {notes}"


# --------------------------------------------------------------------------- #
# Baseline sanity -- a gate that a shortcut also clears is not a gate
# --------------------------------------------------------------------------- #


def test_gates_sit_meaningfully_above_their_naive_baselines():
    assert M.naive_m1a("val")[0] < 0.80
    assert M.naive_m1b_m1c("val")[0] < 0.85
    assert M.naive_m2a("val") < 0.85
    assert M.naive_m2b() < 0.90
    assert M.naive_m3() < 0.85
    assert M.naive_m4() < 0.72
    assert M.naive_m5() < 0.35
    naive_m6, naive_occupancy = M.naive_m6()
    assert naive_m6 < 0.12 or min(naive_occupancy.values()) < 20
    naive_m7a, naive_m7b = M.naive_m7()
    assert naive_m7a > 0.035 and naive_m7b > 0.06
    assert M.naive_m8() < 1.0
    assert M.naive_g1() < 100
    assert M.naive_g2() > 0.05


# --------------------------------------------------------------------------- #
# D0 / D1 -- determinism and replay equivalence on the committed corpus (FR-14)
# --------------------------------------------------------------------------- #


def _canonical(rows: list[Any]) -> str:
    from newsalpha.engine.normalize import canonical_json

    return canonical_json([row.model_dump(mode="json") for row in rows])


def _ingest(raws, as_of, *, articles=None, signals=None, aliases=None):
    from newsalpha.engine import pipeline

    return pipeline.ingest(
        list(raws),
        M.datasets(),
        as_of,
        existing_articles=list(articles or []),
        existing_signals=list(signals or []),
        existing_aliases=list(aliases or []),
    )


def test_fr14_d0_determinism_on_the_eval_corpus():
    """Two runs over the same corpus produce byte-identical canonical exports."""
    from newsalpha.adapters.newsfeed_fixture import FixtureNewsFeed
    from newsalpha.store import InMemoryRepository

    raws = FixtureNewsFeed(M.FIXTURES / "articles.jsonl").load()
    as_of = M.corpus().as_of
    exports = []
    for _ in range(2):
        repository = InMemoryRepository()
        repository.initialize()
        articles, result = _ingest(raws, as_of)
        repository.add_articles(articles)
        repository.replace_derived(list(result.clusters), list(result.events), list(result.links))
        repository.add_signals(list(result.new_signals))
        repository.add_briefs(list(result.briefs))
        exports.append(
            "\n".join(
                _canonical(rows)
                for rows in (
                    repository.list_articles(),
                    repository.list_clusters(),
                    repository.list_events(),
                    repository.list_links(),
                    repository.list_signals(),
                    [repository.get_brief(s.id) for s in repository.list_signals()],
                )
            )
        )
    assert exports[0] == exports[1]


def test_fr14_d0_reingest_writes_no_new_revisions():
    from newsalpha.adapters.newsfeed_fixture import FixtureNewsFeed

    raws = FixtureNewsFeed(M.FIXTURES / "articles.jsonl").load()
    as_of = M.corpus().as_of
    articles, first = _ingest(raws, as_of)
    _again, second = _ingest(
        raws, as_of, articles=articles, signals=first.new_signals, aliases=first.new_aliases
    )
    assert second.new_signals == ()
    assert second.new_aliases == ()


def test_fr14_d1_replay_equivalence_on_the_eval_corpus():
    """One batch vs five chronological arrival batches: identical derived state."""
    from newsalpha.adapters.newsfeed_fixture import FixtureNewsFeed
    from newsalpha.engine.revise import latest_revisions

    truth = M.corpus().truth
    raws = FixtureNewsFeed(M.FIXTURES / "articles.jsonl").load()
    as_of = truth["as_of"]
    batches: dict[int, list[Any]] = {}
    for raw in raws:
        batches.setdefault(truth["arrival_batches"][raw.external_id], []).append(raw)
    assert len(batches) == 5

    _articles, batch_result = _ingest(raws, as_of)

    stored: list[Any] = []
    signals: list[Any] = []
    aliases: list[Any] = []
    incremental = None
    for index in sorted(batches):
        window = [raw for k in sorted(batches) if k <= index for raw in batches[k]]
        run_as_of = max(raw.published_at for raw in window) if index < 4 else as_of
        fresh, incremental = _ingest(
            batches[index], run_as_of, articles=stored, signals=signals, aliases=aliases
        )
        stored.extend(fresh)
        signals.extend(incremental.new_signals)
        aliases.extend(incremental.new_aliases)

    assert incremental is not None
    assert _canonical(list(batch_result.clusters)) == _canonical(list(incremental.clusters))
    assert _canonical(list(batch_result.events)) == _canonical(list(incremental.events))
    assert _canonical(list(batch_result.links)) == _canonical(list(incremental.links))

    def scored(rows):
        out = {}
        for signal in latest_revisions(list(rows)):
            key = (
                signal.event_snapshot.event_type.value,
                signal.asset_id,
                signal.role.value,
            )
            out[key] = signal.scored_tuple()
        return out

    assert scored(batch_result.new_signals) == scored(signals)


def test_fr15_incremental_corroboration_raises_confidence_via_a_revision():
    """A late-arriving cluster mate must append a revision, never mutate one."""
    from newsalpha.adapters.newsfeed_fixture import FixtureNewsFeed
    from newsalpha.engine.revise import latest_revisions

    truth = M.corpus().truth
    raws = {raw.external_id: raw for raw in FixtureNewsFeed(M.FIXTURES / "articles.jsonl").load()}
    multi = next(
        event
        for event in truth["events"]
        if len(event["article_external_ids"]) == 3 and event["links"][0]["role"] != "mentioned"
    )
    first_id, *rest = multi["article_external_ids"]
    early = [raws[first_id]]
    later = [raws[external_id] for external_id in rest]

    fresh, first = _ingest(early, raws[first_id].published_at)
    assert first.new_signals
    before = latest_revisions(list(first.new_signals))[0]

    _more, second = _ingest(
        later,
        max(raw.published_at for raw in later),
        articles=fresh,
        signals=first.new_signals,
        aliases=first.new_aliases,
    )
    revisions = [s for s in second.new_signals if s.signal_key == before.signal_key]
    assert revisions, "a late cluster mate produced no revision"
    assert revisions[0].revision == before.revision + 1
    assert revisions[0].supersedes == before.id
    assert revisions[0].confidence > before.confidence


# --------------------------------------------------------------------------- #
# Fixture integrity & hermeticity
# --------------------------------------------------------------------------- #


def test_fixture_composition_matches_evals_md():
    counts = M.corpus().truth["counts"]
    assert counts["events"] == 108
    assert counts["events_by_family"] == {"dev": 32, "val": 76}
    assert counts["trap_mentions"] == 50
    assert counts["trap_link"] == 22
    assert counts["trap_no_link"] == 28
    assert counts["role_decisions"] == 40
    assert len(M.corpus().frame_cases["cases"]) == 16


def test_fixture_cluster_jaccard_margins_are_committed():
    margins = M.corpus().truth["jaccard_margins"]
    assert margins["intra_min"] >= 0.72, margins
    assert margins["inter_max"] <= 0.45, margins


def test_fixture_families_use_disjoint_trigger_ngrams():
    disjointness = M.corpus().truth["disjointness"]
    assert disjointness["shared_trigger_ngrams"] == 0
    assert disjointness["dev_trigger_ngrams"] > 0
    assert disjointness["val_trigger_ngrams"] > 0


def test_market_truth_is_independent_of_the_shipped_priors():
    """The planted table is literature-scaled and must not be a copy of priors.json."""
    planted = M.corpus().market_truth["planted_table"]
    priors = M.datasets().priors
    assert planted, "no planted-effect table"
    identical = 0
    for key, row in planted.items():
        event_type, role, kind, polarity = key.split("|")
        prior = M.datasets().resolve_prior(
            f"{event_type}.{role}.{polarity if polarity != '*' else '*'}",
            kind if kind != "*" else "equity",
        )
        if prior is not None and abs(prior.mid_expected_ar - row["post_entry_mean"]) < 1e-12:
            identical += 1
    assert len(priors) > 0
    assert identical < len(planted), (
        "every planted effect equals its prior's midpoint exactly -- the market ground "
        "truth is not independent of the table it grades"
    )


def test_hermetic_no_live_adapters_after_a_full_eval_run():
    for module in (
        "feedparser",
        "newsalpha.adapters.newsfeed_rss",
        "newsalpha.adapters.marketdata_live",
    ):
        sys.modules.pop(module, None)
    assert M.m1a("val").macro_f1 >= 0.80
    assert M.m4()[0] >= 0.72
    for module in (
        "feedparser",
        "newsalpha.adapters.newsfeed_rss",
        "newsalpha.adapters.marketdata_live",
    ):
        assert module not in sys.modules, module


def test_eval_conftest_blocks_the_network():
    import socket

    with pytest.raises(RuntimeError):
        socket.socket()


def test_fixture_files_are_committed_and_readable():
    for name in ("articles.jsonl", "articles_truth.json", "market_truth.json", "frame_cases.json"):
        path = M.FIXTURES / name
        assert path.exists(), path
    assert (M.FIXTURES / "gapped" / "eq_AAPL.csv").exists()
    for seed in M.market_seeds():
        assert (M.FIXTURES / "market" / f"seed_{seed}" / "idx_US.csv").exists()
    truth = json.loads((M.FIXTURES / "articles_truth.json").read_text(encoding="utf-8"))
    assert truth["seed"] == 20260731
