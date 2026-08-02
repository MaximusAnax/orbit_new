"""The EncodingDetector port and its two implementations (SCOPE.md FR-4).

The offline cascade — BOM → strict UTF-8 → cp1252 → latin-1 — never fails and
is fully deterministic, which is what evals run against.  The
charset-normalizer adapter exists for the long tail (Shift-JIS, KOI8-R…) and
activates only when the optional ``charset`` extra is installed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from ..errors import MissingDependencyError

UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"


class DetectedEncoding(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    had_bom: bool = False
    confidence: float = 1.0

    def decode(self, data: bytes) -> str:
        """Decode with the detected codec, stripping any BOM."""
        if self.had_bom and self.name == "utf-8":
            return data.decode("utf-8-sig")
        return data.decode(self.name)


@runtime_checkable
class EncodingDetector(Protocol):
    def detect(self, data: bytes) -> DetectedEncoding:  # pragma: no cover - protocol
        ...


class SimpleEncodingDetector:
    """FR-4's cascade.  ``latin-1`` is the terminal case: it never raises."""

    def detect(self, data: bytes) -> DetectedEncoding:
        if data.startswith(UTF8_BOM):
            return DetectedEncoding(name="utf-8", had_bom=True, confidence=1.0)
        if data.startswith(UTF16_LE_BOM) or data.startswith(UTF16_BE_BOM):
            return DetectedEncoding(name="utf-16", had_bom=True, confidence=1.0)
        for name, confidence in (("utf-8", 1.0), ("cp1252", 0.8)):
            try:
                data.decode(name)
            except UnicodeDecodeError:
                continue
            return DetectedEncoding(name=name, had_bom=False, confidence=confidence)
        return DetectedEncoding(name="latin-1", had_bom=False, confidence=0.5)


class CharsetNormalizerDetector:
    """Live adapter (extra ``charset``): delegates to ``charset-normalizer``.

    The result still passes through the same strict-decode validation as the
    offline cascade, and the cascade is used as the fallback whenever the
    library declines to guess.  Never imported by the offline path.
    """

    def __init__(self) -> None:
        try:
            from charset_normalizer import from_bytes  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MissingDependencyError(
                "CharsetNormalizerDetector needs the 'charset' extra: "
                "uv pip install 'datasweep[charset]'"
            ) from exc
        self._fallback = SimpleEncodingDetector()

    def detect(self, data: bytes) -> DetectedEncoding:  # pragma: no cover - live path
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is None or not best.encoding:
            return self._fallback.detect(data)
        name = best.encoding.replace("_", "-")
        try:
            data.decode(name)
        except (UnicodeDecodeError, LookupError):
            return self._fallback.detect(data)
        had_bom = bool(getattr(best, "bom", False))
        confidence = max(0.0, min(1.0, 1.0 - float(best.chaos)))
        return DetectedEncoding(name=name, had_bom=had_bom, confidence=confidence)
