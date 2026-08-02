"""Offline :class:`MediaResolver` — the committed manifest plus local files.

FR-2's integrity rule, implemented exactly:

* ``source = local`` assets are verified by **file existence** under the data
  directory;
* ``source = url`` assets are verified by **manifest schema only** — a
  well-formed https URL and a non-empty license — and are *never fetched*.

That is what makes ``formcoach init`` hermetic.  HTTP verification lives only in
the env-gated :mod:`formcoach.adapters.media_wger` resolver.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from formcoach.models import MediaAsset, MediaSource


class LocalMediaResolver:
    """Resolves against the committed manifest; touches only the local disk."""

    name = "local"

    def __init__(self, assets: Sequence[MediaAsset], data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self._by_exercise: dict[str, list[MediaAsset]] = {}
        for asset in assets:
            self._by_exercise.setdefault(asset.exercise_id, []).append(asset)

    def resolve(self, exercise_id: str) -> list[MediaAsset]:
        return list(self._by_exercise.get(exercise_id, []))

    def local_path(self, asset: MediaAsset) -> Path | None:
        """Filesystem path of a ``source = local`` asset."""
        if asset.source is not MediaSource.LOCAL:
            return None
        return self.data_dir / asset.ref

    def verify(self, asset: MediaAsset) -> bool:
        if asset.source is MediaSource.LOCAL:
            path = self.local_path(asset)
            return path is not None and path.is_file()
        # URL assets: schema-only verification.  The model already rejects a
        # non-https ref and an empty license at load time, so re-checking those
        # here keeps the rule visible at the point it is applied.
        return asset.ref.startswith("https://") and bool(asset.license.strip())
