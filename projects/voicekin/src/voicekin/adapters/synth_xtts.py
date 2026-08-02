"""Live synthesizer: XTTS-class zero-shot voice cloning (FR-8, post-MVP).

Interface-fixed now, activated only when the ``voicekin[live-tts]`` extra is
installed. The heavy import happens inside :meth:`XttsSynthesizer._model`, so the
offline path never touches it.

Note the shape of the dependency: XTTS conditions on the *enrollment WAVs*, not
on the four derived voice parameters, so a live deployment must hand this adapter
the speaker reference files. They are supplied at construction time by the
service that already holds an :class:`~voicekin.engine.consent.Authorization` —
the consent gate is upstream of this class and is not weakened by it.

Environment:
    ``VOICEKIN_XTTS_MODEL``  Coqui TTS model id (default
                             ``tts_models/multilingual/multi-dataset/xtts_v2``)
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import numpy as np

from voicekin.adapters.embedder import MissingOptionalDependency
from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip
from voicekin.models import VoiceParams

XTTS_SYNTH_ID = "xtts-v2"
DEFAULT_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


def xtts_available() -> bool:
    """True when the optional extra is importable."""
    try:  # pragma: no cover - depends on an optional extra
        import TTS  # noqa: F401
    except ImportError:
        return False
    return True


class XttsSynthesizer:
    """Zero-shot cloning conditioned on a profile's enrollment recordings."""

    def __init__(
        self,
        speaker_wav_paths: Sequence[str],
        *,
        model_name: str | None = None,
        language: str = "en",
    ) -> None:
        if not speaker_wav_paths:
            raise ValueError("XTTS conditioning requires at least one enrollment WAV")
        self.speaker_wav_paths = list(speaker_wav_paths)
        self.model_name = model_name or os.environ.get("VOICEKIN_XTTS_MODEL", DEFAULT_MODEL)
        self.language = language
        self._tts: Any | None = None

    @property
    def synth_id(self) -> str:
        return XTTS_SYNTH_ID

    def _model(self) -> Any:
        try:
            from TTS.api import TTS
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise MissingOptionalDependency(
                "XttsSynthesizer needs the optional extra: pip install 'voicekin[live-tts]'"
            ) from exc
        if self._tts is None:  # pragma: no cover - requires the model weights
            self._tts = TTS(self.model_name)
        return self._tts

    def synthesize(
        self,
        text: str,
        voice_params: VoiceParams,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        seed: int = 0,
    ) -> AudioClip:
        """Render ``text`` in the enrolled voice. Raises without the extra installed.

        ``voice_params`` is unused here — XTTS conditions on audio rather than on
        formant parameters — but stays in the signature because the Protocol, and
        therefore the rendering pipeline, is shared with the offline stub.
        """
        del voice_params
        model = self._model()
        waveform = model.tts(  # pragma: no cover - requires the model weights
            text=text, speaker_wav=self.speaker_wav_paths, language=self.language
        )
        samples = np.asarray(waveform, dtype=np.float64)  # pragma: no cover
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0  # pragma: no cover
        if peak > 1.0:  # pragma: no cover
            samples = samples / peak
        return AudioClip(samples=samples, sample_rate=sample_rate)  # pragma: no cover


__all__ = ["DEFAULT_MODEL", "XTTS_SYNTH_ID", "XttsSynthesizer", "xtts_available"]
