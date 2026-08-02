"""Offline notifier: write the body to a deterministically named outbox file."""

from __future__ import annotations

from pathlib import Path

from ..engine.models import ensure_utc
from .notify import ComposedMessage, DeliveryError

__all__ = ["FileNotifier"]


class FileNotifier:
    """Write ``<outbox>/<YYYYMMDDTHHMMSSZ>-<kind>-<delivery_id>.md``.

    Contents are the delivery's ``body_text`` byte for byte — the same bytes the
    audit copy in SQLite holds (DATA_MODEL §3.3). The timestamp comes from the
    injected ``now`` carried on the message, so names are deterministic under
    ``FixedClock``.
    """

    def __init__(self, outbox_dir: Path | str) -> None:
        self.outbox_dir = Path(outbox_dir)

    def filename_for(self, message: ComposedMessage) -> str:
        stamp = ensure_utc(message.created_at).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{message.kind.value}-{message.delivery_id}.md"

    def path_for(self, message: ComposedMessage) -> Path:
        return self.outbox_dir / self.filename_for(message)

    def deliver(self, message: ComposedMessage) -> None:
        path = self.path_for(message)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(message.body_text, encoding="utf-8")
        except OSError as exc:
            raise DeliveryError(f"file delivery failed for {path}: {exc}") from exc
