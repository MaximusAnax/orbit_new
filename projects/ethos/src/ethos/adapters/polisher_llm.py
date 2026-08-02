"""Live LLM polish adapter (FR-9). Optional; activates only when
ETHOS_LLM_API_KEY is set. Never imported by tests or evals. The model is
assumed adversarial: its output is parsed back and re-verified by FR-8, so a
misbehaving model can only ever degrade prose, never integrity.

Env: ETHOS_LLM_API_KEY (required), ETHOS_LLM_MODEL (default
claude-3-5-haiku-latest), ETHOS_LLM_BASE_URL (default Anthropic API).
"""
from __future__ import annotations

import json
import os
import urllib.request

_PROMPT = (
    "You are a prose editor. The document below is an envelope with regions "
    "delimited by [[...]] sentinels. You may lightly rephrase ONLY the text "
    "inside [[M:...]] regions for smoothness, preserving meaning. You must "
    "return the entire envelope with every [[I:...]] region, every sentinel, "
    "and the region order byte-for-byte unchanged. Never add citations, "
    "locators, or references of any kind. Return only the envelope.\n\n"
)


class LLMPolisher:
    def __init__(self) -> None:
        self.api_key = os.environ["ETHOS_LLM_API_KEY"]
        self.model = os.environ.get("ETHOS_LLM_MODEL", "claude-3-5-haiku-latest")
        self.base_url = os.environ.get("ETHOS_LLM_BASE_URL", "https://api.anthropic.com")

    def polish(self, envelope: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": _PROMPT + envelope}],
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        return "".join(
            block.get("text", "") for block in data.get("content", []) if isinstance(block, dict)
        )


def polisher_available() -> bool:
    return bool(os.environ.get("ETHOS_LLM_API_KEY"))
