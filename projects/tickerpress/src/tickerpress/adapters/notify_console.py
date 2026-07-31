"""Offline notifier: write the body to stdout (SCOPE FR-12)."""

from __future__ import annotations

import sys
from typing import TextIO

from .notify import ComposedMessage, DeliveryError

__all__ = ["ConsoleNotifier"]


class ConsoleNotifier:
    """Print the rendered body verbatim.

    The stream is resolved at delivery time so redirection in tests and in the
    CLI (Typer's captured stdout) works as expected.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def deliver(self, message: ComposedMessage) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        try:
            stream.write(message.body_text)
            if not message.body_text.endswith("\n"):
                stream.write("\n")
            stream.flush()
        except OSError as exc:
            raise DeliveryError(f"console delivery failed: {exc}") from exc
