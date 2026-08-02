"""The ``MediaResolver`` capability interface (FR-2).

The library ships *references* to images and video, never bytes (non-goal #2).
Resolving and verifying those references is an external capability because the
live implementation talks to an open exercise database, so it sits behind this
Protocol with an offline implementation that is what ``formcoach init`` and the
evals use.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from formcoach.models import MediaAsset


@runtime_checkable
class MediaResolver(Protocol):
    """Resolve an exercise's media references and verify that they are usable."""

    #: Human-readable adapter name, surfaced in ``init`` output.
    name: str

    def resolve(self, exercise_id: str) -> list[MediaAsset]:
        """Every media asset registered for an exercise."""
        ...

    def verify(self, asset: MediaAsset) -> bool:
        """Whether the asset is usable under this resolver's rules."""
        ...
