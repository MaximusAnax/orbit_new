"""FR-4 speaker-embedding capability interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voicekin.engine.audio import AudioClip

Embedding = list[float]
"""A speaker embedding. Scored by cosine similarity, so scale is irrelevant."""


class MissingOptionalDependency(RuntimeError):
    """A live adapter was used without its optional extra installed."""


@runtime_checkable
class SpeakerEmbedder(Protocol):
    """Maps a clip to a fixed-dimension speaker embedding.

    ``embedder_id`` is versioned and participates in the enrollment fingerprint
    (FR-3), so swapping embedders invalidates every prior consent by design.
    """

    @property
    def embedder_id(self) -> str: ...

    def embed(self, clip: AudioClip) -> Embedding: ...


__all__ = ["Embedding", "MissingOptionalDependency", "SpeakerEmbedder"]
