"""FR-10 — immediate alerts during ingest, and the shared ledger."""

from __future__ import annotations

import pytest
from tickerpress.engine.models import Channel, DeliveryKind, DeliveryMode
from tickerpress.services import TickerPressService
from tickerpress_testkit import rss_feed, rss_item

HEADLINE = rss_item(
    guid="tsla-recall",
    link="https://bizdaily.example.com/tesla-recall",
    title="Tesla, Inc. recalls 12,000 vehicles over seatbelt fault",
    description=(
        "Tesla, Inc. said the recall covers vehicles built between January and March. "
        "The company will replace the anchor free of charge."
    ),
    pub_date="Mon, 02 Mar 2026 06:05:00 GMT",
)
PASSING = rss_item(
    guid="roundup",
    link="https://bizdaily.example.com/roundup",
    title="Markets slip as the week opens",
    description=(
        "Tesla, Inc. drifted lower in thin trading alongside other carmakers. "
        "European indexes were mixed on Monday morning."
    ),
    pub_date="Mon, 02 Mar 2026 07:05:00 GMT",
)


def seed(service: TickerPressService, items: list[str], **company_kwargs) -> None:
    service.add_company("TSLA", "Tesla, Inc.", **company_kwargs)
    service.feed_source.add("file:///biz.xml", rss_feed(items))
    service.add_feed("Biz Daily", "file:///biz.xml")


def test_fr10_headline_story_alerts_exactly_once_during_ingest(
    service: TickerPressService, notifier
) -> None:
    seed(service, [HEADLINE], mode=DeliveryMode.ALERT, alert_min_relevance=60)
    run = service.ingest(alert_channel=Channel.CONSOLE)
    assert run.alerts_sent == 1
    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert message.kind is DeliveryKind.ALERT
    assert message.subject.startswith("Alert: TSLA — ")
    assert len(message.items) == 1


def test_fr10_passing_mention_does_not_alert(service: TickerPressService, notifier) -> None:
    seed(service, [PASSING], mode=DeliveryMode.ALERT, alert_min_relevance=60)
    run = service.ingest(alert_channel=Channel.CONSOLE)
    appearance = service.repository.get_appearance(1, "TSLA")
    assert appearance is not None and appearance.relevance < 60
    assert run.alerts_sent == 0
    assert notifier.messages == []


@pytest.mark.parametrize(
    "mode,expected",
    [
        (DeliveryMode.ALERT, 1),
        (DeliveryMode.BOTH, 1),
        (DeliveryMode.DIGEST, 0),
        (DeliveryMode.MUTE, 0),
    ],
)
def test_fr10_mode_matrix(
    service: TickerPressService, notifier, mode: DeliveryMode, expected: int
) -> None:
    seed(service, [HEADLINE], mode=mode, alert_min_relevance=60)
    run = service.ingest(alert_channel=Channel.CONSOLE)
    assert run.alerts_sent == expected
    assert len(notifier.messages) == expected


def test_fr10_threshold_is_inclusive(service: TickerPressService) -> None:
    seed(service, [HEADLINE], mode=DeliveryMode.ALERT, alert_min_relevance=88)
    appearance_relevance = None
    run = service.ingest(alert_channel=Channel.CONSOLE)
    appearance = service.repository.get_appearance(1, "TSLA")
    assert appearance is not None
    appearance_relevance = appearance.relevance
    assert appearance_relevance == 88
    assert run.alerts_sent == 1


def test_fr10_above_threshold_by_one_point_does_not_alert(service: TickerPressService) -> None:
    seed(service, [HEADLINE], mode=DeliveryMode.ALERT, alert_min_relevance=89)
    run = service.ingest(alert_channel=Channel.CONSOLE)
    assert run.alerts_sent == 0


def test_fr10_no_alerts_flag_suppresses_delivery(service: TickerPressService, notifier) -> None:
    seed(service, [HEADLINE], mode=DeliveryMode.BOTH, alert_min_relevance=60)
    run = service.ingest(deliver_alerts=False)
    assert run.alerts_sent == 0
    assert notifier.messages == []


def test_fr10_alerted_story_is_absent_from_that_channels_digest(
    service: TickerPressService,
) -> None:
    """Alerts and digests share one ledger (US-6, US-7)."""

    seed(service, [HEADLINE, PASSING], mode=DeliveryMode.BOTH, alert_min_relevance=60)
    run = service.ingest(alert_channel=Channel.CONSOLE)
    assert run.alerts_sent == 1

    digest = service.run_digest(Channel.CONSOLE)
    assert digest.delivery is not None
    delivered_stories = {item.story_id for item in digest.items}
    alerted_items = service.repository.list_delivery_items(channel=Channel.CONSOLE)
    alerted_story = next(item.story_id for item in alerted_items if item.counted)
    assert alerted_story not in delivered_stories


def test_fr10_a_different_channel_still_receives_the_story(
    service: TickerPressService, tmp_path
) -> None:
    seed(service, [HEADLINE], mode=DeliveryMode.BOTH, alert_min_relevance=60)
    service.ingest(alert_channel=Channel.CONSOLE)

    file_digest = service.run_digest(Channel.FILE)
    assert file_digest.delivery is not None
    assert "Tesla" in (file_digest.body or "")
    outbox = sorted((tmp_path / "outbox").iterdir())
    assert len(outbox) == 1


def test_fr10_alerts_appear_in_the_same_ledger_as_digests(service: TickerPressService) -> None:
    seed(service, [HEADLINE, PASSING], mode=DeliveryMode.BOTH, alert_min_relevance=60)
    service.ingest(alert_channel=Channel.CONSOLE)
    service.run_digest(Channel.CONSOLE)
    kinds = [delivery.kind for delivery in service.repository.list_deliveries()]
    assert set(kinds) == {DeliveryKind.ALERT, DeliveryKind.DIGEST}
    assert all(item.counted for item in service.repository.list_delivery_items())


def test_fr10_re_ingest_does_not_re_alert(service: TickerPressService, notifier) -> None:
    seed(service, [HEADLINE], mode=DeliveryMode.ALERT, alert_min_relevance=60)
    first = service.ingest(alert_channel=Channel.CONSOLE)
    second = service.ingest(alert_channel=Channel.CONSOLE)
    assert first.alerts_sent == 1
    assert second.alerts_sent == 0
    assert len(notifier.messages) == 1
