"""``GoalParser``: free text to a validated ``GoalSpec`` (FR-5)."""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..engine.parser import parse_goal_text
from ..models import GoalSpec, ParseError, World


@runtime_checkable
class GoalParser(Protocol):
    """Turns an utterance into a ``GoalSpec`` or a structured ``ParseError``."""

    def parse(
        self,
        text: str,
        *,
        today: date,
        home_city: str | None = None,
        default_passengers: int = 1,
    ) -> GoalSpec | ParseError: ...


class RuleBasedGoalParser:
    """Offline default: gazetteer alias matching plus FR-5's rule set.

    Deterministic and hermetic — ``today`` is an input, the vocabulary is the
    world's own gazetteer, and no network or model is involved.
    """

    def __init__(self, world: World) -> None:
        self._world = world

    def parse(
        self,
        text: str,
        *,
        today: date,
        home_city: str | None = None,
        default_passengers: int = 1,
    ) -> GoalSpec | ParseError:
        return parse_goal_text(
            text,
            world=self._world,
            today=today,
            home_city=home_city,
            default_passengers=default_passengers,
        )
