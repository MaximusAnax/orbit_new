"""FR-2: pinned tokenization, Jaccard, transitive closure, cluster identity."""

from __future__ import annotations

from newsalpha.engine.cluster import (
    SIMILARITY_THRESHOLD,
    build_clusters,
    jaccard,
    shingles,
    similar,
    tokens,
)
from newsalpha.engine.normalize import hex16
from newsalpha_testkit import article

STORY = (
    "Solana is now available on Coinbase for trading, the exchange said on Tuesday. "
    "Trading opens once liquidity conditions are met and the order book is seeded."
)
OTHER = (
    "Nvidia beat estimates for the quarter as data-center revenue climbed. "
    "Shares rose in extended trading after the report landed."
)


def test_fr2_tokenization_is_pinned_to_lowercase_alnum_and_dollar():
    assert tokens("Apple's Q3: $AAPL beat -- 4.2%!") == [
        "apple",
        "s",
        "q3",
        "$aapl",
        "beat",
        "4",
        "2",
    ]


def test_fr2_short_documents_yield_one_shingle(datasets):
    row = article(datasets, "s", "A B", "C")
    assert len(shingles(row)) == 1


def test_fr2_jaccard_of_identical_documents_is_one(datasets):
    left = article(datasets, "a", "Title", STORY)
    right = article(datasets, "b", "Title", STORY, domain="cnbc.example")
    assert jaccard(shingles(left), shingles(right)) == 1.0


def test_fr2_near_duplicates_cluster_and_unrelated_stories_do_not(datasets):
    left = article(datasets, "a", "Solana lists on Coinbase", STORY)
    right = article(
        datasets,
        "b",
        "Solana lists on Coinbase",
        STORY + " Analysts noted the pair.",
        domain="cnbc.example",
        published_at="2026-03-10T12:00:00Z",
    )
    unrelated = article(datasets, "c", "Nvidia results", OTHER, domain="wsj.example")
    assert jaccard(shingles(left), shingles(right)) >= SIMILARITY_THRESHOLD
    assert jaccard(shingles(left), shingles(unrelated)) < SIMILARITY_THRESHOLD

    clusters = build_clusters([left, right, unrelated])
    assert len(clusters) == 2
    sizes = sorted(c.article_count for c in clusters)
    assert sizes == [1, 2]


def test_fr2_similarity_requires_the_48_hour_window(datasets):
    left = article(datasets, "a", "Solana lists", STORY, published_at="2026-03-10T08:00:00Z")
    late = article(
        datasets,
        "b",
        "Solana lists",
        STORY,
        domain="cnbc.example",
        published_at="2026-03-13T08:00:00Z",
    )
    assert jaccard(shingles(left), shingles(late)) == 1.0
    assert similar(left, late) is False
    assert len(build_clusters([left, late])) == 2


def test_fr2_clusters_are_the_transitive_closure(datasets):
    """A-B similar, B-C similar, A-C not: all three still land in one cluster."""
    words = [f"w{i:02d}" for i in range(1, 51)]
    a = article(datasets, "a", "T", " ".join(words[:30]))
    b = article(
        datasets,
        "b",
        "T",
        " ".join(words[:40]),
        domain="cnbc.example",
        published_at="2026-03-10T09:00:00Z",
    )
    c = article(
        datasets,
        "c",
        "T",
        " ".join(words),
        domain="wsj.example",
        published_at="2026-03-10T10:00:00Z",
    )
    assert jaccard(shingles(a), shingles(b)) >= SIMILARITY_THRESHOLD
    assert jaccard(shingles(b), shingles(c)) >= SIMILARITY_THRESHOLD
    assert jaccard(shingles(a), shingles(c)) < SIMILARITY_THRESHOLD
    clusters = build_clusters([a, b, c])
    assert len(clusters) == 1
    assert clusters[0].article_count == 3


def test_fr2_exact_cluster_reconstruction_is_order_independent(datasets):
    """US-3/D1: membership and identity do not depend on arrival order."""
    rows = [
        article(datasets, "a", "Solana lists on Coinbase", STORY),
        article(
            datasets,
            "b",
            "Solana lists on Coinbase",
            STORY + " More.",
            domain="cnbc.example",
            published_at="2026-03-10T12:00:00Z",
        ),
        article(datasets, "c", "Nvidia results", OTHER, domain="wsj.example"),
    ]
    forward = build_clusters(rows)
    backward = build_clusters(list(reversed(rows)))
    assert [c.model_dump() for c in forward] == [c.model_dump() for c in backward]


def test_fr2_cluster_id_derives_from_the_earliest_member(datasets):
    early = article(datasets, "a", "Solana lists", STORY, published_at="2026-03-10T08:00:00Z")
    late = article(
        datasets,
        "b",
        "Solana lists",
        STORY,
        domain="cnbc.example",
        published_at="2026-03-10T20:00:00Z",
    )
    cluster = build_clusters([early, late])[0]
    assert cluster.id == hex16(f"cluster|{early.id}")
    assert cluster.earliest_published_at == early.published_at
    assert cluster.latest_published_at == late.published_at


def test_fr2_corroboration_counts_distinct_domains_and_best_tier_wins(datasets):
    rows = [
        article(datasets, "a", "Solana lists", STORY, domain="blog.example"),
        article(
            datasets,
            "b",
            "Solana lists",
            STORY + " x",
            domain="blog.example",
            published_at="2026-03-10T09:00:00Z",
        ),
        article(
            datasets,
            "c",
            "Solana lists",
            STORY + " y",
            domain="sec.example",
            published_at="2026-03-10T10:00:00Z",
        ),
    ]
    cluster = build_clusters(rows)[0]
    assert cluster.article_count == 3
    assert cluster.corroboration == 2
    assert cluster.best_tier.value == "t1_official"
