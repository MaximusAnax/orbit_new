"""FR-10 at the service boundary: a personalizer can never malform a card."""

from __future__ import annotations

import datetime as dt

from almanac.adapters.personalizer import PersonalizerError
from almanac.models import CardContext

START = dt.date(2026, 1, 1)


def stamp(hour: int = 8) -> dt.datetime:
    return dt.datetime.combine(START, dt.time(hour), dt.UTC)


class BrokenPersonalizer:
    """Returns structurally invalid output for every card."""

    name = "broken"
    personalizes = True

    def __init__(self, output: str = "") -> None:
        self.output = output

    def personalize(self, context: CardContext) -> str:
        return self.output


class ExplodingPersonalizer:
    name = "exploding"
    personalizes = True

    def personalize(self, context: CardContext) -> str:
        raise PersonalizerError("no credentials")


class ShoutingPersonalizer:
    """A well-formed rewrite: keeps the kind signature and any excerpt."""

    name = "shouting"
    personalizes = True

    def personalize(self, context: CardContext) -> str:
        # Structurally identical to the template rendering, so it validates.
        return context.rendered_prompt


def _surface(service, clock):
    service.capture("A line worth returning to", themes=["courage"], captured_on=START, now=stamp())
    clock.set(START)
    return service.materialize_day(START, now=stamp())[0]


def test_fr10_offline_default_serves_the_template_text(service, clock):
    card = _surface(service, clock)
    assert card.surfacing.personalize_fell_back is False
    assert card.surfacing.personalized is False
    assert card.surfacing.prompt_text


def test_fr10_invalid_personalizer_output_falls_back_to_the_template(service, clock):
    service.personalizer = BrokenPersonalizer(output="")
    card = _surface(service, clock)
    assert card.surfacing.personalize_fell_back is True
    assert card.surfacing.personalized is False
    assert "{" not in card.surfacing.prompt_text
    assert card.surfacing.prompt_text.strip()


def test_fr10_slot_leaking_output_falls_back(service, clock):
    service.personalizer = BrokenPersonalizer(output="What would {author} do here?")
    card = _surface(service, clock)
    assert card.surfacing.personalize_fell_back is True
    assert "{author}" not in card.surfacing.prompt_text


def test_fr10_a_raising_personalizer_falls_back_silently(service, clock):
    service.personalizer = ExplodingPersonalizer()
    card = _surface(service, clock)
    assert card.surfacing.personalize_fell_back is True
    assert card.surfacing.prompt_text


def test_fr10_a_valid_rewrite_is_marked_personalized(service, clock):
    service.personalizer = ShoutingPersonalizer()
    card = _surface(service, clock)
    assert card.surfacing.personalize_fell_back is False
    assert card.surfacing.personalized is True
