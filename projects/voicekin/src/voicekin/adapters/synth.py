"""FR-8 text-to-speech capability interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voicekin.engine.audio import AudioClip
from voicekin.models import VoiceParams


@runtime_checkable
class Synthesizer(Protocol):
    """Renders normalized text in a profile's voice.

    Implementations must be deterministic in ``(text, voice_params, sample_rate,
    seed)``: FR-15's replay contract and FR-10's provenance both rest on it.
    """

    @property
    def synth_id(self) -> str: ...

    def synthesize(
        self,
        text: str,
        voice_params: VoiceParams,
        *,
        sample_rate: int,
        seed: int,
    ) -> AudioClip: ...


__all__ = ["Synthesizer"]
