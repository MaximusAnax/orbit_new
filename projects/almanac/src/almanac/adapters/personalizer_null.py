"""Offline default personalizer: the identity on the rendered template (FR-10).

This is what tests and evals use.  It still goes through the validator, which
is what makes "even the default path is checked" true rather than aspirational.
"""

from __future__ import annotations

from almanac.models import CardContext


class TemplatePersonalizer:
    """Returns the rendered template unchanged."""

    name = "template"
    #: The identity personalizer does not claim its output is personalized.
    personalizes = False

    def personalize(self, context: CardContext) -> str:
        return context.rendered_prompt
