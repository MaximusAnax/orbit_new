"""Live personalizer: rewrites a rendered prompt with an LLM (FR-10).

Activates only when ``ALMANAC_LLM_API_KEY`` is set.  This module is never
imported by the offline path — ``service`` receives an already-constructed
personalizer — so tests and evals cannot reach the network through it.

Environment (documented in the project README):

* ``ALMANAC_LLM_API_KEY``  — required; absence disables the adapter entirely
* ``ALMANAC_LLM_MODEL``    — default ``claude-opus-5``
* ``ALMANAC_LLM_BASE_URL`` — default ``https://api.anthropic.com``

The transport is stdlib ``urllib`` on purpose: no extra dependency, and the
one network call in the project stays inspectable.  Whatever comes back is
still handed to the FR-10 validator by the service — the LLM can degrade
style, never structure (SCOPE.md D7).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from almanac.adapters.personalizer import PersonalizerError
from almanac.models import CardContext

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT_SECONDS = 20

_SYSTEM_PROMPT = (
    "You rewrite a single application prompt so it speaks to one reader's own "
    "note and source. Return ONLY the rewritten prompt, on one line, under 60 "
    "words. Never add links. Keep any quoted excerpt exactly as given. A "
    "'reflect' or 'connect' prompt must end with a question mark; an 'act' "
    "prompt must contain the scaffold 'When ... I will ...'; a 'reframe' "
    "prompt must not contain a question mark except optionally at the end."
)


def llm_configured() -> bool:
    """True when credentials are present, i.e. the live adapter can be built."""
    return bool(os.environ.get("ALMANAC_LLM_API_KEY"))


class LLMPersonalizer:
    """Rewrites the rendered prompt via the configured LLM provider."""

    name = "llm"
    personalizes = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        key = api_key or os.environ.get("ALMANAC_LLM_API_KEY")
        if not key:
            raise PersonalizerError(
                "ALMANAC_LLM_API_KEY is not set; the LLM personalizer is unavailable"
            )
        self._api_key = key
        self._model = model or os.environ.get("ALMANAC_LLM_MODEL") or DEFAULT_MODEL
        self._base_url = (
            base_url or os.environ.get("ALMANAC_LLM_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout

    def personalize(self, context: CardContext) -> str:
        payload = {
            "model": self._model,
            "max_tokens": 512,
            "output_config": {"effort": "low"},
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": self._user_message(context)}],
        }
        request = urllib.request.Request(
            f"{self._base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PersonalizerError(f"personalizer request failed: {exc}") from exc
        return self._first_text(body)

    @staticmethod
    def _user_message(context: CardContext) -> str:
        lines = [
            f"Prompt kind: {context.prompt_kind.value}",
            f"Rendered prompt: {context.rendered_prompt}",
            f"Entry: {context.entry_text}",
        ]
        if context.entry_author:
            lines.append(f"Author: {context.entry_author}")
        if context.entry_source:
            lines.append(f"Source: {context.entry_source}")
        if context.entry_note:
            lines.append(f"My note: {context.entry_note}")
        if context.theme_names:
            lines.append(f"Themes: {', '.join(context.theme_names)}")
        if context.text_short:
            lines.append(f"Keep this excerpt verbatim: {context.text_short}")
        return "\n".join(lines)

    @staticmethod
    def _first_text(body: dict) -> str:
        for block in body.get("content", []):
            if block.get("type") == "text":
                return str(block.get("text", "")).strip()
        raise PersonalizerError("personalizer response contained no text block")
