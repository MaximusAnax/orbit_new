"""The ``PromptPersonalizer`` capability (FR-10).

A personalizer may rewrite the rendered application prompt using the entry's
text, note and source.  Its output — including the offline identity
implementation's — always passes the FR-10 validator before it is shown; a
rejected output is discarded in favour of the template rendering.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from almanac.models import CardContext


class PersonalizerError(RuntimeError):
    """A personalizer could not produce output (network, credentials, quota)."""


@runtime_checkable
class PromptPersonalizer(Protocol):
    """Rewrites a rendered prompt. Never trusted: the validator is the boundary."""

    #: Stamped on the surfacing so history records which path produced the text.
    name: str

    def personalize(self, context: CardContext) -> str:  # pragma: no cover - protocol
        ...
