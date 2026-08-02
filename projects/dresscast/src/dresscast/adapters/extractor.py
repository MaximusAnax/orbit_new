"""Attribute extractors (SCOPE.md FR-2, §Architecture-Adapters).

Suggestions are always staged and human-confirmed: a mistagged clo poisons
every future recommendation, so no adapter ever writes garment attributes
directly (D14).

``FixtureAttributeExtractor`` is the offline default — a committed JSON map
keyed by photo SHA-256, falling back to the file's basename.
``VisionAttributeExtractor`` is the live path; it is deferred to the next phase
(SCOPE.md §Non-goals), imports its optional dependency lazily, and raises a
clear, actionable error when that dependency or its configuration is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from dresscast.engine.models import AttributeSuggestionPayload
from dresscast.errors import NoExtractorConfigured

#: Environment variables the live extractor reads (documented in the README).
VISION_MODEL_ENV = "DRESSCAST_VISION_MODEL"
VISION_WEIGHTS_ENV = "DRESSCAST_VISION_WEIGHTS"


@runtime_checkable
class AttributeExtractor(Protocol):
    """Proposes garment attributes from a photo.  Never mutates a garment."""

    name: str

    def extract(self, photo_path: str) -> AttributeSuggestionPayload | None:
        """Return proposed fields with per-field confidence, or None."""
        ...


def photo_sha256(path: str | Path) -> str:
    """SHA-256 of a photo file, as recorded on the garment (FR-2)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FixtureAttributeExtractor:
    """Deterministic offline extractor backed by a committed JSON map.

    The map's keys are photo SHA-256 digests; a basename key is accepted as a
    fallback so fixtures stay readable.  Each value is
    ``{"fields": {...}, "confidences": {...}}`` or the bare ``fields`` mapping.
    """

    name = "fixture"

    def __init__(self, mapping: dict[str, Any] | str | Path) -> None:
        if isinstance(mapping, (str, Path)):
            path = Path(mapping)
            self.mapping: dict[str, Any] = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
        else:
            self.mapping = dict(mapping)

    def _lookup(self, photo_path: str) -> dict[str, Any] | None:
        path = Path(photo_path)
        if path.exists():
            entry = self.mapping.get(photo_sha256(path))
            if entry is not None:
                return entry
        return self.mapping.get(path.name)

    def extract(self, photo_path: str) -> AttributeSuggestionPayload | None:
        entry = self._lookup(photo_path)
        if entry is None:
            return None
        if "fields" in entry:
            fields = dict(entry["fields"])
            confidences = dict(entry.get("confidences", {}))
        else:
            fields = {k: v["value"] for k, v in entry.items()}
            confidences = {k: float(v.get("confidence", 1.0)) for k, v in entry.items()}
        return AttributeSuggestionPayload(fields=fields, confidences=confidences)


class NullAttributeExtractor:
    """The "no extractor configured" default (FR-2/US-2).

    Manual tagging is the product's floor and works with this in place; asking
    for a suggestion fails loudly rather than silently returning nothing.
    """

    name = "none"

    def extract(self, photo_path: str) -> AttributeSuggestionPayload | None:
        raise NoExtractorConfigured(
            "no attribute extractor is configured; tag the garment manually "
            "or configure one in ~/.dresscast/config.toml",
            photo_path=photo_path,
        )


#: The zero-shot vocabulary the live extractor scores a photo against.  Kept
#: beside the adapter so the prompt set is reviewable without the dependency.
VISION_CATEGORY_PROMPTS: dict[str, str] = {
    "tshirt": "a photo of a t-shirt",
    "shirt_long_sleeve": "a photo of a long-sleeve button-down shirt",
    "flannel_shirt": "a photo of a flannel shirt",
    "sweater_thin": "a photo of a thin knit sweater",
    "sweater_thick": "a photo of a thick knit sweater",
    "fleece": "a photo of a fleece jacket",
    "blazer": "a photo of a blazer",
    "rain_shell": "a photo of a waterproof rain shell",
    "light_jacket": "a photo of a light jacket",
    "wool_coat": "a photo of a wool overcoat",
    "parka": "a photo of an insulated parka",
    "jeans": "a photo of denim jeans",
    "trousers_thin": "a photo of lightweight trousers",
    "shorts": "a photo of shorts",
    "dress_light": "a photo of a light dress",
    "boots": "a photo of leather boots",
    "sneakers": "a photo of sneakers",
}


class VisionAttributeExtractor:
    """Live zero-shot extractor (CLIP-style), gated behind the ``vision`` extra.

    Deferred to the next phase (SCOPE.md §Non-goals).  The dependency is
    imported lazily inside :meth:`extract`, so importing this module — or the
    whole package — never pulls in a heavy ML stack, and the offline path never
    touches it.  Whatever it proposes still goes through FR-2's human
    confirmation before it can touch a garment.
    """

    name = "vision"

    def __init__(self, model: str | None = None, weights: str | None = None) -> None:
        self.model = model or os.environ.get(VISION_MODEL_ENV, "")
        self.weights = weights or os.environ.get(VISION_WEIGHTS_ENV, "")

    def available(self) -> bool:
        """Is the optional dependency importable and the model configured?"""
        if not self.model:
            return False
        try:  # pragma: no cover - depends on an optional extra
            import importlib.util

            return importlib.util.find_spec("open_clip") is not None
        except ImportError:  # pragma: no cover
            return False

    def extract(self, photo_path: str) -> AttributeSuggestionPayload | None:
        if not self.model:
            raise NoExtractorConfigured(
                f"set {VISION_MODEL_ENV} to a CLIP-style model name to enable the vision extractor",
                env=VISION_MODEL_ENV,
            )
        try:  # pragma: no cover - requires the optional 'vision' extra
            import open_clip  # type: ignore[import-not-found]
            import torch  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise NoExtractorConfigured(
                "the vision extractor needs the optional 'vision' extra "
                "(pip install 'dresscast[vision]')",
                missing=str(exc),
            ) from exc

        # pragma: no cover - the whole block below needs the optional extra
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model, pretrained=self.weights or None
        )
        tokenizer = open_clip.get_tokenizer(self.model)
        categories = list(VISION_CATEGORY_PROMPTS)
        prompts = [VISION_CATEGORY_PROMPTS[c] for c in categories]
        image = preprocess(Image.open(photo_path).convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            image_features = model.encode_image(image)
            text_features = model.encode_text(tokenizer(prompts))
            image_features /= image_features.norm(dim=-1, keepdim=True)
            text_features /= text_features.norm(dim=-1, keepdim=True)
            probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)[0]
        best = int(probs.argmax())
        return AttributeSuggestionPayload(
            fields={"category": categories[best]},
            confidences={"category": float(probs[best])},
        )
