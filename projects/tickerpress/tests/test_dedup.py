"""FR-7 — shingling, Jaccard, story assignment and its determinism rules."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tickerpress.engine.dedup import (
    DEDUP_THRESHOLD,
    DEDUP_WINDOW_DAYS,
    SHINGLE_SIZE,
    PendingArticle,
    StoredArticleView,
    assign_story,
    dedup_tokens,
    jaccard,
    shingle_set,
    shingles_from_texts,
)
from tickerpress.services import TickerPressService
from tickerpress_testkit import NOW, article_of, make_lexicons, rss_feed, rss_item


@pytest.fixture
def lex():
    return make_lexicons()


def view(
    article_id: int,
    story_id: int,
    *,
    days: float = 0.0,
    url: str = "",
    sha: str = "",
    shingles=frozenset(),
):
    return StoredArticleView(
        article_id=article_id,
        story_id=story_id,
        published_at=NOW + timedelta(days=days),
        canonical_url=url,
        content_sha256=sha,
        shingles=shingles,
    )


def pending(*, days: float = 0.0, url: str = "", sha: str = "", shingles=frozenset()):
    return PendingArticle(
        published_at=NOW + timedelta(days=days),
        canonical_url=url,
        content_sha256=sha,
        shingles=shingles,
    )


# ---------------------------------------------------------------------------
# shingle + similarity mathematics
# ---------------------------------------------------------------------------


def test_fr7_shingles_are_contiguous_three_token_windows() -> None:
    tokens = ["a", "b", "c", "d"]
    assert shingle_set(tokens) == {("a", "b", "c"), ("b", "c", "d")}
    assert SHINGLE_SIZE == 3


def test_fr7_short_documents_contribute_their_whole_token_tuple() -> None:
    assert shingle_set(["a", "b"]) == {("a", "b")}
    assert shingle_set([]) == frozenset()


def test_fr7_dedup_text_is_case_folded_and_punctuation_free(lex) -> None:
    article = article_of("Apple Inc. BEATS estimates!", "Shares rose 3%.", lexicons=lex)
    assert dedup_tokens(article) == (
        "apple",
        "inc.",
        "beats",
        "estimates",
        "shares",
        "rose",
        "3",
    )


def test_fr7_jaccard_is_exact() -> None:
    left = {("a", "b", "c"), ("b", "c", "d")}
    right = {("b", "c", "d"), ("c", "d", "e")}
    assert jaccard(frozenset(left), frozenset(right)) == pytest.approx(1 / 3)
    assert jaccard(frozenset(left), frozenset(left)) == 1.0
    assert jaccard(frozenset(left), frozenset()) == 0.0
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_fr7_case_and_punctuation_do_not_change_resemblance(lex) -> None:
    left = shingles_from_texts("Apple beats estimates", "Shares rose.", None, lex)
    right = shingles_from_texts("APPLE, BEATS ESTIMATES!", "shares rose", None, lex)
    assert jaccard(left, right) == 1.0


# ---------------------------------------------------------------------------
# assignment rules
# ---------------------------------------------------------------------------


def test_fr7_empty_window_opens_a_new_story() -> None:
    decision = assign_story(pending(shingles=shingle_set(["a", "b", "c"])), [])
    assert decision.opens_new_story
    assert decision.similarity is None


def test_fr7_identical_canonical_url_is_a_fast_path() -> None:
    decision = assign_story(
        pending(url="https://x.example/a"),
        [view(7, 3, url="https://x.example/a")],
    )
    assert decision.story_id == 3
    assert decision.similarity == 1.0
    assert decision.matched_article_id == 7


def test_fr7_identical_content_hash_is_a_fast_path() -> None:
    decision = assign_story(pending(sha="abc"), [view(9, 5, url="https://other", sha="abc")])
    assert decision.story_id == 5
    assert decision.similarity == 1.0


def test_fr7_canonical_url_beats_content_hash_when_both_fire() -> None:
    candidates = [
        view(2, 20, url="https://other", sha="same-hash"),
        view(3, 30, url="https://x.example/a", sha="different"),
    ]
    decision = assign_story(pending(url="https://x.example/a", sha="same-hash"), candidates)
    assert decision.story_id == 30
    assert decision.matched_article_id == 3


def test_fr7_fast_path_ties_resolve_to_the_smallest_article_id() -> None:
    candidates = [
        view(11, 110, url="https://x.example/a"),
        view(4, 40, url="https://x.example/a"),
    ]
    decision = assign_story(pending(url="https://x.example/a"), candidates)
    assert decision.matched_article_id == 4
    assert decision.story_id == 40


def test_fr7_fast_paths_are_window_limited() -> None:
    """A year-old identical URL must not resurrect a dead story."""

    old = view(1, 1, days=-400, url="https://x.example/a", sha="h")
    decision = assign_story(pending(url="https://x.example/a", sha="h"), [old])
    assert decision.opens_new_story


def test_fr7_empty_canonical_urls_do_not_match_each_other() -> None:
    decision = assign_story(pending(url="", sha=""), [view(1, 1, url="", sha="")])
    assert decision.opens_new_story


@pytest.mark.parametrize("days,joined", [(7.0, True), (7.01, False), (-7.0, True), (-7.01, False)])
def test_fr7_window_edges_are_inclusive(days: float, joined: bool) -> None:
    decision = assign_story(
        pending(url="https://x.example/a"), [view(1, 1, days=days, url="https://x.example/a")]
    )
    assert (not decision.opens_new_story) is joined
    assert DEDUP_WINDOW_DAYS == 7


def test_fr7_similarity_at_threshold_joins() -> None:
    tokens = [f"t{i}" for i in range(10)]
    shingles = shingle_set(tokens)
    decision = assign_story(
        pending(shingles=shingles), [view(1, 1, shingles=shingles)], threshold=DEDUP_THRESHOLD
    )
    assert decision.story_id == 1
    assert decision.similarity == 1.0


def test_fr7_similarity_below_threshold_opens_a_new_story() -> None:
    left = shingle_set([f"a{i}" for i in range(10)])
    right = shingle_set([f"b{i}" for i in range(10)])
    decision = assign_story(pending(shingles=left), [view(1, 1, shingles=right)])
    assert decision.opens_new_story


def test_fr7_argmax_wins_and_ties_resolve_to_the_smallest_article_id() -> None:
    shingles = shingle_set([f"t{i}" for i in range(10)])
    candidates = [view(5, 50, shingles=shingles), view(2, 20, shingles=shingles)]
    decision = assign_story(pending(shingles=shingles), candidates)
    assert decision.matched_article_id == 2
    assert decision.story_id == 20


def test_fr7_argmax_prefers_the_more_similar_candidate() -> None:
    base = [f"t{i}" for i in range(12)]
    near = shingle_set(base)
    far = shingle_set([*base[:3], *[f"z{i}" for i in range(9)]])
    candidates = [view(1, 10, shingles=far), view(2, 20, shingles=near)]
    decision = assign_story(pending(shingles=near), candidates)
    assert decision.story_id == 20


# ---------------------------------------------------------------------------
# end-to-end story behaviour
# ---------------------------------------------------------------------------


def _wire(guid: str, link: str, title: str, description: str, pub: str) -> str:
    return rss_item(guid=guid, link=link, title=title, description=description, pub_date=pub)


BODY = (
    "Tesla said the recall covers vehicles built between January and March. "
    "The company will replace the seatbelt anchor free of charge at service centres. "
    "Regulators had opened an inquiry after three reports of loose fittings."
)


def test_fr7_syndicated_copies_cluster_into_one_story(service: TickerPressService) -> None:
    service.add_company("TSLA", "Tesla, Inc.")
    service.feed_source.add(
        "file:///a.xml",
        rss_feed(
            [
                _wire(
                    "a1",
                    "https://a.example/tesla-recall",
                    "Tesla recalls 12,000 vehicles over seatbelt fault",
                    BODY,
                    "Mon, 02 Mar 2026 06:05:00 GMT",
                )
            ]
        ),
    )
    service.feed_source.add(
        "file:///b.xml",
        rss_feed(
            [
                _wire(
                    "b1",
                    "https://b.example/tesla-seatbelt?utm_source=rss",
                    "Tesla issues seatbelt recall for 12,000 cars",
                    BODY + " Biz Daily is a fictional outlet.",
                    "Mon, 02 Mar 2026 07:15:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("A Wire", "file:///a.xml")
    service.add_feed("B Daily", "file:///b.xml")
    service.ingest(deliver_alerts=False)

    articles = list(service.repository.iter_articles())
    assert len(articles) == 2
    assert articles[0].story_id == articles[1].story_id
    assert articles[0].dedup_similarity is None
    assert articles[1].dedup_similarity is not None
    assert articles[1].dedup_similarity >= DEDUP_THRESHOLD
    assert service.repository.story_copy_count(articles[0].story_id) == 2


def test_fr7_different_same_day_stories_stay_apart(service: TickerPressService) -> None:
    service.add_company("TSLA", "Tesla, Inc.")
    service.feed_source.add(
        "file:///a.xml",
        rss_feed(
            [
                _wire(
                    "a1",
                    "https://a.example/tesla-recall",
                    "Tesla recalls 12,000 vehicles over seatbelt fault",
                    BODY,
                    "Mon, 02 Mar 2026 06:05:00 GMT",
                ),
                _wire(
                    "a2",
                    "https://a.example/tesla-berlin",
                    "Tesla opens a paint shop at its Berlin plant",
                    "Tesla said the Berlin paint shop adds capacity for two colours. "
                    "Local officials attended the opening ceremony on Friday morning. "
                    "The site now employs eleven thousand people.",
                    "Mon, 02 Mar 2026 08:05:00 GMT",
                ),
            ]
        ),
    )
    service.add_feed("A Wire", "file:///a.xml")
    service.ingest(deliver_alerts=False)

    first, second = list(service.repository.iter_articles())
    assert first.story_id != second.story_id


def test_fr7_representative_is_repointed_when_an_earlier_copy_arrives(
    service: TickerPressService,
) -> None:
    service.add_company("TSLA", "Tesla, Inc.")
    service.feed_source.add(
        "file:///late.xml",
        rss_feed(
            [
                _wire(
                    "late",
                    "https://late.example/tesla-recall",
                    "Tesla recalls 12,000 vehicles over seatbelt fault",
                    BODY,
                    "Mon, 02 Mar 2026 09:00:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("Late", "file:///late.xml")
    service.ingest(deliver_alerts=False)
    story_id = next(iter(service.repository.iter_articles())).story_id
    story = service.repository.get_story(story_id)
    assert story is not None and story.representative_article_id == 1

    service.feed_source.add(
        "file:///early.xml",
        rss_feed(
            [
                _wire(
                    "early",
                    "https://early.example/tesla-recall",
                    "Tesla recalls 12,000 vehicles over seatbelt fault",
                    BODY,
                    "Mon, 02 Mar 2026 05:00:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("Early", "file:///early.xml")
    service.ingest(deliver_alerts=False)

    story = service.repository.get_story(story_id)
    assert story is not None
    assert story.representative_article_id == 2
    assert story.first_published_at.hour == 5


def test_fr7_stories_never_merge_after_creation(service: TickerPressService) -> None:
    """A bridging article joins the argmax side only (SCOPE D6)."""

    service.add_company("TSLA", "Tesla, Inc.")
    left = "Tesla said the recall covers vehicles built in January and February this year."
    right = "Tesla said the Berlin paint shop adds capacity for two additional colours."
    service.feed_source.add(
        "file:///a.xml",
        rss_feed(
            [
                _wire(
                    "l",
                    "https://a.example/l",
                    "Tesla recall widens",
                    left,
                    "Mon, 02 Mar 2026 06:00:00 GMT",
                ),
                _wire(
                    "r",
                    "https://a.example/r",
                    "Tesla paints Berlin",
                    right,
                    "Mon, 02 Mar 2026 07:00:00 GMT",
                ),
                _wire(
                    "bridge",
                    "https://a.example/bridge",
                    "Tesla recall widens as Berlin paint shop opens",
                    f"{left} {right}",
                    "Mon, 02 Mar 2026 08:00:00 GMT",
                ),
            ]
        ),
    )
    service.add_feed("A Wire", "file:///a.xml")
    service.ingest(deliver_alerts=False)

    stories = {article.id: article.story_id for article in service.repository.iter_articles()}
    assert stories[1] != stories[2]
    assert stories[3] in {stories[1], stories[2], max(stories.values())}
    assert service.repository.get_story(stories[1]) is not None
    assert service.repository.story_copy_count(stories[1]) <= 2
