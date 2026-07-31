"""FR-12 — notifier adapters: offline twins and env-gated live ones."""

from __future__ import annotations

import io

import pytest
from tickerpress.adapters.notify import ComposedMessage, DeliveryError, MessageItem
from tickerpress.adapters.notify_console import ConsoleNotifier
from tickerpress.adapters.notify_email import EmailNotifier
from tickerpress.adapters.notify_file import FileNotifier
from tickerpress.adapters.notify_webhook import WebhookNotifier
from tickerpress.engine.models import Channel, DeliveryKind
from tickerpress.services import NotifierRegistry, TickerPressService, UnknownChannelError
from tickerpress_testkit import NOW, rss_feed, rss_item

BODY = "# TickerPress digest — 2026-03-02\n\nbody\n\n---\nInformational only.\n"


def message(channel: Channel = Channel.FILE, delivery_id: int = 12) -> ComposedMessage:
    return ComposedMessage(
        channel=channel,
        kind=DeliveryKind.DIGEST,
        delivery_id=delivery_id,
        subject="TickerPress digest — 2026-03-02",
        body_text=BODY,
        created_at=NOW,
        items=(
            MessageItem(
                ticker="AAPL",
                story_id=41,
                article_id=117,
                url="https://wireone.example.com/apple-q2",
                title="Apple beats March-quarter estimates",
                relevance=94,
                published_at=NOW,
            ),
        ),
    )


def test_fr12_console_notifier_writes_the_body() -> None:
    stream = io.StringIO()
    ConsoleNotifier(stream).deliver(message(Channel.CONSOLE))
    assert stream.getvalue() == BODY


def test_fr12_console_notifier_terminates_the_body_with_a_newline() -> None:
    stream = io.StringIO()
    ConsoleNotifier(stream).deliver(
        ComposedMessage(
            channel=Channel.CONSOLE,
            kind=DeliveryKind.ALERT,
            delivery_id=1,
            subject="s",
            body_text="no trailing newline",
            created_at=NOW,
        )
    )
    assert stream.getvalue().endswith("\n")


def test_fr12_file_notifier_name_is_deterministic(tmp_path) -> None:
    notifier = FileNotifier(tmp_path)
    assert notifier.filename_for(message()) == "20260302T130000Z-digest-12.md"


def test_fr12_file_notifier_writes_the_body_byte_for_byte(tmp_path) -> None:
    notifier = FileNotifier(tmp_path)
    notifier.deliver(message())
    written = (tmp_path / "20260302T130000Z-digest-12.md").read_text(encoding="utf-8")
    assert written == BODY


def test_fr12_file_notifier_creates_the_outbox(tmp_path) -> None:
    notifier = FileNotifier(tmp_path / "nested" / "outbox")
    notifier.deliver(message())
    assert notifier.path_for(message()).exists()


def test_fr12_outbox_file_matches_the_delivery_audit_copy(
    service: TickerPressService, tmp_path
) -> None:
    service.add_company("AAPL", "Apple Inc.")
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
    result = service.run_digest(Channel.FILE)
    assert result.delivery is not None

    written = sorted((tmp_path / "outbox").iterdir())
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == result.delivery.body_text
    assert written[0].name == f"20260302T130000Z-digest-{result.delivery.id}.md"


def test_fr12_file_notifier_failure_raises_delivery_error(tmp_path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    with pytest.raises(DeliveryError):
        FileNotifier(blocker).deliver(message())


def test_fr12_email_notifier_is_none_without_credentials() -> None:
    assert EmailNotifier.from_env({}) is None
    assert EmailNotifier.from_env({"TICKERPRESS_SMTP_HOST": "smtp.example.com"}) is None


def test_fr12_email_notifier_builds_from_env() -> None:
    notifier = EmailNotifier.from_env(
        {
            "TICKERPRESS_SMTP_HOST": "smtp.example.com",
            "TICKERPRESS_SMTP_PORT": "2525",
            "TICKERPRESS_SMTP_USERNAME": "user",
            "TICKERPRESS_SMTP_PASSWORD": "secret",
            "TICKERPRESS_EMAIL_FROM": "bot@example.com",
            "TICKERPRESS_EMAIL_TO": "me@example.com, other@example.com",
        }
    )
    assert notifier is not None
    assert notifier.port == 2525
    assert notifier.recipients == ("me@example.com", "other@example.com")


def test_fr12_email_notifier_rejects_a_username_without_a_password() -> None:
    with pytest.raises(DeliveryError):
        EmailNotifier.from_env(
            {
                "TICKERPRESS_SMTP_HOST": "smtp.example.com",
                "TICKERPRESS_SMTP_USERNAME": "user",
                "TICKERPRESS_EMAIL_FROM": "bot@example.com",
                "TICKERPRESS_EMAIL_TO": "me@example.com",
            }
        )


def test_fr12_email_message_carries_subject_and_body() -> None:
    notifier = EmailNotifier(
        host="smtp.example.com", sender="bot@example.com", recipients=("me@example.com",)
    )
    mail = notifier.build_message(message(Channel.EMAIL))
    assert mail["Subject"] == "TickerPress digest — 2026-03-02"
    assert mail["To"] == "me@example.com"
    assert mail.get_content().strip() == BODY.strip()


def test_fr12_webhook_notifier_is_none_without_a_url() -> None:
    assert WebhookNotifier.from_env({}) is None


def test_fr12_webhook_notifier_rejects_a_non_http_url() -> None:
    with pytest.raises(DeliveryError):
        WebhookNotifier.from_env({"TICKERPRESS_WEBHOOK_URL": "ftp://example.com/hook"})


def test_fr12_webhook_payload_is_slack_compatible() -> None:
    notifier = WebhookNotifier.from_env({"TICKERPRESS_WEBHOOK_URL": "https://hooks.example/x"})
    assert notifier is not None
    payload = notifier.build_payload(message(Channel.WEBHOOK))
    assert payload["text"] == BODY
    assert payload["items"] == [
        {
            "ticker": "AAPL",
            "story_id": 41,
            "url": "https://wireone.example.com/apple-q2",
            "title": "Apple beats March-quarter estimates",
            "relevance": 94,
            "published_at": "2026-03-02T13:00:00Z",
        }
    ]


def test_fr12_registry_returns_offline_notifiers_by_default(tmp_path) -> None:
    registry = NotifierRegistry(outbox_dir=tmp_path)
    assert isinstance(registry.get(Channel.CONSOLE), ConsoleNotifier)
    assert isinstance(registry.get(Channel.FILE), FileNotifier)


def test_fr12_registry_refuses_unconfigured_live_channels(tmp_path, monkeypatch) -> None:
    for name in (
        "TICKERPRESS_SMTP_HOST",
        "TICKERPRESS_EMAIL_FROM",
        "TICKERPRESS_EMAIL_TO",
        "TICKERPRESS_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    registry = NotifierRegistry(outbox_dir=tmp_path)
    with pytest.raises(UnknownChannelError):
        registry.get(Channel.EMAIL)
    with pytest.raises(UnknownChannelError):
        registry.get(Channel.WEBHOOK)
