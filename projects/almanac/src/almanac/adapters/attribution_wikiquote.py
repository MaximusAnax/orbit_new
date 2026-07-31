"""Live attribution checker: Wikiquote's "Misattributed" sections (FR-2).

Activates only when ``ALMANAC_WIKIQUOTE=1``.  It is best-effort curation, not
scholarship (SCOPE.md non-goal 9): a hit yields a `disputed` verdict pointing
at the author's Wikiquote page so the user can judge for themselves.

Never imported by the offline path; stdlib ``urllib`` only, so it adds no
dependency to the core test and eval suites.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from almanac.engine.normalize import contains_phrase, tokenize
from almanac.models import AttributionFinding, Verdict

DEFAULT_API_URL = "https://en.wikiquote.org/w/api.php"
USER_AGENT = "almanac/0.1 (local quote almanac; attribution check)"
REQUEST_TIMEOUT_SECONDS = 15
#: Sections whose contents mean "this author probably didn't say it".
_SUSPECT_SECTIONS = ("misattributed", "disputed")


class WikiquoteError(RuntimeError):
    """The Wikiquote API could not be reached or returned nothing usable."""


def wikiquote_enabled() -> bool:
    return os.environ.get("ALMANAC_WIKIQUOTE") == "1"


class WikiquoteAttributionChecker:
    """Queries the claimed author's Wikiquote page for a matching quote."""

    name = "wikiquote"

    def __init__(self, api_url: str | None = None, timeout: int = REQUEST_TIMEOUT_SECONDS) -> None:
        if not wikiquote_enabled() and api_url is None:
            raise WikiquoteError("set ALMANAC_WIKIQUOTE=1 to enable the live checker")
        self._api_url = api_url or DEFAULT_API_URL
        self._timeout = timeout

    def check(self, text: str, author: str | None) -> list[AttributionFinding]:
        if not author:
            return []
        page = author.strip().replace(" ", "_")
        try:
            sections = self._fetch_sections(page)
            findings: list[AttributionFinding] = []
            needle = tokenize(text)
            for index, title in sections:
                body = self._fetch_section_text(page, index)
                if not contains_phrase(tokenize(body), needle):
                    continue
                findings.append(
                    AttributionFinding(
                        misattribution_id=None,
                        verdict=Verdict.DISPUTED,
                        likely_origin=f"listed under '{title}' on Wikiquote's {author} page",
                        note=(
                            f"Wikiquote lists this quote in the '{title}' section of its "
                            f"{author} page. Treat the attribution as unverified."
                        ),
                        reference_url=(
                            f"https://en.wikiquote.org/wiki/{page}#{title.replace(' ', '_')}"
                        ),
                    )
                )
            return findings
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise WikiquoteError(f"wikiquote lookup failed: {exc}") from exc

    def _get(self, params: dict[str, str]) -> dict:
        url = f"{self._api_url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _fetch_sections(self, page: str) -> list[tuple[str, str]]:
        body = self._get({"action": "parse", "page": page, "prop": "sections", "format": "json"})
        sections = body.get("parse", {}).get("sections", [])
        return [
            (section["index"], section["line"])
            for section in sections
            if any(word in section.get("line", "").casefold() for word in _SUSPECT_SECTIONS)
        ]

    def _fetch_section_text(self, page: str, index: str) -> str:
        body = self._get(
            {
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "section": index,
                "format": "json",
            }
        )
        return body.get("parse", {}).get("wikitext", {}).get("*", "")
