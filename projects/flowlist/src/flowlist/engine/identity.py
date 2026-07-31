"""Stable track identity (SCOPE.md D9).

``spotify:<id>`` when a Spotify URI is present, else ``file:<sha1-of-bytes>``
for local files, else ``meta:<sha1(norm_artist|norm_title)>``.

The engine stays pure: hashing *file bytes* is I/O, so the adapter that reads
the file passes the digest in.  Normalisation lives here because it decides
identity and therefore must be deterministic and testable without a filesystem.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

#: A trailing "feat." clause is credited differently by every source, so it is
#: stripped before hashing (D9).
_FEAT_RE = re.compile(
    r"[\s(\[-]+(feat\.?|ft\.?|featuring|with)\s+.*$",
    re.IGNORECASE,
)
_SPOTIFY_URI_RE = re.compile(r"^spotify:track:([0-9A-Za-z]{22})$")
_SPOTIFY_URL_RE = re.compile(r"^https?://open\.spotify\.com/track/([0-9A-Za-z]{22})")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """casefold -> strip trailing feat. clause -> strip punctuation -> collapse space.

    NFKD decomposition plus dropping combining marks folds accents too
    (``Véra`` and ``Vera`` are the same artist to every catalog that matters),
    which is what keeps the same song from splitting across sources that spell
    diacritics differently.
    """
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    normalized = _FEAT_RE.sub("", without_marks)
    normalized = _PUNCT_RE.sub(" ", normalized)
    return _WS_RE.sub(" ", normalized).strip()


def spotify_id_from_uri(value: str) -> str | None:
    """Extract the base62 id from ``spotify:track:...``, an open.spotify URL, or a bare id."""
    value = value.strip()
    for pattern in (_SPOTIFY_URI_RE, _SPOTIFY_URL_RE):
        match = pattern.match(value)
        if match:
            return match.group(1)
    if re.fullmatch(r"[0-9A-Za-z]{22}", value):
        return value
    return None


def meta_hash(artist: str, title: str) -> str:
    """sha1 of ``normalize(artist) + "|" + normalize(title)``."""
    payload = f"{normalize(artist)}|{normalize(title)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def track_id(
    *,
    spotify_id: str | None = None,
    file_sha1: str | None = None,
    artist: str = "",
    title: str = "",
) -> str:
    """The catalog id for a track, in D9's precedence order."""
    if spotify_id:
        return f"spotify:{spotify_id}"
    if file_sha1:
        return f"file:{file_sha1}"
    if not (artist.strip() or title.strip()):
        raise ValueError("cannot derive a track id without a Spotify id, file hash, or metadata")
    return f"meta:{meta_hash(artist, title)}"


def parse_filename(stem: str) -> tuple[str, str]:
    """``"Vera Lux - Night Drive"`` -> ``("Vera Lux", "Night Drive")`` (FR-2).

    Falls back to an empty artist when the pattern is absent, so a bare
    ``night_drive.flac`` still imports with a usable title.
    """
    if " - " in stem:
        artist, _, title = stem.partition(" - ")
        artist, title = artist.strip(), title.strip()
        if artist and title:
            return artist, title
    return "", stem.strip()


__all__ = [
    "meta_hash",
    "normalize",
    "parse_filename",
    "spotify_id_from_uri",
    "track_id",
]
