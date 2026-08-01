"""ProsePolisher Protocol + offline default NullPolisher (FR-9).

The polisher sees only the envelope; whatever it returns is parsed back and
re-verified (FR-8) before anything reaches the user. The live LLM adapter
lives in polisher_llm.py and is never imported on the test/eval path.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProsePolisher(Protocol):
    def polish(self, envelope: str) -> str: ...


class NullPolisher:
    """Identity polish — the offline default."""

    def polish(self, envelope: str) -> str:
        return envelope
