"""Live ``GoalParser``: Claude turns an utterance into the same ``GoalSpec`` (FR-5).

This is the one live adapter that is **in scope** for this pass (SCOPE, adapter
table).  It is strictly a natural-language front door: it never ranks, values or
explains anything, it is never on the eval path, and its output is validated by
exactly the same Pydantic/goal-builder code the offline parser uses — so an
LLM cannot smuggle an invalid goal into the engine.

Activation is gated twice: the optional ``llm`` extra must be installed (the
``anthropic`` SDK is imported lazily, never at module load of the offline path)
and ``ANTHROPIC_API_KEY`` must be set.  Either missing raises a clear error.

    uv pip install 'pointsmax[llm]'
    export ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import os
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from ..engine.goals import cash_goal, flight_goal, stay_goal
from ..models import Cabin, GoalSpec, ParseError, World

#: Claude model used for parsing.  Small, cheap, deterministic task.
DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You convert a traveller's free-text request into a structured goal for a \
credit-card points planner. You only extract fields; you never plan, price, \
rank or advise.

Rules:
- kind is "flight", "stay" or "cash". "cash" means the user wants to liquidate \
points for money.
- origin_city / dest_city / city are three-letter city codes drawn ONLY from \
the provided gazetteer. Never invent a code.
- month is "YYYY-MM" and must be the next occurrence on or after today's month \
unless the user names a year.
- If a required field is genuinely absent, set it to null and list its name in \
missing. Do not guess.
"""


class LLMGoalDraft(BaseModel):
    """The narrow JSON shape Claude is asked for; never used by the engine directly."""

    model_config = {"extra": "forbid"}

    kind: Literal["flight", "stay", "cash"] | None = None
    origin_city: str | None = None
    dest_city: str | None = None
    city: str | None = None
    cabin: Literal["economy", "premium_economy", "business", "first"] | None = None
    round_trip: bool | None = None
    passengers: int | None = None
    nights: int | None = None
    month: str | None = None
    missing: list[str] = Field(default_factory=list)


class LLMGoalParser:
    """Parse free text with Claude, then validate through the offline goal builders."""

    def __init__(
        self,
        world: World,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self._world = world
        self._model = model
        self._max_tokens = max_tokens
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "LLMGoalParser needs ANTHROPIC_API_KEY (or an explicit api_key); "
                "use RuleBasedGoalParser for the offline path."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "LLMGoalParser requires the optional 'llm' extra: "
                "install pointsmax[llm] (provides the anthropic SDK)."
            ) from exc
        self._client = anthropic.Anthropic(api_key=key)

    def _gazetteer_hint(self) -> str:
        lines = []
        for entry in self._world.gazetteer:
            aliases = ", ".join([entry.name, *entry.aliases, *entry.airports])
            lines.append(f"{entry.city_code}: {aliases}")
        return "\n".join(lines)

    def parse(
        self,
        text: str,
        *,
        today: date,
        home_city: str | None = None,
        default_passengers: int = 1,
    ) -> GoalSpec | ParseError:
        prompt = (
            f"Today is {today.isoformat()}.\n"
            f"Known cities (code: aliases):\n{self._gazetteer_hint()}\n\n"
            f"Request: {text}"
        )
        response = self._client.messages.parse(  # pragma: no cover - network path
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=LLMGoalDraft,
        )
        draft = response.parsed_output  # pragma: no cover - network path
        if draft is None:  # pragma: no cover - network path
            return ParseError(
                raw_text=text,
                message="the language model did not return a parsable goal",
                missing=["kind"],
            )
        return self.build(draft, text, default_passengers=default_passengers)

    def build(
        self, draft: LLMGoalDraft, text: str, *, default_passengers: int = 1
    ) -> GoalSpec | ParseError:
        """Validate a model draft through the same builders the rule parser uses."""
        if draft.kind is None or draft.missing:
            return ParseError(
                raw_text=text,
                message="could not resolve " + ", ".join(draft.missing or ["kind"]),
                missing=sorted(draft.missing) or ["kind"],
            )
        try:
            if draft.kind == "cash":
                return cash_goal(raw_text=text)
            if draft.kind == "stay":
                if not (draft.city and draft.nights and draft.month):
                    raise ValueError("stay goals need city, nights and month")
                return stay_goal(
                    city=draft.city, nights=draft.nights, month=draft.month, raw_text=text
                )
            if not (draft.origin_city and draft.dest_city and draft.month):
                raise ValueError("flight goals need origin_city, dest_city and month")
            return flight_goal(
                origin_city=draft.origin_city,
                dest_city=draft.dest_city,
                month=draft.month,
                cabin=Cabin(draft.cabin) if draft.cabin else None,
                round_trip=bool(draft.round_trip),
                passengers=draft.passengers or default_passengers,
                raw_text=text,
            )
        except (ValueError, TypeError) as exc:
            return ParseError(
                raw_text=text,
                message=f"the language model returned an invalid goal: {exc}",
            )
