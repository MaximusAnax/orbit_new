"""Loading of the committed ``data/`` lexicons (SCOPE architecture, D6).

This is the **only** module in the package that reads a file. Everything under
``engine/`` receives the loaded :class:`Lexicons` value as an explicit argument,
which is what keeps the engine a pure function of its inputs and lets tests
inject synthetic lexicons without touching the filesystem.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

__all__ = [
    "LEXICON_FILES",
    "Lexicons",
    "data_root",
    "lexicon_digests",
    "load_lexicons",
    "parse_lexicon_lines",
]

#: Logical name -> file name under ``data/``. Frozen; hashes of these files are
#: committed to ``evals/fixtures/lexicon_manifest.json`` (SCOPE D19).
LEXICON_FILES: dict[str, str] = {
    "corporate_cues": "cues_corporate.txt",
    "anti_cues": "cues_anti.txt",
    "common_words": "common_words.txt",
    "legal_suffixes": "legal_suffixes.txt",
    "abbreviations": "abbreviations.txt",
    "tracking_params": "tracking_params.txt",
    "digest_template": "digest_template.md",
}


@dataclass(frozen=True, slots=True)
class Lexicons:
    """Immutable bundle of every committed lexicon.

    Cue entries, common words and abbreviations are stored case-folded because
    every consumer matches them case-insensitively (SCOPE FR-6 rule 3).
    ``legal_suffixes`` keeps file order so suffix stripping is deterministic.
    """

    corporate_cues: frozenset[str]
    anti_cues: frozenset[str]
    common_words: frozenset[str]
    legal_suffixes: tuple[str, ...]
    abbreviations: frozenset[str]
    tracking_params: tuple[str, ...]
    digest_template: str


def parse_lexicon_lines(text: str) -> list[str]:
    """Return the meaningful entries of a lexicon file.

    Blank lines and ``#`` comment lines are dropped; entries are stripped and
    case-folded. Order is preserved and duplicates are removed, so callers that
    need a sequence (legal suffixes) and callers that need a set (cues) share
    one parser.
    """

    seen: dict[str, None] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        seen.setdefault(line.casefold(), None)
    return list(seen)


def data_root() -> Path:
    """Locate the committed ``data/`` directory.

    Preferred anchor is the installed package (``importlib.resources``), which
    is what a built wheel carries. In a source checkout the directory lives
    beside ``src/`` (SCOPE architecture tree), so that layout is the fallback.
    """

    package_dir = Path(str(resources.files("tickerpress")))
    candidates = (package_dir / "data", package_dir.parents[1] / "data")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "tickerpress data/ directory not found; looked in: "
        + ", ".join(str(c) for c in candidates)
    )


def _read(name: str) -> str:
    path = data_root() / LEXICON_FILES[name]
    if not path.is_file():
        raise FileNotFoundError(f"missing tickerpress lexicon: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_lexicons() -> Lexicons:
    """Read every ``data/`` file once and return the immutable bundle."""

    return Lexicons(
        corporate_cues=frozenset(parse_lexicon_lines(_read("corporate_cues"))),
        anti_cues=frozenset(parse_lexicon_lines(_read("anti_cues"))),
        common_words=frozenset(parse_lexicon_lines(_read("common_words"))),
        legal_suffixes=tuple(parse_lexicon_lines(_read("legal_suffixes"))),
        abbreviations=frozenset(parse_lexicon_lines(_read("abbreviations"))),
        tracking_params=tuple(parse_lexicon_lines(_read("tracking_params"))),
        digest_template=_read("digest_template"),
    )


def lexicon_digests() -> dict[str, str]:
    """sha256 of each committed lexicon file, keyed by file name.

    Used by ``tickerpress init`` (report) and by eval gate M6_lex (freeze
    protocol, SCOPE D19).
    """

    root = data_root()
    digests: dict[str, str] = {}
    for file_name in sorted(LEXICON_FILES.values()):
        digests[file_name] = hashlib.sha256((root / file_name).read_bytes()).hexdigest()
    return digests


def lexicon_entry_counts(lexicons: Lexicons) -> dict[str, int]:
    """Entry count per lexicon, for the ``init`` report."""

    return {
        "cues_corporate.txt": len(lexicons.corporate_cues),
        "cues_anti.txt": len(lexicons.anti_cues),
        "common_words.txt": len(lexicons.common_words),
        "legal_suffixes.txt": len(lexicons.legal_suffixes),
        "abbreviations.txt": len(lexicons.abbreviations),
        "tracking_params.txt": len(lexicons.tracking_params),
        "digest_template.md": len(lexicons.digest_template.splitlines()),
    }
