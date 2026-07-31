"""Metric implementations for EVALS.md M1-M9.

**Independence rule (EVALS.md sections 3 and 6).** This module must not import
from ``almanac.engine``.  Everything it needs — the FR-6 fold, the FR-9
candidate pools, theme membership, eligibility, pool classification — is
re-derived here from the committed data files and the timeline, so a predicate
can never inherit the bug it exists to catch.  ``test_gates.py`` asserts the
import ban mechanically.

The two capabilities that *are* the system under test (the FR-3 suggester for
M8, the FR-10 validator for M9) are passed in as callables by the caller, which
keeps this module engine-free while still measuring the real code.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

#: EVALS.md section 4: the frozen per-scenario backlog-drain bound for M2b.
L_BOUND = {"S1": 120, "S2": 150, "S3": 20, "S4": 135}
#: EVALS.md M2: no entry anywhere may wait longer than this, ever.
HARD_LATENCY_BOUND = 180
#: EVALS.md M3: warm-up slots discarded before the first window.
SIGMA_WARMUP = 28
#: EVALS.md M4/M6: steady-state and cohort horizons, in days from day 0.
STEADY_STATE_DAY = 60
COHORT_CAPTURE_DAY = 90
COHORT_EXPOSURE_DAY = 165
#: EVALS.md M5: US-5's promise, and the minimum length of a measured interval.
PINNED_GAP_DAYS = 45
MIN_PINNED_INTERVAL_DAYS = 60
#: EVALS.md M2a: entries captured inside this window of the run end are censored.
CENSORING_GUARD_DAYS = 60

CONTESTED = ("novelty", "review")
GENERAL = "general"


# --------------------------------------------------------------------------
# Result plumbing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: float | int | str
    gate: str
    passed: bool
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        rendered = f"{self.value:.4g}" if isinstance(self.value, float) else str(self.value)
        return f"  {mark}  {self.name:<28} {rendered:>10}   gate {self.gate:<12} {self.detail}"


@dataclass
class EvalReport:
    results: list[MetricResult] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)

    def add(self, result: MetricResult) -> MetricResult:
        self.results.append(result)
        return result

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)


# --------------------------------------------------------------------------
# Committed data, re-read here rather than imported
# --------------------------------------------------------------------------


def load_params() -> dict:
    return json.loads((DATA / "scheduler.json").read_text(encoding="utf-8"))


def load_templates() -> dict[str, dict]:
    rows = json.loads((DATA / "prompts.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def load_theme_ids() -> set[str]:
    return {row["id"] for row in json.loads((DATA / "themes.json").read_text(encoding="utf-8"))}


# --------------------------------------------------------------------------
# Independent re-implementation of the FR-6 fold (M7c, and M4's I_eff)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldState:
    exposure_count: int
    last_surfaced_on: dt.date | None
    interval_days: int
    flat_streak: int


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fold_entry(events: Sequence[tuple[dt.date, str | None]], params: dict) -> FoldState:
    """The DATA_MODEL.md section SchedulerState fold, written from the spec.

    ``events`` is the entry's surfacings in order, each paired with the grade of
    *that* surfacing's reflection (or ``None``).  Step ``i`` applies the grade of
    step ``i-1``.
    """
    clamp = params["clamp"]
    state = FoldState(0, None, params["I0"], 0)
    prior: str | None = None
    for on_date, grade in events:
        if state.exposure_count == 0:
            state = FoldState(1, on_date, params["I0"], 0)
        else:
            if prior == "flat":
                streak = state.flat_streak + 1
            elif prior is None:
                streak = state.flat_streak
            else:
                streak = 0
            if streak >= params["demote_flat_streak"]:
                interval = clamp["hi_flat"]
            else:
                multiplier = params["multipliers"]["none" if prior is None else prior]
                scaled = _round_half_up(Decimal(state.interval_days) * Decimal(str(multiplier)))
                interval = max(clamp["lo"], min(clamp["hi"], scaled))
            state = FoldState(state.exposure_count + 1, on_date, interval, streak)
        prior = grade
    return state


def fold_steps(
    events: Sequence[tuple[dt.date, str | None]], params: dict
) -> list[tuple[dt.date, FoldState]]:
    """The state *before* each surfacing, so a surfacing can be scored fairly."""
    out: list[tuple[dt.date, FoldState]] = []
    for index, (on_date, _) in enumerate(events):
        out.append((on_date, fold_entry(events[:index], params)))
    return out


def effective_interval(state: FoldState, pinned: bool, params: dict) -> int:
    base = min(state.interval_days, params["pinned_cap"]) if pinned else state.interval_days
    return max(params["W"], base)


# --------------------------------------------------------------------------
# Timeline views
# --------------------------------------------------------------------------


def ordered_surfacings(timeline) -> list:
    """Chronological order: daily slots first on each date, then extra draws."""
    return sorted(
        timeline.surfacings,
        key=lambda s: (s.on_date, 1 if s.kind.value == "extra" else 0, s.slot, s.created_at),
    )


def events_by_entry(timeline) -> dict[str, list[tuple[dt.date, str | None]]]:
    out: dict[str, list[tuple[dt.date, str | None]]] = {}
    for s in ordered_surfacings(timeline):
        out.setdefault(s.entry_id, []).append((s.on_date, timeline.reflections.get(s.id)))
    return out


def pin_history(timeline) -> dict[str, list[tuple[dt.date, bool]]]:
    """Per entry, the ordered ``(date, pinned)`` transitions."""
    out: dict[str, list[tuple[dt.date, bool]]] = {
        row.entry_id: [(timeline.start, row.initially_pinned)] for row in timeline.entries
    }
    for event in sorted(timeline.curation, key=lambda e: (e.on_date, e.entry_id)):
        if event.action in ("pin", "unpin"):
            out.setdefault(event.entry_id, []).append((event.on_date, event.action == "pin"))
    return out


def archive_dates(timeline) -> dict[str, dt.date]:
    return {
        event.entry_id: event.on_date for event in timeline.curation if event.action == "archive"
    }


def _state_at(history: Sequence[tuple[dt.date, bool]], on_date: dt.date) -> bool:
    value = False
    for when, flag in history:
        if when <= on_date:
            value = flag
    return value


def pinned_at(timeline, entry_id: str, on_date: dt.date) -> bool:
    return _state_at(pin_history(timeline).get(entry_id, []), on_date)


# --------------------------------------------------------------------------
# Percentiles and quartiles
# --------------------------------------------------------------------------


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile: the smallest value at or above the rank."""
    if not values:
        return math.nan
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def quartile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolation quantile (the 'inclusive' definition)."""
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


# --------------------------------------------------------------------------
# M1 — constraint compliance
# --------------------------------------------------------------------------


def constraint_violations(timelines: Sequence, params: dict) -> dict[str, list[str]]:
    """Every M1 predicate, re-derived from the timelines. Returns per-check detail."""
    templates = load_templates()
    theme_ids = load_theme_ids()
    found: dict[str, list[str]] = {key: [] for key in "abcdefghi"}

    for timeline in timelines:
        rows = ordered_surfacings(timeline)
        entries = timeline.by_id()
        archived = archive_dates(timeline)
        pins = pin_history(timeline)
        last_seen: dict[str, dt.date] = {}
        seen_dates: dict[str, list[dt.date]] = {}
        templates_seen: dict[str, list[str]] = {}
        by_date: dict[dt.date, set[str]] = {}
        newest_any: dt.date | None = None
        newest_daily: dt.date | None = None
        tag = timeline.scenario

        for s in rows:
            entry = entries[s.entry_id]
            themes = set(entry.themes)

            # (a) cooldown
            previous = last_seen.get(s.entry_id)
            if (
                previous is not None
                and (s.on_date - previous).days < params["W"]
                and not s.relaxed_cooldown
            ):
                found["a"].append(
                    f"{tag}: {s.entry_id} resurfaced after {(s.on_date - previous).days}d on {s.on_date}"
                )

            # (b) same-day duplicate
            if s.entry_id in by_date.setdefault(s.on_date, set()):
                found["b"].append(f"{tag}: {s.entry_id} surfaced twice on {s.on_date}")
            by_date[s.on_date].add(s.entry_id)

            # (c) archived entry surfaced / arrow of time
            archived_on = archived.get(s.entry_id)
            if archived_on is not None and s.on_date >= archived_on:
                found["c"].append(f"{tag}: archived {s.entry_id} surfaced on {s.on_date}")
            if newest_any is not None and s.on_date < newest_any:
                found["c"].append(f"{tag}: {s.on_date} precedes {newest_any}")
            newest_any = s.on_date if newest_any is None else max(newest_any, s.on_date)
            if s.kind.value == "daily":
                if newest_daily is not None and s.on_date < newest_daily:
                    found["c"].append(f"{tag}: daily set on {s.on_date} after {newest_daily}")
                newest_daily = s.on_date if newest_daily is None else max(newest_daily, s.on_date)

            # (d) prompt reuse inside the recency window
            window = params["prompt_reuse_window"]
            recent = templates_seen.setdefault(s.entry_id, [])[-window:]
            pool = [
                tid
                for tid, row in templates.items()
                if (row["theme_id"] in themes if themes else row["theme_id"] == GENERAL)
            ]
            if (
                s.prompt_template_id in recent
                and not s.prompt_recency_relaxed
                and any(tid not in recent for tid in pool)
            ):
                found["d"].append(
                    f"{tag}: {s.prompt_template_id} reused for {s.entry_id} on {s.on_date}"
                )
            templates_seen[s.entry_id].append(s.prompt_template_id)

            # (e) theme match
            template = templates.get(s.prompt_template_id)
            if template is None:
                found["e"].append(f"{tag}: unknown template {s.prompt_template_id}")
            else:
                theme_of = template["theme_id"]
                if themes and theme_of not in themes:
                    found["e"].append(f"{tag}: {theme_of} prompt for entry themed {sorted(themes)}")
                if not themes and theme_of != GENERAL:
                    found["e"].append(f"{tag}: {theme_of} prompt for an unthemed entry")
                if theme_of != GENERAL and theme_of not in theme_ids:
                    found["e"].append(f"{tag}: template references unknown theme {theme_of}")

            last_seen[s.entry_id] = s.on_date
            seen_dates.setdefault(s.entry_id, []).append(s.on_date)

        # (f) idempotence — recorded live by the driver, which alone can re-invoke
        for _ in range(timeline.idempotence_violations):
            found["f"].append(f"{tag}: re-materialization differed")

        # (g)/(h) forcing and rescue compliance, replayed slot by slot
        found["g"].extend(_forcing_violations(timeline, params))
        found["h"].extend(_rescue_violations(timeline, params, pins))

        # (i) draw accounting
        found["i"].extend(_draw_violations(timeline, params))

    return found


def _library_at(timeline, on_date: dt.date, archived: dict[str, dt.date]) -> list:
    return [
        row
        for row in timeline.entries
        if row.captured_on <= on_date
        and (archived.get(row.entry_id) is None or on_date < archived[row.entry_id])
    ]


def _forcing_violations(timeline, params: dict) -> list[str]:
    """M1g: an aged never-seen entry must claim the slot."""
    out: list[str] = []
    archived = archive_dates(timeline)
    exposures: dict[str, dt.date] = {}
    rows = ordered_surfacings(timeline)
    for s in rows:
        if s.kind.value == "daily":
            picked_today = {
                other.entry_id
                for other in rows
                if other.on_date == s.on_date
                and other.slot < s.slot
                and other.kind.value == "daily"
            }
            aged = [
                row
                for row in _library_at(timeline, s.on_date, archived)
                if row.entry_id not in exposures
                and row.entry_id not in picked_today
                and (s.on_date - row.captured_on).days > params["S"]
            ]
            if aged and s.select_pool.value not in ("forced_novelty", "pinned_rescue"):
                out.append(
                    f"{timeline.scenario}: {len(aged)} entries past the forcing horizon on "
                    f"{s.on_date} but the slot went to {s.select_pool.value}"
                )
        exposures[s.entry_id] = s.on_date
    return out


def _rescue_violations(timeline, params: dict, pins: dict) -> list[str]:
    """M1h: an eligible overdue pinned entry must claim the slot."""
    out: list[str] = []
    archived = archive_dates(timeline)
    exposures: dict[str, dt.date] = {}
    rows = ordered_surfacings(timeline)
    for s in rows:
        if s.kind.value == "daily":
            picked_today = {
                other.entry_id
                for other in rows
                if other.on_date == s.on_date
                and other.slot < s.slot
                and other.kind.value == "daily"
            }
            overdue = [
                row
                for row in _library_at(timeline, s.on_date, archived)
                if row.entry_id in exposures
                and row.entry_id not in picked_today
                and _state_at(pins.get(row.entry_id, []), s.on_date)
                and (s.on_date - exposures[row.entry_id]).days >= params["P_rescue"]
            ]
            if overdue and s.select_pool.value != "pinned_rescue":
                out.append(
                    f"{timeline.scenario}: {len(overdue)} pinned entries past P_rescue on "
                    f"{s.on_date} but the slot went to {s.select_pool.value}"
                )
        exposures[s.entry_id] = s.on_date
    return out


def _draw_violations(timeline, params: dict) -> list[str]:
    """M1i: draws are stamped ``extra``, honour their filter, and start a cooldown."""
    out: list[str] = []
    entries = timeline.by_id()
    archived = archive_dates(timeline)
    rows = ordered_surfacings(timeline)
    for index, s in enumerate(rows):
        is_extra = s.kind.value == "extra"
        if is_extra != (s.select_pool.value == "extra"):
            out.append(
                f"{timeline.scenario}: {s.id} kind={s.kind.value} pool={s.select_pool.value}"
            )
        if not is_extra:
            continue
        entry = entries[s.entry_id]
        if s.filter_theme_id and s.filter_theme_id not in entry.themes:
            out.append(
                f"{timeline.scenario}: draw filtered on {s.filter_theme_id} surfaced {s.entry_id}"
            )
        if s.filter_collection_id:
            members = timeline.collections.get(s.filter_collection_id, [])
            if s.entry_id not in members:
                out.append(f"{timeline.scenario}: draw surfaced a non-member {s.entry_id}")
        when = archived.get(s.entry_id)
        if when is not None and s.on_date >= when:
            out.append(f"{timeline.scenario}: draw surfaced archived {s.entry_id}")
        following = next((row for row in rows[index + 1 :] if row.entry_id == s.entry_id), None)
        if (
            following is not None
            and (following.on_date - s.on_date).days < params["W"]
            and not following.relaxed_cooldown
        ):
            out.append(f"{timeline.scenario}: the draw on {s.on_date} did not start a cooldown")
    return out


def m1_constraint_compliance(timelines: Sequence, params: dict) -> MetricResult:
    found = constraint_violations(timelines, params)
    total = sum(len(v) for v in found.values())
    detail = " ".join(f"{key}={len(value)}" for key, value in sorted(found.items()))
    if total:
        first = next(v[0] for v in found.values() if v)
        detail += f"   first: {first}"
    return MetricResult("M1 constraint_compliance", total, "== 0", total == 0, detail)


# --------------------------------------------------------------------------
# M2 — debut behaviour
# --------------------------------------------------------------------------


def latencies(timeline, cohort: str) -> list[tuple[str, int]]:
    """``(entry_id, latency)`` for the stream or the day-0 cohort.

    A never-surfaced entry gets ``run_end - captured_on``: total starvation must
    score strictly worse than a late debut (EVALS.md M2a).
    """
    first: dict[str, dt.date] = {}
    for s in ordered_surfacings(timeline):
        first.setdefault(s.entry_id, s.on_date)
    out: list[tuple[str, int]] = []
    for row in timeline.entries:
        is_initial = row.captured_on <= timeline.start
        if cohort == "stream" and is_initial:
            continue
        if cohort == "day0" and not is_initial:
            continue
        debut = first.get(row.entry_id)
        latency = (
            (debut - row.captured_on).days
            if debut is not None
            else (timeline.end - row.captured_on).days
        )
        out.append((row.entry_id, latency))
    return out


def censored(timeline, rows: Sequence[tuple[str, int]]) -> list[tuple[str, int]]:
    cutoff = timeline.end - dt.timedelta(days=CENSORING_GUARD_DAYS)
    by_id = timeline.by_id()
    return [row for row in rows if by_id[row[0]].captured_on <= cutoff]


def m2a_stream_debut_latency(timelines: Sequence) -> tuple[float, float, int]:
    values: list[int] = []
    for timeline in timelines:
        values.extend(v for _, v in censored(timeline, latencies(timeline, "stream")))
    return percentile(values, 0.50), percentile(values, 0.95), len(values)


def m2b_backlog_drain(timelines: Sequence) -> tuple[float, dict[str, str]]:
    detail: dict[str, str] = {}
    hits = total = 0
    for timeline in timelines:
        rows = latencies(timeline, "day0")
        if not rows:
            continue
        bound = L_BOUND[timeline.scenario]
        inside = sum(1 for _, value in rows if value <= bound)
        hits += inside
        total += len(rows)
        detail[timeline.scenario] = f"{inside}/{len(rows)} within {bound}d"
    return (hits / total if total else 0.0), detail


def m2_hard_bound(timelines: Sequence) -> tuple[int, list[str]]:
    offenders: list[str] = []
    for timeline in timelines:
        rows = censored(timeline, latencies(timeline, "stream") + latencies(timeline, "day0"))
        offenders.extend(
            f"{timeline.scenario}:{entry_id}={value}d"
            for entry_id, value in rows
            if value > HARD_LATENCY_BOUND
        )
    return len(offenders), offenders


# --------------------------------------------------------------------------
# M3 — mix balance
# --------------------------------------------------------------------------


def contested_slots(timeline) -> list[str]:
    daily = [s for s in timeline.surfacings if s.kind.value == "daily"]
    daily.sort(key=lambda s: (s.on_date, s.slot))
    return [s.select_pool.value for s in daily if s.select_pool.value in CONTESTED]


def sigma_windows(timeline, window: int) -> list[float]:
    slots = contested_slots(timeline)[SIGMA_WARMUP:]
    return [
        sum(1 for pool in slots[i : i + window] if pool == "novelty") / window
        for i in range(len(slots) - window + 1)
    ]


def m3_mix_balance(timeline, params: dict) -> tuple[float, int]:
    windows = sigma_windows(timeline, params["H"])
    if not windows:
        return math.inf, 0
    return max(abs(value - params["rho"]) for value in windows), len(windows)


# --------------------------------------------------------------------------
# M4 — spacing fidelity
# --------------------------------------------------------------------------


def review_sample(timeline, params: dict) -> list[tuple[object, float]]:
    """``(surfacing, O)`` for every daily review surfacing in steady state."""
    since = timeline.start + dt.timedelta(days=STEADY_STATE_DAY)
    events = events_by_entry(timeline)
    pins = pin_history(timeline)
    by_key = {(s.entry_id, s.on_date): s for s in ordered_surfacings(timeline)}
    out: list[tuple[object, float]] = []
    for entry_id, entry_events in events.items():
        for on_date, before in fold_steps(entry_events, params):
            surfacing = by_key[(entry_id, on_date)]
            if surfacing.kind.value != "daily" or on_date < since:
                continue
            if before.exposure_count == 0 or before.last_surfaced_on is None:
                continue
            pinned = _state_at(pins.get(entry_id, []), on_date)
            interval = effective_interval(before, pinned, params)
            out.append((surfacing, (on_date - before.last_surfaced_on).days / interval))
    return out


def m4a_early_violations(timelines: Sequence, params: dict) -> tuple[int, list[str]]:
    offenders: list[str] = []
    for timeline in timelines:
        for surfacing, ratio in review_sample(timeline, params):
            if ratio < 1.0 and surfacing.select_pool.value not in ("not_due", "relaxed"):
                offenders.append(
                    f"{timeline.scenario}:{surfacing.entry_id} O={ratio:.2f} "
                    f"pool={surfacing.select_pool.value} on {surfacing.on_date}"
                )
    return len(offenders), offenders


def m4b_proportional_fidelity(timelines: Sequence, params: dict) -> tuple[float, int, float]:
    excluded = {"pinned_rescue", "not_due", "relaxed"}
    ratios = [
        ratio
        for timeline in timelines
        for surfacing, ratio in review_sample(timeline, params)
        if surfacing.select_pool.value not in excluded
    ]
    if not ratios:
        return math.inf, 0, math.nan
    q1 = quartile(ratios, 0.25)
    q3 = quartile(ratios, 0.75)
    median = quartile(ratios, 0.50)
    return (q3 / q1 if q1 else math.inf), len(ratios), median


def spearman(pairs: Sequence[tuple[float, float]]) -> float:
    """Rank correlation, report-only (M4's diagnostic)."""
    if len(pairs) < 3:
        return math.nan

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        index = 0
        while index < len(order):
            stop = index
            while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
                stop += 1
            average = (index + stop) / 2 + 1
            for position in range(index, stop + 1):
                out[order[position]] = average
            index = stop + 1
        return out

    xs, ys = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    n = len(pairs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator = math.sqrt(sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys))
    return numerator / denominator if denominator else math.nan


# --------------------------------------------------------------------------
# M5 — pinned recurrence
# --------------------------------------------------------------------------


def pinned_intervals(timeline) -> list[tuple[str, dt.date, dt.date]]:
    """Maximal periods during which an entry was both pinned and active.

    Clipped to ``[day 60, run end]`` (EVALS.md M5) and to the entry's archive
    date; intervals shorter than 60 days are dropped by the caller's filter.
    """
    archived = archive_dates(timeline)
    window_start = timeline.start + dt.timedelta(days=STEADY_STATE_DAY)
    out: list[tuple[str, dt.date, dt.date]] = []
    for entry_id, history in pin_history(timeline).items():
        active_end = timeline.end
        if entry_id in archived:
            active_end = min(active_end, archived[entry_id] - dt.timedelta(days=1))
        opened: dt.date | None = None
        closing = timeline.end + dt.timedelta(days=1)
        for when, pinned in [*history, (closing, False)]:
            if pinned and opened is None:
                opened = when
            elif not pinned and opened is not None:
                out.append((entry_id, opened, min(when - dt.timedelta(days=1), active_end)))
                opened = None
    clipped = [(entry_id, max(a, window_start), b) for entry_id, a, b in out]
    return [row for row in clipped if (row[2] - row[1]).days >= MIN_PINNED_INTERVAL_DAYS]


def m5_pinned_recurrence(timelines: Sequence) -> tuple[float, int, list[str]]:
    good = total = 0
    offenders: list[str] = []
    for timeline in timelines:
        exposures: dict[str, list[dt.date]] = {}
        for s in ordered_surfacings(timeline):
            exposures.setdefault(s.entry_id, []).append(s.on_date)
        for entry_id, a, b in pinned_intervals(timeline):
            dates = exposures.get(entry_id, [])
            before = [d for d in dates if d <= a]
            anchor = before[-1] if before else a
            inside = [d for d in dates if anchor < d <= b]
            gaps = []
            cursor = anchor
            for when in inside:
                gaps.append((when - cursor).days)
                cursor = when
            total += 1
            worst = max(gaps, default=0)
            if worst <= PINNED_GAP_DAYS:
                good += 1
            else:
                offenders.append(f"{timeline.scenario}:{entry_id} worst gap {worst}d in [{a}, {b}]")
    return (good / total if total else 0.0), total, offenders


# --------------------------------------------------------------------------
# M6 — feedback responsiveness (planted truth)
# --------------------------------------------------------------------------


def m6_feedback_responsiveness(timelines: Sequence) -> tuple[float, dict[str, object]]:
    rates: dict[str, list[float]] = {"gem": [], "dud": []}
    for timeline in timelines:
        capture_cut = timeline.start + dt.timedelta(days=COHORT_CAPTURE_DAY)
        exposure_cut = timeline.start + dt.timedelta(days=COHORT_EXPOSURE_DAY)
        exposures: dict[str, list[dt.date]] = {}
        for s in ordered_surfacings(timeline):
            exposures.setdefault(s.entry_id, []).append(s.on_date)
        for row in timeline.entries:
            if row.quality not in rates or row.captured_on > capture_cut:
                continue
            dates = exposures.get(row.entry_id, [])
            if sum(1 for d in dates if d <= exposure_cut) < 2:
                continue
            rates[row.quality].append(sum(1 for d in dates if d > exposure_cut))
    sizes = {q: len(v) for q, v in rates.items()}
    means = {q: (sum(v) / len(v) if v else 0.0) for q, v in rates.items()}
    detail = {"cohort_sizes": sizes, "rates": means}
    if sizes["gem"] < 10 or sizes["dud"] < 10 or means["gem"] == 0:
        return math.inf, detail
    return means["dud"] / means["gem"], detail


def m6_archive_subcheck(timelines: Sequence, params: dict) -> tuple[float, list[str]]:
    """Every active entry with ``flat_streak >= archive_flat_streak`` is listed, and only those."""
    correct = total = 0
    offenders: list[str] = []
    for timeline in timelines:
        archived = archive_dates(timeline)
        listed = set(timeline.final_archive_candidates)
        for entry_id, streak in timeline.final_flat_streaks.items():
            if entry_id in archived:
                continue
            total += 1
            expected = streak >= params["archive_flat_streak"]
            if expected == (entry_id in listed):
                correct += 1
            else:
                offenders.append(
                    f"{timeline.scenario}:{entry_id} streak={streak} listed={entry_id in listed}"
                )
    return (correct / total if total else 1.0), offenders


# --------------------------------------------------------------------------
# M7 — determinism and replay
# --------------------------------------------------------------------------


def m7_replay_equality(timeline, params: dict) -> list[str]:
    """M7c: the maintained cache equals the independently recomputed fold."""
    offenders: list[str] = []
    events = events_by_entry(timeline)
    for entry_id, state in timeline.final_states.items():
        recomputed = fold_entry(events.get(entry_id, []), params)
        actual = (
            state.exposure_count,
            state.last_surfaced_on,
            state.interval_days,
            state.flat_streak,
        )
        expected = (
            recomputed.exposure_count,
            recomputed.last_surfaced_on,
            recomputed.interval_days,
            recomputed.flat_streak,
        )
        if actual != expected:
            offenders.append(f"{timeline.scenario}:{entry_id} cache={actual} fold={expected}")
    return offenders


def m7_checkpoint_replay(timeline, params: dict, checkpoints: int = 12) -> list[str]:
    """M7c at monthly checkpoints: the fold prefix must match a prefix re-fold."""
    offenders: list[str] = []
    events = events_by_entry(timeline)
    for month in range(1, checkpoints + 1):
        cut = timeline.start + dt.timedelta(days=month * 30)
        if cut > timeline.end:
            break
        for entry_id, entry_events in events.items():
            prefix = [event for event in entry_events if event[0] <= cut]
            whole = fold_entry(entry_events[: len(prefix)], params)
            recomputed = fold_entry(prefix, params)
            if whole != recomputed:  # pragma: no cover - defensive
                offenders.append(f"{timeline.scenario}:{entry_id}@{cut}")
    return offenders


# --------------------------------------------------------------------------
# M8 — theme suggestion accuracy (held-out labels)
# --------------------------------------------------------------------------


def load_labeled() -> list[dict]:
    path = Path(__file__).resolve().parent / "fixtures" / "themes_labeled.json"
    return json.loads(path.read_text(encoding="utf-8"))


def m8_theme_accuracy(
    rows: Iterable[dict], suggest: Callable[[str], list[str]]
) -> dict[str, object]:
    """``suggest`` returns the ordered theme ids the FR-3 suggester proposes."""
    rows = list(rows)
    top1 = hit3 = 0
    per_theme: dict[str, list[int]] = {}
    confusion: dict[tuple[str, str], int] = {}
    for row in rows:
        proposed = suggest(row["text"])
        label = row["theme"]
        correct = bool(proposed) and proposed[0] == label
        in_top3 = label in proposed[:3]
        top1 += correct
        hit3 += in_top3
        bucket = per_theme.setdefault(label, [0, 0])
        bucket[0] += in_top3
        bucket[1] += 1
        predicted = proposed[0] if proposed else "(none)"
        confusion[(label, predicted)] = confusion.get((label, predicted), 0) + 1
    total = len(rows)
    return {
        "n": total,
        "top1": top1 / total if total else 0.0,
        "hit3": hit3 / total if total else 0.0,
        "worst_theme_hit3": min((v[0] / v[1] for v in per_theme.values()), default=0.0),
        "per_theme": {k: v[0] / v[1] for k, v in sorted(per_theme.items())},
        "confusion": confusion,
    }


# --------------------------------------------------------------------------
# M9 — personalizer tamper resistance (truth by construction)
# --------------------------------------------------------------------------


def m9_validator(
    cases: Sequence[dict],
    clean: Sequence[dict],
    mutate: Callable[[dict], str],
    validate: Callable[[str, str, str | None], tuple[bool, str | None]],
) -> dict[str, object]:
    mutations = [case for case in cases if case["mutation"] != "off_spec"]
    off_spec = [case for case in cases if case["mutation"] == "off_spec"]
    rejected = 0
    wrong_check: list[str] = []
    escaped: list[str] = []
    for case in mutations:
        ok, check = validate(mutate(case), case["prompt_kind"], case["excerpt"])
        if ok:
            escaped.append(case["id"])
            continue
        rejected += 1
        if check != case["expected_check"]:
            wrong_check.append(f"{case['id']}: check {check} != {case['expected_check']}")
    false_positives = [
        row["id"]
        for row in clean
        if not validate(row["text"], row["prompt_kind"], row["excerpt"])[0]
    ]
    accepted_off_spec = sum(
        1 for case in off_spec if validate(mutate(case), case["prompt_kind"], case["excerpt"])[0]
    )
    return {
        "recall": rejected / len(mutations) if mutations else 0.0,
        "fpr": len(false_positives) / len(clean) if clean else 0.0,
        "escaped": escaped,
        "wrong_check": wrong_check,
        "false_positives": false_positives,
        "off_spec_accepted": accepted_off_spec,
        "off_spec_total": len(off_spec),
    }


# --------------------------------------------------------------------------
# Report-only diagnostics
# --------------------------------------------------------------------------


def pool_histogram(timeline) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in timeline.surfacings:
        if s.kind.value == "daily":
            out[s.select_pool.value] = out.get(s.select_pool.value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def kind_coverage(timeline) -> float:
    """Fraction of well-exposed entries that saw at least 3 distinct prompt kinds."""
    kinds: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for s in ordered_surfacings(timeline):
        kinds.setdefault(s.entry_id, set()).add(s.prompt_kind.value)
        counts[s.entry_id] = counts.get(s.entry_id, 0) + 1
    eligible = [eid for eid, count in counts.items() if count >= 4]
    if not eligible:
        return math.nan
    return sum(1 for eid in eligible if len(kinds[eid]) >= 3) / len(eligible)


def novelty_sparkline(timeline, params: dict, span: float = 0.15) -> str:
    """Sigma per window, scaled to ``rho +/- span`` so the regulation is visible."""
    windows = sigma_windows(timeline, params["H"])
    if not windows:
        return "(no windows)"
    glyphs = " .:-=+*#%@"
    low = params["rho"] - span
    step = max(1, len(windows) // 60)
    out = []
    for value in windows[::step][:60]:
        position = (value - low) / (2 * span)
        out.append(glyphs[max(0, min(len(glyphs) - 1, int(position * len(glyphs))))])
    return "".join(out)


def seed_sensitivity(left, right) -> float:
    """Fraction of dates on which two seeds disagree about what to show."""

    def by_date(timeline):
        out: dict[dt.date, tuple] = {}
        for s in timeline.surfacings:
            if s.kind.value == "daily":
                out[s.on_date] = (s.entry_id, s.prompt_template_id)
        return out

    a, b = by_date(left), by_date(right)
    dates = set(a) | set(b)
    if not dates:
        return 0.0
    return sum(1 for date in dates if a.get(date) != b.get(date)) / len(dates)
