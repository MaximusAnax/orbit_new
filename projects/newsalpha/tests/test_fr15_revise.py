"""FR-15: ingest-run semantics, signal revisions, key continuity and supersession."""

from __future__ import annotations

from newsalpha.engine import pipeline
from newsalpha.engine.revise import latest_revisions, signal_key, superseded_keys
from newsalpha.models import Stage
from newsalpha_testkit import raw

AS_OF_1 = "2026-03-11T07:00:00Z"
AS_OF_2 = "2026-03-12T07:00:00Z"
AS_OF_3 = "2026-03-16T07:00:00Z"

STORY = (
    "The Aave protocol was exploited in a bridge hack that drained $47 million from the "
    "cross-chain bridge early on Saturday, the team said in a short statement to users."
)


def hack(external_id, domain, published_at, extra=""):
    return raw(
        external_id,
        "Aave bridge hack drains $47 million",
        STORY + extra,
        domain=domain,
        published_at=published_at,
    )


def test_fr15_signal_key_is_content_derived():
    assert signal_key("mna", "eq:DDOG", "target", "2026-03-10") == signal_key(
        "mna", "eq:DDOG", "target", "2026-03-10"
    )
    assert signal_key("mna", "eq:DDOG", "target", "2026-03-10") != signal_key(
        "mna", "eq:DDOG", "acquirer", "2026-03-10"
    )


def test_fr15_first_run_inserts_revision_one(datasets):
    _, result = pipeline.ingest(
        [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")], datasets, as_of=AS_OF_1
    )
    assert [s.revision for s in result.new_signals] == [1]
    assert result.new_signals[0].supersedes is None


def test_fr15_reingest_with_no_new_articles_writes_nothing(datasets):
    rows = [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")]
    fresh, first = pipeline.ingest(rows, datasets, as_of=AS_OF_1)
    _, second = pipeline.ingest(
        rows,
        datasets,
        as_of=AS_OF_1,
        existing_articles=fresh,
        existing_signals=list(first.new_signals),
    )
    assert second.new_signals == ()
    assert second.briefs == ()
    # The derived view is unchanged, byte for byte.
    assert [e.model_dump() for e in first.events] == [e.model_dump() for e in second.events]


def test_fr15_corroboration_emits_revision(datasets):
    """US-3: a second outlet raises confidence through a *new revision*."""
    day1 = [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")]
    fresh1, first = pipeline.ingest(day1, datasets, as_of=AS_OF_1)

    day2 = [
        hack("b", "theblock.example", "2026-03-10T18:00:00Z", extra=" Users were told to wait.")
    ]
    _, second = pipeline.ingest(
        day2,
        datasets,
        as_of=AS_OF_2,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )

    assert len(second.new_signals) == 1
    revision = second.new_signals[0]
    assert revision.revision == 2
    assert revision.supersedes == first.new_signals[0].id
    assert revision.signal_key == first.new_signals[0].signal_key
    assert revision.confidence > first.new_signals[0].confidence
    assert "mod:corroboration=2" in revision.rationale_codes
    # The earlier revision is retained unmodified, for backtest honesty.
    assert first.new_signals[0].confidence != revision.confidence


def test_fr15_previous_revision_is_never_mutated(datasets):
    day1 = [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")]
    fresh1, first = pipeline.ingest(day1, datasets, as_of=AS_OF_1)
    before = first.new_signals[0].model_dump()
    pipeline.ingest(
        [hack("b", "theblock.example", "2026-03-10T18:00:00Z", extra=" More detail.")],
        datasets,
        as_of=AS_OF_2,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    assert first.new_signals[0].model_dump() == before


def test_fr15_observed_at_anchors_on_the_latest_evidence(datasets):
    day1 = [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")]
    fresh1, first = pipeline.ingest(day1, datasets, as_of=AS_OF_1)
    _, second = pipeline.ingest(
        [hack("b", "theblock.example", "2026-03-10T18:00:00Z", extra=" More detail.")],
        datasets,
        as_of=AS_OF_2,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    assert first.new_signals[0].observed_at == "2026-03-10T08:00:00Z"
    assert second.new_signals[0].observed_at == "2026-03-10T18:00:00Z"


def test_fr15_key_continuity_alias(datasets):
    """A late, earlier-dated article shifts event_date by a day: same key, new revision."""
    late_but_earlier = hack("z", "reuters.example", "2026-03-09T22:00:00Z", extra=" Filed late.")
    fresh1, first = pipeline.ingest(
        [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")], datasets, as_of=AS_OF_1
    )
    _, second = pipeline.ingest(
        [late_but_earlier],
        datasets,
        as_of=AS_OF_2,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    assert second.new_aliases, "an aliased key must be recorded"
    alias = second.new_aliases[0]
    assert alias.to_key == first.new_signals[0].signal_key
    assert second.new_signals[0].signal_key == first.new_signals[0].signal_key
    assert second.new_signals[0].revision == 2


def test_fr15_supersession_records_the_older_key(datasets):
    """A denial three days after a rumor demotes it -- across clusters, not within one."""
    rumor = raw(
        "r",
        "Microsoft in talks to acquire Datadog",
        "Microsoft is reportedly in talks to acquire Datadog, sources say.",
        published_at="2026-03-10T08:00:00Z",
    )
    denial = raw(
        "d",
        "Microsoft denies Datadog talks",
        "Microsoft denied it is in talks to acquire Datadog, the company said on Friday.",
        domain="cnbc.example",
        published_at="2026-03-14T08:00:00Z",
    )
    fresh1, first = pipeline.ingest([rumor], datasets, as_of=AS_OF_1)
    _, second = pipeline.ingest(
        [denial],
        datasets,
        as_of=AS_OF_3,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    rumor_target = next(s for s in first.new_signals if s.asset_id == "eq:DDOG")
    denial_target = next(s for s in second.new_signals if s.asset_id == "eq:DDOG")
    assert rumor_target.event_snapshot.stage is Stage.rumored
    assert denial_target.event_snapshot.stage is Stage.denied
    assert denial_target.supersedes_key == rumor_target.signal_key
    assert superseded_keys([*first.new_signals, *second.new_signals]) == {rumor_target.signal_key}


def test_fr15_latest_revisions_picks_max_revision(datasets):
    day1 = [hack("a", "coindesk.example", "2026-03-10T08:00:00Z")]
    fresh1, first = pipeline.ingest(day1, datasets, as_of=AS_OF_1)
    _, second = pipeline.ingest(
        [hack("b", "theblock.example", "2026-03-10T18:00:00Z", extra=" More detail.")],
        datasets,
        as_of=AS_OF_2,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    everything = [*first.new_signals, *second.new_signals]
    latest = latest_revisions(everything)
    assert len(latest) == 1
    assert latest[0].revision == 2


def test_fr15_articles_outside_the_active_window_take_no_part(datasets):
    old = hack("old", "coindesk.example", "2026-01-01T08:00:00Z")
    fresh, result = pipeline.ingest([old], datasets, as_of=AS_OF_3)
    assert fresh[0].excluded_from_analysis is True
    assert result.clusters == ()
    assert result.new_signals == ()
