"""Human-readable rendering of transitions and flow reports (US-4, FR-11).

Pure formatting: everything below takes a model and returns text, so the CLI
and the API can share one vocabulary and the stored breakdown reads the same
way months later.
"""

from __future__ import annotations

from flowlist.engine.keys import relation_label
from flowlist.engine.models import (
    CLIFF_THRESHOLD,
    COMPONENT_NAMES,
    SEAMLESS_THRESHOLD,
    CoverageReport,
    FlowReport,
    Transition,
)

#: Every flag the engine can attach to a transition (DATA_MODEL 2.6, extended
#: with ``missing_danceability`` because FR-6 specifies ``missing_*`` for *any*
#: weighted component with absent inputs).
FLAG_LABELS: dict[str, str] = {
    "missing_key": "no key on one side",
    "missing_bpm": "no tempo on one side",
    "missing_energy": "no energy on one side",
    "missing_loudness": "no loudness on one side",
    "missing_danceability": "no danceability on one side",
    "half_time": "half/double-time blend",
    "cliff": f"cliff (score < {CLIFF_THRESHOLD:.2f})",
    "anchored": "anchored endpoint",
}


def flag_label(flag: str) -> str:
    return FLAG_LABELS.get(flag, flag)


def format_signed(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}{suffix}"


def key_phrase(transition: Transition) -> str:
    """``"8A -> 9A (adjacent fifth/fourth)"`` or ``"key unknown"``."""
    if transition.camelot_from is None or transition.camelot_to is None:
        return "key unknown"
    return (
        f"{transition.camelot_from} -> {transition.camelot_to} "
        f"({relation_label(transition.key_relation)})"
    )


def component_phrase(transition: Transition) -> str:
    parts = [
        f"{name}={transition.components[name]:.2f}"
        for name in COMPONENT_NAMES
        if transition.components.get(name) is not None
    ]
    return " ".join(parts)


def explain_transition(transition: Transition, position: int | None = None) -> str:
    """One line per seam: relation, tempo, energy, loudness, components, flags."""
    prefix = "" if position is None else f"{position:>3} -> {position + 1:<3} "
    pieces = [
        key_phrase(transition),
        f"tempo {format_signed(transition.bpm_delta_pct, 1, '%')}",
        f"energy {format_signed(transition.energy_delta)}",
        f"loudness {format_signed(transition.loudness_delta_db, 1, ' dB')}",
        f"[{component_phrase(transition)}]",
        f"total {transition.score:.3f}",
    ]
    line = f"{prefix}{'; '.join(pieces)}"
    if transition.flags:
        line += "  <" + ", ".join(flag_label(flag) for flag in transition.flags) + ">"
    return line


def explain_report(report: FlowReport) -> list[str]:
    """One line per transition, in order."""
    return [
        explain_transition(transition, position)
        for position, transition in enumerate(report.transitions)
    ]


def summarize_report(report: FlowReport, label: str = "order") -> str:
    """A single aggregate line (FR-7)."""
    return (
        f"{label}: n={report.n_entries} mean={report.mean:.3f} min={report.min_score:.3f} "
        f"total={report.total:.3f} seamless={report.seamless} cliffs={report.cliffs}"
    )


def compare_reports(before: FlowReport, after: FlowReport) -> list[str]:
    """The before/after scorecard US-3 asks for."""
    lines = [summarize_report(before, "before"), summarize_report(after, "after ")]
    lines.append(
        "delta: "
        f"mean {format_signed(after.mean - before.mean, 3)} "
        f"min {format_signed(after.min_score - before.min_score, 3)} "
        f"seamless {after.seamless - before.seamless:+d} "
        f"cliffs {after.cliffs - before.cliffs:+d} "
        f"(seamless >= {SEAMLESS_THRESHOLD:.2f}, cliff < {CLIFF_THRESHOLD:.2f})"
    )
    return lines


def summarize_coverage(coverage: CoverageReport) -> str:
    """US-2's ``41/44 tracks fully featured; 3 missing key`` line."""
    return coverage.summary


__all__ = [
    "FLAG_LABELS",
    "compare_reports",
    "component_phrase",
    "explain_report",
    "explain_transition",
    "flag_label",
    "format_signed",
    "key_phrase",
    "summarize_coverage",
    "summarize_report",
]
