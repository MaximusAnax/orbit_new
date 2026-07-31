"""FR-9 — digest selection, ordering law, rendering and dry-run inertness."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tickerpress.engine.digest import (
    DigestCandidate,
    compose_alert,
    compose_digest,
    parse_template,
    select_digest_candidates,
)
from tickerpress.engine.models import Channel, DeliveryMode
from tickerpress.resources import load_lexicons
from tickerpress.services import TickerPressService
from tickerpress_testkit import NOW, make_company, rss_feed, rss_item

TEMPLATE = parse_template(load_lexicons().digest_template)

GOLDEN_DIGEST = """# TickerPress digest — 2026-03-02

## AAPL — Apple Inc.
- [Apple beats March-quarter estimates on services strength](https://wireone.example.com/apple-q2)
  — Wire One, 2026-03-01 21:30 UTC, relevance 94, matched: Apple, Apple Inc., AAPL — +3 other outlets
- [Apple opens flagship store in Mumbai](https://techledger.example.com/apple-mumbai)
  — Tech Ledger, 2026-03-01 09:10 UTC, relevance 44, matched: Apple

## TSLA — Tesla, Inc.
- [Tesla recalls 12,000 vehicles over seatbelt fault](https://bizdaily.example.com/tesla-recall)
  — Biz Daily, 2026-03-02 06:05 UTC, relevance 75, matched: Tesla — +1 other outlet

---
Informational only — links to third-party news coverage. Not investment advice.
"""


def candidate(
    ticker: str,
    story_id: int,
    relevance: int,
    *,
    title: str = "Headline",
    url: str = "https://example.com/a",
    outlet: str = "Outlet",
    published: datetime | None = None,
    copies: int = 1,
    surfaces: tuple[str, ...] = ("Alias",),
    company=None,
    **company_kwargs,
) -> DigestCandidate:
    moment = published or datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    return DigestCandidate(
        company=company or make_company(ticker, f"{ticker} Inc.", **company_kwargs),
        story_id=story_id,
        relevance=relevance,
        article_id=story_id * 10,
        title=title,
        url=url,
        outlet=outlet,
        published_at=moment,
        first_published_at=moment,
        copy_count=copies,
        matched_surfaces=surfaces,
    )


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------


def test_fr9_template_exposes_every_required_section() -> None:
    for section in (
        "digest_subject",
        "digest_heading",
        "alert_subject",
        "alert_heading",
        "company_heading",
        "item",
        "extra_one",
        "extra_many",
        "footer",
    ):
        assert section in TEMPLATE


def test_fr9_template_missing_a_section_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_template("=== footer ===\nonly a footer\n")


def test_d14_footer_is_part_of_the_committed_template() -> None:
    assert "Not investment advice" in TEMPLATE["footer"]
    assert "Informational only" in TEMPLATE["footer"]


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def test_fr9_selection_requires_digest_or_both_mode() -> None:
    items = [
        candidate("AAA", 1, 90, mode=DeliveryMode.DIGEST),
        candidate("BBB", 2, 90, mode=DeliveryMode.BOTH),
        candidate("CCC", 3, 90, mode=DeliveryMode.ALERT),
        candidate("DDD", 4, 90, mode=DeliveryMode.MUTE),
    ]
    assert [c.company.ticker for c in select_digest_candidates(items)] == ["AAA", "BBB"]


def test_fr9_selection_applies_the_company_min_relevance_floor() -> None:
    items = [
        candidate("AAA", 1, 19, min_relevance=20),
        candidate("BBB", 2, 25, min_relevance=20),
        candidate("CCC", 3, 44, min_relevance=50),
        candidate("DDD", 4, 63, min_relevance=50),
    ]
    assert [c.company.ticker for c in select_digest_candidates(items)] == ["BBB", "DDD"]


def test_fr9_ordering_law() -> None:
    early = datetime(2026, 3, 1, 6, 0, tzinfo=UTC)
    late = datetime(2026, 3, 2, 6, 0, tzinfo=UTC)
    items = [
        candidate("TSLA", 9, 50, published=late),
        candidate("AAPL", 3, 50, published=early),
        candidate("AAPL", 1, 94, published=early),
        candidate("AAPL", 2, 50, published=late),
    ]
    ordered = select_digest_candidates(items)
    assert [(c.company.ticker, c.story_id) for c in ordered] == [
        ("AAPL", 1),  # relevance desc
        ("AAPL", 2),  # then first_published_at desc
        ("AAPL", 3),
        ("TSLA", 9),  # companies by ticker asc
    ]


def test_fr9_story_id_breaks_a_full_tie() -> None:
    moment = datetime(2026, 3, 1, 6, 0, tzinfo=UTC)
    items = [candidate("AAPL", 7, 50, published=moment), candidate("AAPL", 2, 50, published=moment)]
    assert [c.story_id for c in select_digest_candidates(items)] == [2, 7]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_fr9_rendered_body_matches_the_data_model_example() -> None:
    apple = make_company("AAPL", "Apple Inc.")
    tesla = make_company("TSLA", "Tesla, Inc.")
    items = [
        candidate(
            "AAPL",
            41,
            94,
            title="Apple beats March-quarter estimates on services strength",
            url="https://wireone.example.com/apple-q2",
            outlet="Wire One",
            published=datetime(2026, 3, 1, 21, 30, tzinfo=UTC),
            copies=4,
            surfaces=("Apple", "Apple Inc.", "AAPL"),
            company=apple,
        ),
        candidate(
            "AAPL",
            42,
            44,
            title="Apple opens flagship store in Mumbai",
            url="https://techledger.example.com/apple-mumbai",
            outlet="Tech Ledger",
            published=datetime(2026, 3, 1, 9, 10, tzinfo=UTC),
            copies=1,
            surfaces=("Apple",),
            company=apple,
        ),
        candidate(
            "TSLA",
            43,
            75,
            title="Tesla recalls 12,000 vehicles over seatbelt fault",
            url="https://bizdaily.example.com/tesla-recall",
            outlet="Biz Daily",
            published=datetime(2026, 3, 2, 6, 5, tzinfo=UTC),
            copies=2,
            surfaces=("Tesla",),
            company=tesla,
        ),
    ]
    body = compose_digest(items, NOW, TEMPLATE)
    assert body is not None
    assert body.body_text == GOLDEN_DIGEST
    assert body.subject == "TickerPress digest — 2026-03-02"


def test_fr9_single_extra_outlet_is_singular() -> None:
    body = compose_digest([candidate("AAA", 1, 90, copies=2)], NOW, TEMPLATE)
    assert body is not None
    assert "+1 other outlet\n" in body.body_text
    assert "outlets" not in body.body_text


def test_fr9_a_single_copy_story_has_no_outlet_suffix() -> None:
    body = compose_digest([candidate("AAA", 1, 90, copies=1)], NOW, TEMPLATE)
    assert body is not None
    assert "other outlet" not in body.body_text


def test_fr9_empty_selection_composes_nothing() -> None:
    assert compose_digest([], NOW, TEMPLATE) is None
    assert compose_digest([candidate("AAA", 1, 5, min_relevance=20)], NOW, TEMPLATE) is None


def test_fr9_alert_body_uses_the_same_item_line_and_footer() -> None:
    item = candidate("TSLA", 43, 75, title="Tesla recalls 12,000 vehicles", surfaces=("Tesla",))
    body = compose_alert(item, NOW, TEMPLATE)
    assert body.subject == "Alert: TSLA — Tesla recalls 12,000 vehicles"
    assert body.body_text.startswith("# TickerPress alert — TSLA")
    assert "Not investment advice" in body.body_text
    assert "relevance 75" in body.body_text


def test_fr9_bodies_carry_no_opinion_fields() -> None:
    """D14: the template can only interpolate facts."""

    body = compose_digest([candidate("AAA", 1, 90)], NOW, TEMPLATE)
    assert body is not None
    for banned in ("buy", "sell", "sentiment", "price target", "recommend"):
        assert banned not in body.body_text.casefold()


# ---------------------------------------------------------------------------
# service-level behaviour
# ---------------------------------------------------------------------------


def _seed(service: TickerPressService, **company_kwargs) -> None:
    service.add_company("AAPL", "Apple Inc.", **company_kwargs)
    service.feed_source.add(
        "file:///wire.xml",
        rss_feed(
            [
                rss_item(
                    guid="g1",
                    link="https://wireone.example.com/apple-q2",
                    title="Apple Inc. beats March-quarter estimates",
                    description="Apple Inc. (NASDAQ: AAPL) reported quarterly revenue.",
                    pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
                )
            ]
        ),
    )
    service.add_feed("Wire One", "file:///wire.xml")
    service.ingest(deliver_alerts=False)


def test_fr9_digest_run_persists_and_sends(service: TickerPressService, notifier) -> None:
    _seed(service)
    result = service.run_digest(Channel.CONSOLE)
    assert result.delivery is not None
    assert result.delivery.status.value == "sent"
    assert result.body is not None and result.body.endswith("\n")
    assert len(notifier.messages) == 1
    assert notifier.messages[0].body_text == result.delivery.body_text


def test_fr9_dry_run_persists_nothing_and_calls_no_notifier(
    service: TickerPressService, notifier
) -> None:
    _seed(service)
    result = service.run_digest(Channel.CONSOLE, dry_run=True)
    assert result.dry_run
    assert result.body is not None and "Apple" in result.body
    assert result.delivery is None
    assert service.repository.list_deliveries() == []
    assert service.repository.list_delivery_items() == []
    assert notifier.messages == []

    # ... and the story is still eligible afterwards
    second = service.run_digest(Channel.CONSOLE)
    assert second.delivery is not None
    assert second.body == result.body


def test_fr9_empty_digest_sends_and_records_nothing(service: TickerPressService, notifier) -> None:
    result = service.run_digest(Channel.CONSOLE)
    assert result.empty
    assert result.delivery is None
    assert service.repository.list_deliveries() == []
    assert notifier.messages == []


def test_fr9_muted_company_is_absent_from_the_digest(service: TickerPressService) -> None:
    _seed(service, mode=DeliveryMode.MUTE)
    assert service.repository.list_appearances() != []  # still detected and archived
    assert service.run_digest(Channel.CONSOLE).empty


def test_fr9_story_below_min_relevance_is_excluded(service: TickerPressService) -> None:
    _seed(service, min_relevance=100)
    assert service.run_digest(Channel.CONSOLE).empty
