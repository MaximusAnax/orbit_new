"""Live notifier: HTTP POST of a Slack-compatible payload (SCOPE FR-12).

Payload shape is DATA_MODEL §3.2: ``{"text": body, "items": [...]}``. Slack
renders ``text``; richer consumers read ``items``, whose ``relevance`` is the
same story-level value carried by the matching ``delivery_items`` row.

Environment: ``TICKERPRESS_WEBHOOK_URL`` (required). Needs the optional ``live``
extra (httpx); the offline notifiers are the default everywhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .notify import ComposedMessage, DeliveryError

__all__ = ["WebhookNotifier"]

ENV_URL = "TICKERPRESS_WEBHOOK_URL"


@dataclass(frozen=True, slots=True)
class WebhookNotifier:
    url: str
    timeout: float = 10.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> WebhookNotifier | None:
        """Build from environment, or ``None`` when not configured."""

        source = os.environ if env is None else env
        url = source.get(ENV_URL, "").strip()
        if not url:
            return None
        if not url.lower().startswith(("http://", "https://")):
            raise DeliveryError(f"{ENV_URL} must be an http(s) url, got {url!r}")
        return cls(url=url)

    def build_payload(self, message: ComposedMessage) -> dict[str, Any]:
        return {
            "text": message.body_text,
            "items": [item.payload() for item in message.items],
        }

    def deliver(self, message: ComposedMessage) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise DeliveryError(
                "WebhookNotifier needs the optional 'live' extra: pip install 'tickerpress[live]'"
            ) from exc
        try:  # pragma: no cover - requires network
            response = httpx.post(self.url, json=self.build_payload(message), timeout=self.timeout)
        except Exception as exc:
            raise DeliveryError(f"webhook delivery failed: {exc}") from exc
        if response.status_code >= 400:  # pragma: no cover - requires network
            raise DeliveryError(f"webhook delivery failed: HTTP {response.status_code}")
