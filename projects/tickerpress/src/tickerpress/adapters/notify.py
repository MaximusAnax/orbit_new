"""Notifier port (SCOPE FR-12).

``deliver`` either returns or raises :class:`DeliveryError`. That contract is
what the exactly-once ledger is built on: a raise leaves the delivery's items
uncounted, which releases the stories for a later retry (FR-11).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..engine.models import Channel, DeliveryKind, iso_utc

__all__ = ["ComposedMessage", "DeliveryError", "MessageItem", "Notifier"]


class DeliveryError(RuntimeError):
    """A notifier failed to deliver a message."""


@dataclass(frozen=True, slots=True)
class MessageItem:
    """One cited story, for structured channels (webhook payload, §3.2)."""

    ticker: str
    story_id: int
    article_id: int
    url: str
    title: str
    relevance: int
    published_at: datetime

    def payload(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "story_id": self.story_id,
            "url": self.url,
            "title": self.title,
            "relevance": self.relevance,
            "published_at": iso_utc(self.published_at),
        }


@dataclass(frozen=True, slots=True)
class ComposedMessage:
    """A persisted delivery, ready to send.

    ``delivery_id`` is always set: FR-11 mandates compose -> persist -> send ->
    mark, so a message only reaches a notifier after its audit row exists.
    """

    channel: Channel
    kind: DeliveryKind
    delivery_id: int
    subject: str
    body_text: str
    created_at: datetime
    items: Sequence[MessageItem] = field(default_factory=tuple)


@runtime_checkable
class Notifier(Protocol):
    """Delivers a composed message on one channel."""

    def deliver(self, message: ComposedMessage) -> None: ...
