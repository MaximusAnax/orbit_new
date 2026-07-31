"""FR-2: near-duplicate clustering, cluster identity and corroboration.

The tokenization is pinned exactly by SCOPE FR-2 so any faithful implementation
reproduces the same clusters:

    shingle_input(a) = normalized_title + " " + normalized_body[:400]
    tokens(s)        = casefold(s), every maximal run of characters outside
                       [a-z0-9$] -> a single space, strip, split(" ")
    shingles(a)      = set of contiguous 5-token windows (or the single tuple of
                       all tokens when there are fewer than 5)
    similar(a, b)    = Jaccard >= 0.60 and |published_at delta| <= 48 h

Clusters are the transitive closure (union-find) of `similar`, so membership is
independent of ingest order and of how articles were partitioned across runs.
"""

from __future__ import annotations

from datetime import timedelta

from ..models import TIER_ORDER, Article, Cluster
from .normalize import hex16, parse_iso

SHINGLE_BODY_CHARS = 400
SHINGLE_SIZE = 5
SIMILARITY_THRESHOLD = 0.60
WINDOW_HOURS = 48

_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789$")


def shingle_input(article: Article) -> str:
    return f"{article.title} {article.body[:SHINGLE_BODY_CHARS]}"


def tokens(text: str) -> list[str]:
    """Casefold, collapse every non-`[a-z0-9$]` run to one space, strip, split."""
    folded = text.casefold()
    out: list[str] = []
    pending_space = False
    for char in folded:
        if char in _ALLOWED:
            if pending_space and out:
                out.append(" ")
            pending_space = False
            out.append(char)
        else:
            pending_space = True
    return "".join(out).split(" ") if out else []


def shingles(article: Article) -> frozenset[tuple[str, ...]]:
    toks = tokens(shingle_input(article))
    if not toks:
        return frozenset()
    if len(toks) < SHINGLE_SIZE:
        return frozenset({tuple(toks)})
    return frozenset(tuple(toks[i : i + SHINGLE_SIZE]) for i in range(len(toks) - SHINGLE_SIZE + 1))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    if union == 0:  # pragma: no cover - unreachable given the guard above
        return 0.0
    return len(a & b) / union


def similar(article_a: Article, article_b: Article) -> bool:
    """FR-2's pairwise relation: shingle Jaccard >= 0.60 within a 48-hour window."""
    delta = abs(parse_iso(article_a.published_at) - parse_iso(article_b.published_at))
    if delta > timedelta(hours=WINDOW_HOURS):
        return False
    return jaccard(shingles(article_a), shingles(article_b)) >= SIMILARITY_THRESHOLD


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            # Deterministic merge: the lexicographically smaller root wins.
            if root_b < root_a:
                root_a, root_b = root_b, root_a
            self._parent[root_b] = root_a


def build_clusters(articles: list[Article]) -> list[Cluster]:
    """Transitive closure of `similar` over in-window articles (FR-2).

    `articles` must already be filtered to the active window and to
    `excluded_from_analysis == False`; the caller (pipeline) owns that filter.
    Returns clusters sorted by id, each with sorted member ids, so the output is a
    pure function of the input set -- not of its order.
    """
    ordered = sorted(articles, key=lambda a: (a.published_at, a.id))
    by_id = {a.id: a for a in ordered}
    if len(by_id) != len(ordered):
        raise ValueError("duplicate article ids passed to build_clusters")

    shingle_cache = {a.id: shingles(a) for a in ordered}
    published = {a.id: parse_iso(a.published_at) for a in ordered}
    uf = _UnionFind([a.id for a in ordered])

    horizon = timedelta(hours=WINDOW_HOURS)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if published[right.id] - published[left.id] > horizon:
                break  # ordered by published_at: no later article can be in window
            if jaccard(shingle_cache[left.id], shingle_cache[right.id]) >= SIMILARITY_THRESHOLD:
                uf.union(left.id, right.id)

    groups: dict[str, list[str]] = {}
    for article_id in by_id:
        groups.setdefault(uf.find(article_id), []).append(article_id)

    clusters: list[Cluster] = []
    for members in groups.values():
        member_articles = [by_id[m] for m in members]
        anchor = min(member_articles, key=lambda a: (a.published_at, a.id))
        cluster_id = hex16(f"cluster|{anchor.id}")
        domains = {a.source_domain for a in member_articles}
        best_tier = max((a.tier for a in member_articles), key=lambda tier: TIER_ORDER[tier])
        clusters.append(
            Cluster(
                id=cluster_id,
                article_ids=tuple(sorted(members)),
                earliest_published_at=min(a.published_at for a in member_articles),
                latest_published_at=max(a.published_at for a in member_articles),
                article_count=len(members),
                corroboration=len(domains),
                best_tier=best_tier,
            )
        )
    return sorted(clusters, key=lambda c: c.id)


__all__ = [
    "SHINGLE_BODY_CHARS",
    "SHINGLE_SIZE",
    "SIMILARITY_THRESHOLD",
    "WINDOW_HOURS",
    "build_clusters",
    "jaccard",
    "shingle_input",
    "shingles",
    "similar",
    "tokens",
]
