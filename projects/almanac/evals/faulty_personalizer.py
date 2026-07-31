"""Eval-only adversarial personalizer (EVALS.md M9).

Wraps the template renderer and applies exactly one scripted mutation, each
invalid *by construction* under one FR-10 check.  This lives in ``evals/`` and
is never importable from the product's offline path.

The mutations are the ground truth for M9: the truth of "this output is
malformed" comes from the mutation's definition, not from the validator that is
being measured.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from almanac.models import CardContext

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Longer than the FR-10 400-character ceiling when appended.
_FILLER = (
    " Consider, additionally, the whole shape of the week, the people in it, "
    "the promises you made and the ones you quietly let go, and everything "
    "else that could conceivably bear on this, at length, without stopping, "
    "and then keep considering it well past the point where it helps anyone, "
    "including the parts that plainly repeat what has already been said here."
)


def _empty(text: str, excerpt: str | None) -> str:
    return ""


def _too_long_chars(text: str, excerpt: str | None) -> str:
    out = text
    while len(out) <= 400:
        out += _FILLER
    return out


def _too_many_words(text: str, excerpt: str | None) -> str:
    filler = " ".join(["and"] * 61)
    return f"{filler} {text}"


def _embedded_newline(text: str, excerpt: str | None) -> str:
    head, _, tail = text.partition(" ")
    return f"{head}\n{tail}" if tail else f"{text}\nand then?"


def _unfilled_slot(text: str, excerpt: str | None) -> str:
    return (
        text.replace("today", "today, as {author} put it,", 1)
        if "today" in text
        else (f"As {{author}} wrote: {text}")
    )


def _excerpt_altered(text: str, excerpt: str | None) -> str:
    if not excerpt:
        raise ValueError("the excerpt mutation needs a template that embedded {text_short}")
    words = excerpt.split()
    scrambled = " ".join([*words[:-2], "somewhere", "else"]) if len(words) > 2 else "something else"
    return text.replace(excerpt, scrambled)


def _url_injected(text: str, excerpt: str | None) -> str:
    return f"{text} See https://example.com/notes for more."


def _kind_signature_broken(text: str, excerpt: str | None) -> str:
    """Strip whatever the kind's signature is: the '?' or the if-then scaffold."""
    if text.rstrip().endswith("?"):
        return text.rstrip().rstrip("?").rstrip() + "."
    if "When " in text and " I will " in text:
        return text.replace("When ", "Whenever ", 1).replace(" I will ", " it would be good to ", 1)
    return text + " Or would it?  And then what?"


MUTATIONS: dict[str, Callable[[str, str | None], str]] = {
    "empty": _empty,
    "too_long_chars": _too_long_chars,
    "too_many_words": _too_many_words,
    "embedded_newline": _embedded_newline,
    "unfilled_slot": _unfilled_slot,
    "excerpt_altered": _excerpt_altered,
    "url_injected": _url_injected,
    "kind_signature_broken": _kind_signature_broken,
}


def load_cases() -> list[dict]:
    """The 40 mutation specs plus the 10 report-only off-spec specs."""
    return json.loads((FIXTURES / "faulty_cases.json").read_text(encoding="utf-8"))


def load_clean() -> list[dict]:
    """The 20 committed clean personalizations (M9 false-positive rate)."""
    return json.loads((FIXTURES / "clean_personalizations.json").read_text(encoding="utf-8"))


def apply_case(case: dict) -> str:
    """The personalizer output a case produces, ready for the FR-10 validator."""
    if case["mutation"] == "off_spec":
        return case["replacement"]
    return MUTATIONS[case["mutation"]](case["base_prompt"], case["excerpt"])


class FaultyPersonalizer:
    """A ``PromptPersonalizer`` that always malforms its output.

    Used to prove the service-level half of the FR-10 guarantee: a rejected
    output is discarded and the surfacing records ``personalize_fell_back``.
    """

    name = "faulty"
    personalizes = True

    def __init__(self, mutation: str = "url_injected") -> None:
        if mutation not in MUTATIONS:
            raise KeyError(f"unknown mutation {mutation!r}")
        self.mutation = mutation

    def personalize(self, context: CardContext) -> str:
        mutate = MUTATIONS[self.mutation]
        if self.mutation == "excerpt_altered" and not context.text_short:
            # No excerpt to corrupt: fall back to a mutation that always applies.
            return MUTATIONS["url_injected"](context.rendered_prompt, None)
        return mutate(context.rendered_prompt, context.text_short)
