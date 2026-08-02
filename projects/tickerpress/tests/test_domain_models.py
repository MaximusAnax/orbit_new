"""DATA_MODEL §2 invariants and their SQLite round trips."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tickerpress.engine.models import (
    Appearance,
    Channel,
    Company,
    DeliveryMode,
    FeedResult,
    FetchStatus,
    IngestRun,
    IngestStatus,
    MatchedVia,
    Mention,
    MentionFeatures,
    Strength,
    TextField,
    ensure_utc,
    iso_utc,
    parse_iso_utc,
)
from tickerpress.services import TickerPressService
from tickerpress_testkit import NOW, rss_feed, rss_item


def mention(**kwargs) -> Mention:
    payload = {
        "article_id": 1,
        "company_ticker": "AAPL",
        "alias_id": 4,
        "field": TextField.TITLE,
        "char_start": 0,
        "char_end": 5,
        "surface": "Apple",
        "matched_via": MatchedVia.ALIAS,
        "strength": Strength.WEAK,
        "features": MentionFeatures(prior=0.25).as_dict(),
        "score": 0.4,
        "threshold": 0.35,
        "accepted": True,
        "engine_version": "0.1.0",
    }
    payload.update(kwargs)
    return Mention.model_validate(payload)


# ---------------------------------------------------------------------------
# time handling
# ---------------------------------------------------------------------------


def test_datamodel_times_are_utc_and_second_precision() -> None:
    naive = datetime(2026, 3, 2, 13, 0, 0, 123456)
    assert ensure_utc(naive) == datetime(2026, 3, 2, 13, 0, 0, tzinfo=UTC)
    assert iso_utc(naive) == "2026-03-02T13:00:00Z"
    assert parse_iso_utc("2026-03-02T13:00:00Z") == NOW
    assert parse_iso_utc("2026-03-02T15:00:00+02:00") == NOW


def test_datamodel_iso_strings_sort_chronologically() -> None:
    stamps = [
        iso_utc(datetime(2026, 3, 2, 13, 0, 0, 500000, tzinfo=UTC)),
        iso_utc(datetime(2026, 3, 2, 13, 0, 1, tzinfo=UTC)),
    ]
    assert stamps == sorted(stamps)


# ---------------------------------------------------------------------------
# mention invariants (§2.7)
# ---------------------------------------------------------------------------


def test_datamodel_accepted_must_equal_score_at_or_above_threshold() -> None:
    mention(score=0.4, threshold=0.35, accepted=True)
    mention(score=0.3, threshold=0.35, accepted=False)
    with pytest.raises(ValidationError):
        mention(score=0.3, threshold=0.35, accepted=True)
    with pytest.raises(ValidationError):
        mention(score=0.4, threshold=0.35, accepted=False)


def test_datamodel_offsets_must_be_a_forward_range() -> None:
    with pytest.raises(ValidationError):
        mention(char_start=5, char_end=5)
    with pytest.raises(ValidationError):
        mention(char_start=-1, char_end=4)


def test_datamodel_strong_candidates_are_unscored() -> None:
    mention(strength=Strength.STRONG, features={}, score=1.0, accepted=True)
    with pytest.raises(ValidationError):
        mention(strength=Strength.STRONG, features={"prior": 0.25}, score=1.0, accepted=True)
    with pytest.raises(ValidationError):
        mention(strength=Strength.STRONG, features={}, score=0.4, accepted=True)


def test_datamodel_mentions_are_frozen() -> None:
    row = mention()
    with pytest.raises(ValidationError):
        row.score = 0.9  # type: ignore[misc]


def test_datamodel_feature_vector_keys_match_the_documented_shape() -> None:
    assert list(MentionFeatures().as_dict()) == [
        "prior",
        "coref_strong",
        "case_signal",
        "window_cues",
        "window_antis",
        "doc_cues",
        "doc_antis",
        "ctx_terms",
        "anti_terms",
        "hyphen_compound",
        "allcaps_run",
    ]


# ---------------------------------------------------------------------------
# appearance invariants (§2.8)
# ---------------------------------------------------------------------------


def test_datamodel_appearance_relevance_must_match_its_components() -> None:
    Appearance(
        article_id=1,
        company_ticker="AAPL",
        mention_count=3,
        title_hit=True,
        lede_hit=True,
        relevance=94,
    )
    with pytest.raises(ValidationError):
        Appearance(
            article_id=1,
            company_ticker="AAPL",
            mention_count=3,
            title_hit=True,
            lede_hit=True,
            relevance=93,
        )


def test_datamodel_appearance_requires_at_least_one_mention() -> None:
    with pytest.raises(ValidationError):
        Appearance(
            article_id=1,
            company_ticker="AAPL",
            mention_count=0,
            title_hit=False,
            lede_hit=False,
            relevance=0,
        )


# ---------------------------------------------------------------------------
# company / ingest run invariants
# ---------------------------------------------------------------------------


def test_datamodel_company_bounds_and_defaults() -> None:
    company = Company(ticker="AAPL", name="Apple Inc.", created_at=NOW)
    assert company.mode is DeliveryMode.DIGEST
    assert company.min_relevance == 20
    assert company.alert_min_relevance == 60
    with pytest.raises(ValidationError):
        Company(ticker="AAPL", name="Apple Inc.", min_relevance=101, created_at=NOW)
    with pytest.raises(ValidationError):
        Company(ticker="AAPL", name="   ", created_at=NOW)


def test_datamodel_ingest_run_is_frozen_and_typed() -> None:
    run = IngestRun(
        started_at=NOW,
        status=IngestStatus.SUCCEEDED,
        feed_results=[FeedResult(feed_id=1, status=FetchStatus.OK, items_seen=3, items_new=2)],
        engine_version="0.1.0",
    )
    assert run.feed_results[0].items_new == 2
    with pytest.raises(ValidationError):
        run.articles_new = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# storage round trips and cascades
# ---------------------------------------------------------------------------


def seed(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.", anti_terms=["orchard"])
    service.feed_source.add(
        "file:///wire.xml",
        rss_feed(
            [
                rss_item(
                    guid="g1",
                    link="https://wireone.example.com/apple-q2",
                    title="Apple Inc. beats estimates",
                    description="Apple Inc. (NASDAQ: AAPL) reported revenue.",
                    pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("Wire One", "file:///wire.xml")
    service.ingest(deliver_alerts=False)


def test_store_round_trips_every_entity(service: TickerPressService) -> None:
    seed(service)
    repository = service.repository
    company = repository.get_company("AAPL")
    assert company is not None and company.anti_terms == ["orchard"]
    assert [alias.text for alias in repository.list_aliases("AAPL")] == [
        "AAPL",
        "$AAPL",
        "Apple Inc.",
        "Apple",
    ]
    feed = repository.get_feed(1)
    assert feed is not None and feed.last_status is FetchStatus.OK
    article = repository.get_article(1)
    assert article is not None and article.story_id == 1
    assert repository.get_story(1) is not None
    assert repository.list_mentions(1)
    assert repository.get_appearance(1, "AAPL") is not None
    assert repository.list_ingest_runs()[0].articles_new == 1
    assert repository.story_relevances(company="AAPL")[0].story_id == 1


def test_store_deleting_an_alias_nulls_the_mention_link_but_keeps_history(
    service: TickerPressService,
) -> None:
    seed(service)
    before = service.repository.list_mentions(1)
    alias_id = before[0].alias_id
    assert alias_id is not None
    assert service.repository.delete_alias(alias_id)
    after = service.repository.list_mentions(1)
    assert len(after) == len(before)
    assert after[0].alias_id is None
    assert after[0].surface == before[0].surface


def test_store_deleting_a_company_keeps_articles_and_the_ledger(
    service: TickerPressService,
) -> None:
    seed(service)
    service.run_digest(Channel.CONSOLE)
    assert service.remove_company("AAPL")
    assert service.repository.get_company("AAPL") is None
    assert service.repository.list_aliases("AAPL") == []
    assert service.repository.list_mentions(1) == []
    assert service.repository.list_appearances(company="AAPL") == []
    assert service.repository.get_article(1) is not None
    assert service.repository.list_delivery_items(company="AAPL")


def test_store_transaction_rolls_back_on_error(service: TickerPressService) -> None:
    seed(service)
    before = len(list(service.repository.iter_articles()))
    with pytest.raises(RuntimeError), service.repository.transaction():
        service.repository.create_story(NOW, NOW, 0)
        raise RuntimeError("boom")
    assert len(list(service.repository.iter_articles())) == before
    assert service.repository.get_story(2) is None


def test_store_list_articles_filters(service: TickerPressService) -> None:
    seed(service)
    assert len(service.repository.list_articles()) == 1
    assert len(service.repository.list_articles(company="AAPL")) == 1
    assert len(service.repository.list_articles(company="TSLA")) == 0
    assert len(service.repository.list_articles(min_relevance=100)) == 0
    assert len(service.repository.list_articles(since=datetime(2027, 1, 1, tzinfo=UTC))) == 0


def test_store_sqlite_repository_satisfies_the_repository_protocol(repository) -> None:
    """One protocol, one implementation; the in-memory backend is the same class."""

    from tickerpress.store import InMemoryRepository, Repository, SQLiteRepository

    assert isinstance(repository, Repository)
    in_memory = InMemoryRepository()
    try:
        assert isinstance(in_memory, SQLiteRepository)
        assert isinstance(in_memory, Repository)
    finally:
        in_memory.close()


def test_store_lexicon_files_all_load_and_hash() -> None:
    from tickerpress.resources import LEXICON_FILES, lexicon_digests, load_lexicons

    lexicons = load_lexicons()
    assert lexicons.corporate_cues and lexicons.anti_cues and lexicons.common_words
    assert lexicons.legal_suffixes and lexicons.abbreviations and lexicons.tracking_params
    digests = lexicon_digests()
    assert set(digests) == set(LEXICON_FILES.values())
    assert all(len(value) == 64 for value in digests.values())
