"""Live speaker embedder: SpeechBrain ECAPA-TDNN (FR-4, post-MVP).

Interface-fixed now, activated only when the ``voicekin[live-embed]`` extra is
installed. Nothing here is imported by the offline path: torch and speechbrain
are imported inside :meth:`EcapaEmbedder._encoder`, so importing this module
costs nothing and never fails.

Swapping to this embedder changes ``embedder_id``, which changes every profile's
enrollment fingerprint (FR-3) and therefore stops every existing consent from
authorizing until it is re-granted. That is deliberate (SCOPE decision 5).

Environment:
    ``VOICEKIN_ECAPA_SOURCE``  HuggingFace model id (default
                               ``speechbrain/spkrec-ecapa-voxceleb``)
    ``VOICEKIN_ECAPA_SAVEDIR`` local model cache directory
"""

from __future__ import annotations

import os
from typing import Any

from voicekin.adapters.embedder import Embedding, MissingOptionalDependency
from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip
from voicekin.engine.enrollment import l2_normalize

ECAPA_EMBEDDER_ID = "ecapa-voxceleb-v1"
ECAPA_DIMENSION = 192
DEFAULT_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"


def ecapa_available() -> bool:
    """True when the optional extra is importable."""
    try:  # pragma: no cover - depends on an optional extra
        import speechbrain  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


class EcapaEmbedder:
    """192-dimensional ECAPA-TDNN embeddings (Desplanques et al. 2020)."""

    def __init__(self, source: str | None = None, savedir: str | None = None) -> None:
        self.source = source or os.environ.get("VOICEKIN_ECAPA_SOURCE", DEFAULT_SOURCE)
        self.savedir = savedir or os.environ.get(
            "VOICEKIN_ECAPA_SAVEDIR", os.path.join(os.path.expanduser("~"), ".voicekin", "models")
        )
        self._classifier: Any | None = None

    @property
    def embedder_id(self) -> str:
        return ECAPA_EMBEDDER_ID

    @property
    def dimension(self) -> int:
        return ECAPA_DIMENSION

    def _encoder(self) -> tuple[Any, Any]:
        try:
            import torch
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise MissingOptionalDependency(
                "EcapaEmbedder needs the optional extra: pip install 'voicekin[live-embed]'"
            ) from exc
        if self._classifier is None:  # pragma: no cover - requires the model weights
            self._classifier = EncoderClassifier.from_hparams(
                source=self.source, savedir=self.savedir, run_opts={"device": "cpu"}
            )
        return self._classifier, torch

    def embed(self, clip: AudioClip) -> Embedding:
        """Encode a 16 kHz mono clip. Raises if the extra is not installed."""
        if clip.sample_rate != TARGET_SAMPLE_RATE:
            raise ValueError(f"ECAPA expects {TARGET_SAMPLE_RATE} Hz audio")
        classifier, torch = self._encoder()
        signal = torch.tensor(clip.samples, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():  # pragma: no cover - requires the model weights
            encoded = classifier.encode_batch(signal)
        vector = encoded.squeeze().detach().cpu().numpy().tolist()
        return l2_normalize(vector)


__all__ = [
    "DEFAULT_SOURCE",
    "ECAPA_DIMENSION",
    "ECAPA_EMBEDDER_ID",
    "EcapaEmbedder",
    "ecapa_available",
]
