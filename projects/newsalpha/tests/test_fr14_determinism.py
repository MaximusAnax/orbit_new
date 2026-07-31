"""FR-14: D0 determinism and D1 replay equivalence."""

from __future__ import annotations

from newsalpha.engine import pipeline
from newsalpha.engine.normalize import canonical_json
from newsalpha_testkit import raw

AS_OF = "2026-03-20T07:00:00Z"

HACK = (
    "The Aave protocol was exploited in a bridge hack that drained $47 million from the "
    "cross-chain bridge early on Saturday, the team said in a statement to holders."
)
HACK_FOLLOW_UP = HACK + " A post-mortem is expected within days."


def corpus():
    """Five days of arrivals, including one out-of-order publication and one late
    corroboration that must raise confidence through a revision."""
    return [
        raw(
            "hack-1",
            "Aave bridge hack drains $47 million",
            HACK,
            domain="coindesk.example",
            published_at="2026-03-10T08:00:00Z",
        ),
        raw(
            "earn-1",
            "Nvidia beat estimates",
            "Nvidia beat estimates for the quarter as data-center revenue climbed.",
            domain="cnbc.example",
            published_at="2026-03-11T09:00:00Z",
        ),
        raw(
            "hack-2",
            "Aave bridge hack drains $47 million",
            HACK_FOLLOW_UP,
            domain="theblock.example",
            published_at="2026-03-10T20:00:00Z",  # arrives after earn-1, published before it
        ),
        raw(
            "deal-1",
            "Microsoft agreed to acquire Datadog",
            "Microsoft agreed to acquire Datadog for $18 billion in an all-cash deal.",
            domain="reuters.example",
            published_at="2026-03-13T08:00:00Z",
        ),
        raw(
            "list-1",
            "Solana is now available on Coinbase",
            "Solana is now available on Coinbase for trading, the exchange said.",
            domain="coindesk.example",
            published_at="2026-03-14T08:00:00Z",
        ),
    ]


def export(result):
    return canonical_json(
        {
            "clusters": [c.model_dump(mode="json") for c in result.clusters],
            "events": [e.model_dump(mode="json") for e in result.events],
            "links": [link.model_dump(mode="json") for link in result.links],
        }
    )


def scored_by_identity(signals):
    """Latest scored tuple per (event_type, asset_id, role) -- what D1 compares."""
    latest: dict[tuple[str, str, str], tuple] = {}
    revisions: dict[tuple[str, str, str], int] = {}
    for signal in signals:
        key = (signal.event_snapshot.event_type.value, signal.asset_id, signal.role.value)
        if revisions.get(key, 0) <= signal.revision:
            revisions[key] = signal.revision
            latest[key] = signal.scored_tuple()
    return latest


def test_fr14_d0_determinism(datasets):
    """Two runs over the same articles produce byte-identical exports and ids."""
    first_articles, first = pipeline.ingest(corpus(), datasets, as_of=AS_OF)
    second_articles, second = pipeline.ingest(corpus(), datasets, as_of=AS_OF)

    assert [a.model_dump() for a in first_articles] == [a.model_dump() for a in second_articles]
    assert export(first) == export(second)
    assert [s.model_dump() for s in first.new_signals] == [
        s.model_dump() for s in second.new_signals
    ]
    assert [b.model_dump() for b in first.briefs] == [b.model_dump() for b in second.briefs]


def test_fr14_d0_reingest_produces_zero_new_revisions(datasets):
    stored, first = pipeline.ingest(corpus(), datasets, as_of=AS_OF)
    _, again = pipeline.ingest(
        corpus(),
        datasets,
        as_of=AS_OF,
        existing_articles=stored,
        existing_signals=list(first.new_signals),
    )
    assert again.new_signals == ()
    assert export(first) == export(again)


def test_fr14_d1_replay_equivalence(datasets):
    """One batch vs five daily batches: identical derived state, same scored tuples."""
    rows = corpus()
    batch_articles, batch = pipeline.ingest(rows, datasets, as_of=AS_OF)

    stored: list = []
    signals: list = []
    aliases: list = []
    incremental = None
    for index, row in enumerate(rows):
        as_of = f"2026-03-1{5 + index}T07:00:00Z"
        fresh, incremental = pipeline.ingest(
            [row],
            datasets,
            as_of=as_of,
            existing_articles=stored,
            existing_signals=signals,
            existing_aliases=aliases,
        )
        stored = [*stored, *fresh]
        signals = [*signals, *incremental.new_signals]
        aliases = [*aliases, *incremental.new_aliases]

    # Clusters, events and links are byte-identical.
    assert export(batch) == export(incremental)
    # Article rows are identical too (content-derived ids).
    assert sorted(a.id for a in batch_articles) == sorted(a.id for a in stored)
    # Latest scored tuple per (event_type, asset_id, role) matches exactly.
    assert scored_by_identity(list(batch.new_signals)) == scored_by_identity(signals)
    # Only the revision *history* differs.
    assert len(signals) >= len(batch.new_signals)


def test_fr14_partitioning_does_not_change_cluster_identity(datasets):
    rows = corpus()
    _, batch = pipeline.ingest(rows, datasets, as_of=AS_OF)
    stored: list = []
    partial = None
    for chunk in (rows[:2], rows[2:4], rows[4:]):
        fresh, partial = pipeline.ingest(chunk, datasets, as_of=AS_OF, existing_articles=stored)
        stored = [*stored, *fresh]
    assert [c.id for c in batch.clusters] == [c.id for c in partial.clusters]
    assert [e.id for e in batch.events] == [e.id for e in partial.events]


def test_fr14_time_is_always_an_input(datasets):
    """The same articles with a different `as_of` change only window membership."""
    rows = corpus()
    _, early = pipeline.ingest(rows, datasets, as_of="2026-03-15T07:00:00Z")
    _, late = pipeline.ingest(rows, datasets, as_of=AS_OF)
    assert export(early) == export(late)
    assert [s.created_as_of for s in early.new_signals] != [
        s.created_as_of for s in late.new_signals
    ]
