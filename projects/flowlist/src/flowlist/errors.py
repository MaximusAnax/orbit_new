"""Domain error catalog.

Every error the engine, store or adapters raise carries a stable machine
readable ``code``.  SCOPE.md FR-13 pins the codes the HTTP layer must be able
to emit; the CLI (FR-14) uses the same codes for non-zero exits.
"""

from __future__ import annotations

from typing import Any


class FlowlistError(Exception):
    """Base class for every flowlist domain error."""

    code: str = "flowlist_error"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context

    def as_detail(self) -> dict[str, Any]:
        """Structured payload for API error bodies (FR-13)."""
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.context:
            detail["context"] = self.context
        return detail


class PlaylistTooLargeError(FlowlistError):
    """More entries than the optimizer's enforced cap (FR-8, n <= 500)."""

    code = "playlist_too_large"


class UnknownTrackError(FlowlistError):
    code = "unknown_track"


class UnknownPlaylistError(FlowlistError):
    code = "unknown_playlist"


class UnknownRunError(FlowlistError):
    code = "unknown_run"


class InvalidWeightsError(FlowlistError):
    """Weights are negative, non-numeric, or all zero (FR-6)."""

    code = "invalid_weights"


class NameConflictError(FlowlistError):
    """Import to an existing playlist name without ``--replace`` (FR-1)."""

    code = "name_conflict"


class PlaylistHasRunsError(FlowlistError):
    """Destructive playlist operation blocked by existing runs (DATA_MODEL 2.3)."""

    code = "playlist_has_runs"


class InvalidProviderError(FlowlistError):
    """A requested feature source / provider name is not one flowlist knows.

    Not in FR-13's minimum catalog; added because ``--providers`` is user input
    and the API needs a 4xx for a typo rather than a 500.
    """

    code = "invalid_providers"


class InvalidAnchorError(FlowlistError):
    """A start/end anchor does not name a distinct node of the instance (FR-9).

    Not in FR-13's minimum catalog; added because anchors are user input and the
    API needs a 4xx for a bad one rather than a 500.
    """

    code = "invalid_anchor"


class InstanceTooLargeError(FlowlistError):
    """Exact solver invoked beyond its Held-Karp guard (FR-8, n <= 14)."""

    code = "instance_too_large"


class PlaylistImportError(FlowlistError):
    """A playlist source could not be parsed at all (FR-1/FR-2)."""

    code = "import_failed"


class AdapterUnavailableError(FlowlistError):
    """A live adapter's credentials or optional dependency are absent."""

    code = "adapter_unavailable"
