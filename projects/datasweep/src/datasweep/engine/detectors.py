"""The eight issue-detector families (SCOPE.md FR-6).

Each detector is a pure function ``detect_X(table, profile, policy) ->
list[Proposal]``.  Detectors decide *what* is wrong and what the repaired value
would be; :mod:`datasweep.engine.planner` decides the confidence, the tier and
the ordering.  Row indices in the returned proposals are indices into the table
handed in; the pipeline maps them back to original coordinates.

Two framing rules from FR-6 that the code enforces literally:

* an already-empty cell is **not** an issue — ``MISS`` detects non-canonical
  *representations* of missingness only;
* ``STR`` is not a detector family — structural repairs live in
  :mod:`datasweep.engine.table`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .inference import (
    DATE_SPEC_BY_ID,
    DOT,
    EXCEL_SERIAL_MAX,
    EXCEL_SERIAL_MIN,
    analyze_dates,
    analyze_numbers,
    cell_types,
    excel_serial_to_iso,
    has_date_evidence,
    is_ascii_digits,
    is_convention_bearing,
    normalize_nfc,
    number_readings,
    parse_datetime_cell,
    parse_numeric,
    sentinel_kind,
    split_affixes,
    strip_zero_width,
)
from .models import (
    STRUCTURED_TYPES,
    AuditKind,
    ColumnType,
    Policy,
    Proposal,
    RawTable,
    TableProfile,
)
from .stats import summarize
from .table import row_signature

# --------------------------------------------------------------------------
# ENC — mojibake (FR-4 / D6)
# --------------------------------------------------------------------------

#: Characters that show up when UTF-8 bytes were decoded as cp1252/latin-1.
MOJIBAKE_INDICATORS: tuple[str, ...] = ("Ã", "Â", "â", "å", "Å")


def mojibake_indicator_count(text: str) -> int:
    return sum(text.count(marker) for marker in MOJIBAKE_INDICATORS)


def repair_mojibake(text: str) -> str | None:
    """ftfy's round-trip repair, or ``None`` when it does not apply.

    The repair is the round trip itself: re-encode as cp1252 and decode as
    UTF-8, both strict.  It is accepted only when it *strictly reduces* the
    mojibake indicator count, which is what keeps legitimate accented text
    (``Åsa``, ``château``) untouched — those fail the strict UTF-8 decode.
    """
    if mojibake_indicator_count(text) == 0:
        return None
    try:
        repaired = text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if mojibake_indicator_count(repaired) < mojibake_indicator_count(text):
        return repaired
    return None


def detect_enc(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    if not policy.rule_enabled("fix.mojibake"):
        return []
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        values = table.column(col)
        candidates = 0
        repairs: list[tuple[int, str, str]] = []
        for row, value in enumerate(values):
            if value is None or mojibake_indicator_count(value) == 0:
                continue
            candidates += 1
            repaired = repair_mojibake(value)
            if repaired is not None and repaired != value:
                repairs.append((row, value, repaired))
        if not repairs:
            continue
        share = len(repairs) / candidates
        for row, before, after in repairs:
            proposals.append(
                Proposal(
                    rule="fix.mojibake",
                    row=row,
                    col=col,
                    before=before,
                    after=after,
                    evidence={
                        "share": round(share, 6),
                        "candidates": candidates,
                        "repaired": len(repairs),
                        "round_trip": "cp1252→utf-8",
                    },
                )
            )
    return proposals


# --------------------------------------------------------------------------
# WS — whitespace and Unicode hygiene
# --------------------------------------------------------------------------

_INTERNAL_RUN = re.compile(r" {2,}")


@dataclass(frozen=True)
class WhitespaceResult:
    value: str
    kinds: list[str]
    rules: list[str]


def normalize_whitespace(text: str, policy: Policy) -> WhitespaceResult | None:
    """Apply every enabled WS sub-rule, reporting which ones fired.

    Order matters: zero-width strip → Unicode space separators → NFC → trim →
    collapse internal runs.  Converting NBSP first means an NBSP at the edge is
    then trimmed like any other space.
    """
    kinds: list[str] = []
    rules: list[str] = []
    value = text

    if policy.rule_enabled("fix.zero_width"):
        stripped = strip_zero_width(value)
        if stripped != value:
            value = stripped
            kinds.append("zero_width")
            rules.append("fix.zero_width")

    if policy.rule_enabled("fix.nbsp"):
        separators = {
            ord(c): " " for c in set(value) if c != " " and unicodedata.category(c) == "Zs"
        }
        if separators:
            value = value.translate(separators)
            kinds.append("nbsp")
            rules.append("fix.nbsp")

    if policy.rule_enabled("fix.nfc"):
        composed = normalize_nfc(value)
        if composed != value:
            value = composed
            kinds.append("nfc")
            rules.append("fix.nfc")

    if policy.rule_enabled("fix.trim"):
        trimmed = value.strip()
        if trimmed != value:
            if value[: len(value) - len(value.lstrip())]:
                kinds.append("leading")
            if value[len(value.rstrip()) :]:
                kinds.append("trailing")
            value = trimmed
            rules.append("fix.trim")

    if policy.rule_enabled("fix.collapse_spaces"):
        collapsed = _INTERNAL_RUN.sub(" ", value)
        if collapsed != value:
            value = collapsed
            kinds.append("internal_run")
            rules.append("fix.collapse_spaces")

    if not rules or value == text:
        return None
    return WhitespaceResult(value=value, kinds=kinds, rules=rules)


#: Which rule id names a combined whitespace repair, most user-visible first.
_WS_RULE_PRIORITY = (
    "fix.trim",
    "fix.nbsp",
    "fix.zero_width",
    "fix.nfc",
    "fix.collapse_spaces",
)


def detect_ws(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    """One proposal per changed cell — never two entries for the same cell."""
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        column_type = profile.type_of(col)
        for row, value in enumerate(table.column(col)):
            if value is None:
                continue
            result = normalize_whitespace(value, policy)
            if result is None:
                continue
            primary = next(rule for rule in _WS_RULE_PRIORITY if rule in result.rules)
            proposals.append(
                Proposal(
                    rule=primary,
                    row=row,
                    col=col,
                    before=value,
                    after=result.value,
                    evidence={
                        "kinds": result.kinds,
                        "rules": result.rules,
                        "column_type": column_type.value,
                    },
                )
            )
    return proposals


# --------------------------------------------------------------------------
# MISS — missing-value representations (D8)
# --------------------------------------------------------------------------


def is_round_sentinel(canonical: str) -> bool:
    """``-999``, ``-9999``, ``9999``, ``-99900`` … — documented missing codes."""
    if "." in canonical:
        integer, _, fraction = canonical.partition(".")
        if fraction.strip("0"):
            return False
    else:
        integer = canonical
    digits = integer.lstrip("-")
    if not is_ascii_digits(digits) or len(digits) < 2 or int(digits) < 99:
        return False
    return set(digits) == {"9"} or digits.endswith("00")


def detect_miss(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        column_type = profile.type_of(col)
        values = table.column(col)
        for row, value in enumerate(values):
            if value is None:
                continue  # FR-6: an already-empty cell is not an issue
            kind = sentinel_kind(value)
            if kind is None:
                continue
            hard = kind == "hard" and column_type is not ColumnType.TEXT
            rule = "fix.sentinel_null_hard" if hard else "fix.sentinel_null_soft"
            if not policy.rule_enabled(rule):
                continue
            proposals.append(
                Proposal(
                    rule=rule,
                    row=row,
                    col=col,
                    before=value,
                    after=None,
                    evidence={
                        "token": value.strip(),
                        "list": kind,
                        "column_type": column_type.value,
                    },
                )
            )
        proposals.extend(_numeric_sentinels(values, col, column_type, policy))
    return proposals


def _numeric_sentinels(
    values: list[str | None], col: int, column_type: ColumnType, policy: Policy
) -> list[Proposal]:
    """Report-only numeric missing codes (D8): round, repeated, and extreme."""
    if column_type not in {ColumnType.INTEGER, ColumnType.FLOAT}:
        return []
    if not policy.rule_enabled("detect.numeric_sentinel"):
        return []
    thresholds = policy.thresholds
    parsed: list[tuple[int, str, float]] = []
    for row, value in enumerate(values):
        if value is None:
            continue
        canonical = parse_numeric(value)
        if canonical is None:
            continue
        parsed.append((row, canonical, float(canonical)))
    if len(parsed) < thresholds.outlier_min_n:
        return []
    counts = Counter(canonical for _, canonical, _ in parsed)
    summary = summarize([number for _, _, number in parsed], thresholds.outlier_iqr_k)
    proposals: list[Proposal] = []
    for canonical, count in sorted(counts.items()):
        if count < 3 or not is_round_sentinel(canonical):
            continue
        number = float(canonical)
        if not summary.outside_fences(number):
            continue
        modified_z = summary.modified_z(number)
        if abs(modified_z) <= thresholds.outlier_mad_z:
            continue
        for row, cell_canonical, _ in parsed:
            if cell_canonical != canonical:
                continue
            proposals.append(
                Proposal(
                    rule="detect.numeric_sentinel",
                    row=row,
                    col=col,
                    before=values[row],
                    after=values[row],
                    evidence={
                        "value": canonical,
                        "occurrences": count,
                        "lower_fence": round(summary.lower_fence, 6),
                        "upper_fence": round(summary.upper_fence, 6),
                        "mad_z": round(modified_z, 6),
                        "note": "documented-missing-code pattern; never auto-nulled",
                    },
                )
            )
    return proposals


# --------------------------------------------------------------------------
# TYPE — number conventions, currency, apostrophes (D7)
# --------------------------------------------------------------------------


def detect_type(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        column_type = profile.type_of(col)
        if column_type not in STRUCTURED_TYPES:
            continue
        proposals.extend(_detect_type_column(table, col, column_type, policy))
    return proposals


def _detect_type_column(
    table: RawTable, col: int, column_type: ColumnType, policy: Policy
) -> list[Proposal]:
    values = table.column(col)
    analysis = analyze_numbers(values)
    thresholds = policy.thresholds
    numeric_column = column_type in {ColumnType.INTEGER, ColumnType.FLOAT}
    mixed_currency = len(analysis.currency_counts) >= 2
    proposals: list[Proposal] = []

    if (
        numeric_column
        and analysis.n_decisive >= thresholds.convention_min_decisive
        and analysis.agree < thresholds.convention_agree
        and policy.rule_enabled("detect.mixed_number_conventions")
    ):
        proposals.append(
            Proposal(
                rule="detect.mixed_number_conventions",
                col=col,
                evidence={
                    "scope": "column",
                    "decisive": analysis.decisive_counts,
                    "majority": analysis.majority,
                    "agree": round(analysis.agree, 6),
                },
            )
        )

    for row, value in enumerate(values):
        if value is None:
            continue
        affixes = split_affixes(value)
        readings = number_readings(affixes.core)
        rules: list[str] = []
        evidence: dict[str, object] = {}

        if affixes.apostrophe and affixes.core:
            rules.append("fix.strip_apostrophe")

        if mixed_currency and affixes.currency is not None and readings:
            if not policy.rule_enabled("fix.currency_mixed"):
                continue
            proposals.append(
                Proposal(
                    rule="fix.currency_mixed",
                    row=row,
                    col=col,
                    before=value,
                    after=readings.get(DOT, next(iter(readings.values()))),
                    group=f"{col}:currency_mixed",
                    evidence={
                        "scope": "column",
                        "symbols": analysis.currency_counts,
                        "symbol": affixes.currency,
                    },
                )
            )
            continue

        after: str | None = None
        if column_type is ColumnType.DIGITS:
            # D7.8 — leading zeros are identity, never numerically coerced.
            if "fix.strip_apostrophe" in rules:
                after = affixes.core
                evidence["digits_guard"] = True
        elif numeric_column and readings:
            if affixes.currency is not None:
                rules.append("fix.currency_strip")
                evidence["currency"] = affixes.currency
                evidence["currency_position"] = (
                    "leading" if affixes.currency_leading else "trailing"
                )
            if is_convention_bearing(affixes.core):
                if len(readings) == 1:
                    convention = next(iter(readings))
                    after = readings[convention]
                    if after != affixes.core:
                        rules.append("fix.number_canon")
                        evidence |= {"convention": convention, "decisive": True}
                else:
                    # The ambiguity is the finding: proposed even when the
                    # recommended reading happens to leave the text unchanged,
                    # because the user still has to choose (D7.5).
                    rules.append("fix.number_canon_ambiguous")
                    after = readings[analysis.majority]
                    evidence |= {
                        "scope": "column",
                        "readings": dict(sorted(readings.items())),
                        "majority": analysis.majority,
                        "agree": round(analysis.agree, 6),
                        "n_decisive": analysis.n_decisive,
                    }
            elif rules:
                after = readings.get(DOT, next(iter(readings.values())))
        elif "fix.strip_apostrophe" in rules:
            after = affixes.core

        rules = [rule for rule in rules if policy.rule_enabled(rule)]
        identity_ok = "fix.number_canon_ambiguous" in rules
        if after is None or not rules or (after == value and not identity_ok):
            continue
        primary = _primary_type_rule(rules)
        evidence["rules"] = rules
        proposals.append(
            Proposal(
                rule=primary,
                row=row,
                col=col,
                before=value,
                after=after,
                group=f"{col}:{primary}" if primary == "fix.number_canon_ambiguous" else None,
                evidence=evidence,
            )
        )
    return proposals


_TYPE_RULE_PRIORITY = (
    "fix.number_canon_ambiguous",
    "fix.number_canon",
    "fix.currency_strip",
    "fix.strip_apostrophe",
)


def _primary_type_rule(rules: list[str]) -> str:
    return next(rule for rule in _TYPE_RULE_PRIORITY if rule in rules)


def detect_coercion_failures(
    table: RawTable,
    profile: TableProfile,
    policy: Policy,
    *,
    covered: set[tuple[int, int]],
    row_map: list[int],
) -> list[Proposal]:
    """Cells that do not fit their column type and that nothing else explains.

    A cell with any planned fix (at any tier) is *accounted for* and is not a
    coercion failure; this is what keeps an Excel-serial cell in a date column
    from being charged twice.
    """
    if not policy.rule_enabled("detect.coercion_failure"):
        return []
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        column_type = profile.type_of(col)
        if column_type not in STRUCTURED_TYPES:
            continue
        for row, value in enumerate(table.column(col)):
            if value is None or sentinel_kind(value) is not None:
                continue
            if (row_map[row], col) in covered:
                continue
            if column_type in cell_types(value):
                continue
            proposals.append(
                Proposal(
                    rule="detect.coercion_failure",
                    row=row,
                    col=col,
                    before=value,
                    after=value,
                    evidence={
                        "expected_type": column_type.value,
                        "note": "left as-is; datasweep never invents a value",
                    },
                )
            )
    return proposals


# --------------------------------------------------------------------------
# DATE — mixed formats, ambiguity, serials, two-digit years (D9)
# --------------------------------------------------------------------------


def detect_date(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        column_type = profile.type_of(col)
        values = table.column(col)
        if column_type is ColumnType.DATETIME:
            proposals.extend(_detect_datetime_column(values, col, policy))
        elif column_type is ColumnType.DATE:
            proposals.extend(_detect_date_column(values, col, policy))
        elif column_type in {ColumnType.INTEGER, ColumnType.DIGITS} and has_date_evidence(
            values, table.header(col)
        ):
            proposals.extend(_detect_excel_serials(values, col, policy))
    return proposals


def _detect_datetime_column(values: list[str | None], col: int, policy: Policy) -> list[Proposal]:
    if not policy.rule_enabled("fix.date_canon"):
        return []
    proposals: list[Proposal] = []
    for row, value in enumerate(values):
        if value is None:
            continue
        canonical = parse_datetime_cell(value)
        if canonical is None or canonical == value:
            continue
        proposals.append(
            Proposal(
                rule="fix.date_canon",
                row=row,
                col=col,
                before=value,
                after=canonical,
                evidence={"format_from": "rfc3339-variant", "target": "rfc3339"},
            )
        )
    return proposals


def _detect_date_column(values: list[str | None], col: int, policy: Policy) -> list[Proposal]:
    analysis = analyze_dates(values)
    proposals: list[Proposal] = []

    if analysis.is_ambiguous and policy.rule_enabled("fix.date_canon_ambiguous"):
        # D9: exactly one column-level review item, zero cell-level auto fixes.
        for row in analysis.ambiguous_rows:
            readings = analysis.readings(row)
            candidate_specs = sorted(readings)
            preferred = next(
                (spec for spec in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y") if spec in readings),
                candidate_specs[0],
            )
            proposals.append(
                Proposal(
                    rule="fix.date_canon_ambiguous",
                    row=row,
                    col=col,
                    before=values[row],
                    after=readings[preferred].iso,
                    group=f"{col}:date_ambiguous",
                    evidence={
                        "scope": "column",
                        "candidates": candidate_specs,
                        "recommended": preferred,
                        "invariant_share": round(analysis.invariant_share, 6),
                        "conflict": analysis.conflict,
                    },
                )
            )

    for row, value in enumerate(values):
        # Only a column with *no* proof leaves its two-way cells to the review
        # item; on a proven column every cell is canonicalized at auto tier.
        if value is None or (analysis.is_ambiguous and row in analysis.ambiguous_rows):
            continue
        chosen = analysis.chosen(row)
        if chosen is None:
            continue
        spec = DATE_SPEC_BY_ID[chosen.spec]
        rule = "fix.two_digit_year" if spec.two_digit_year else "fix.date_canon"
        if not policy.rule_enabled(rule) or chosen.iso == value:
            continue
        evidence: dict[str, object] = {"format_from": chosen.spec, "target": "iso-8601"}
        if spec.ambiguous_family and analysis.proof_row is not None:
            evidence["proof"] = f"deciding component > 12 at row {analysis.proof_row}"
            evidence["proof_row"] = analysis.proof_row
            evidence["order"] = analysis.order
        if spec.two_digit_year:
            evidence["pivot"] = "69 → 1969-1999, 00-68 → 2000-2068"
        proposals.append(
            Proposal(
                rule=rule,
                row=row,
                col=col,
                before=value,
                after=chosen.iso,
                group=f"{col}:two_digit_year" if spec.two_digit_year else None,
                evidence=evidence,
            )
        )

    proposals.extend(_detect_excel_serials(values, col, policy, skip=set(analysis.candidates)))
    return proposals


def _detect_excel_serials(
    values: list[str | None], col: int, policy: Policy, skip: set[int] | None = None
) -> list[Proposal]:
    if not policy.rule_enabled("fix.excel_serial"):
        return []
    skip = skip or set()
    proposals: list[Proposal] = []
    for row, value in enumerate(values):
        if value is None or row in skip:
            continue
        text = value.strip()
        if not is_ascii_digits(text):
            continue
        serial = int(text)
        if not EXCEL_SERIAL_MIN <= serial <= EXCEL_SERIAL_MAX:
            continue
        proposals.append(
            Proposal(
                rule="fix.excel_serial",
                row=row,
                col=col,
                before=value,
                after=excel_serial_to_iso(serial),
                group=f"{col}:excel_serial",
                evidence={
                    "serial": serial,
                    "epoch": "1899-12-30 (Lotus 1-2-3 leap-year bug)",
                    "interpretation": "Excel 1900 date serial",
                },
            )
        )
    return proposals


# --------------------------------------------------------------------------
# CAT — label consolidation (D10)
# --------------------------------------------------------------------------


def fingerprint(label: str) -> str:
    """OpenRefine's strict fingerprint, token order preserved (D10a)."""
    text = normalize_nfc(label.strip()).casefold()
    text = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith(("P", "C"))
    )
    return " ".join(text.split())


def damerau_levenshtein(left: str, right: str) -> int:
    """Unrestricted Damerau-Levenshtein distance (Lowrance-Wagner)."""
    if left == right:
        return 0
    len_l, len_r = len(left), len(right)
    if not len_l or not len_r:
        return max(len_l, len_r)
    max_distance = len_l + len_r
    last_row: dict[str, int] = {}
    matrix = [[max_distance] * (len_r + 2) for _ in range(len_l + 2)]
    matrix[0][0] = max_distance
    for i in range(len_l + 1):
        matrix[i + 1][0] = max_distance
        matrix[i + 1][1] = i
    for j in range(len_r + 1):
        matrix[0][j + 1] = max_distance
        matrix[1][j + 1] = j
    for i in range(1, len_l + 1):
        last_match_col = 0
        for j in range(1, len_r + 1):
            i2 = last_row.get(right[j - 1], 0)
            j2 = last_match_col
            cost = 0 if left[i - 1] == right[j - 1] else 1
            if cost == 0:
                last_match_col = j
            matrix[i + 1][j + 1] = min(
                matrix[i][j] + cost,  # substitution
                matrix[i + 1][j] + 1,  # insertion
                matrix[i][j + 1] + 1,  # deletion
                matrix[i2][j2] + (i - i2 - 1) + 1 + (j - j2 - 1),  # transposition
            )
        last_row[left[i - 1]] = i
    return matrix[len_l + 1][len_r + 1]


def nn_threshold(left: str, right: str) -> int:
    """Distance ≤ 1, or ≤ 2 once a label is at least 8 characters long (D10b)."""
    return 2 if max(len(left), len(right)) >= 8 else 1


def detect_cat(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        if profile.type_of(col) is not ColumnType.CATEGORICAL:
            continue
        proposals.extend(_detect_cat_column(table.column(col), col, policy))
    return proposals


def _detect_cat_column(values: list[str | None], col: int, policy: Policy) -> list[Proposal]:
    counts: Counter[str] = Counter(v for v in values if v is not None)
    first_seen: dict[str, int] = {}
    for row, value in enumerate(values):
        if value is not None and value not in first_seen:
            first_seen[value] = row

    clusters: dict[str, list[str]] = {}
    for label in counts:
        clusters.setdefault(fingerprint(label), []).append(label)

    canonical_of: dict[str, str] = {}
    proposals: list[Proposal] = []
    for key in sorted(clusters):
        members = sorted(clusters[key], key=lambda label: (-counts[label], first_seen[label]))
        canonical = members[0]
        for label in members:
            canonical_of[label] = canonical
        if len(members) == 1:
            continue
        total = sum(counts[label] for label in members)
        dominance = counts[canonical] / total
        if not policy.rule_enabled("fix.label_merge_fingerprint"):
            continue
        for row, value in enumerate(values):
            if value is None or value == canonical or canonical_of.get(value) != canonical:
                continue
            proposals.append(
                Proposal(
                    rule="fix.label_merge_fingerprint",
                    row=row,
                    col=col,
                    before=value,
                    after=canonical,
                    group=f"{col}:{value}→{canonical}",
                    evidence={
                        "dominance": round(dominance, 6),
                        "cluster": sorted(members),
                        "canonical": canonical,
                        "fingerprint": key,
                    },
                )
            )

    proposals.extend(
        _detect_nearest_neighbours(values, col, counts, canonical_of, first_seen, policy)
    )
    return proposals


def _detect_nearest_neighbours(
    values: list[str | None],
    col: int,
    counts: Counter[str],
    canonical_of: dict[str, str],
    first_seen: dict[str, int],
    policy: Policy,
) -> list[Proposal]:
    """Damerau-Levenshtein neighbours, proposed only for rare minorities."""
    if not policy.rule_enabled("fix.label_merge_nn"):
        return []
    thresholds = policy.thresholds
    merged: Counter[str] = Counter()
    for label, count in counts.items():
        merged[canonical_of.get(label, label)] += count
    labels = sorted(merged, key=lambda label: (first_seen[label], label))

    proposals: list[Proposal] = []
    for index, minor in enumerate(labels):
        for major in labels[index + 1 :] + labels[:index]:
            if merged[major] < merged[minor] or (
                merged[major] == merged[minor] and first_seen[major] > first_seen[minor]
            ):
                continue
            distance = damerau_levenshtein(minor, major)
            if distance == 0 or distance > nn_threshold(minor, major):
                continue
            n_minor, n_major = merged[minor], merged[major]
            ratio = n_minor / (n_minor + n_major)
            if ratio > thresholds.nn_max_ratio or n_major < thresholds.nn_min_majority:
                continue
            for row, value in enumerate(values):
                if value is None or canonical_of.get(value, value) != minor:
                    continue
                proposals.append(
                    Proposal(
                        rule="fix.label_merge_nn",
                        row=row,
                        col=col,
                        before=value,
                        after=major,
                        group=f"{col}:{minor}→{major}",
                        evidence={
                            "distance": distance,
                            "ratio": round(ratio, 6),
                            "n_minor": n_minor,
                            "n_major": n_major,
                            "major": major,
                            "minor": minor,
                        },
                    )
                )
            break
    return proposals


# --------------------------------------------------------------------------
# DUP — exact duplicate rows
# --------------------------------------------------------------------------


def detect_dup(table: RawTable, profile: TableProfile, policy: Policy) -> list[Proposal]:
    """Exact full-row duplicates only; the first occurrence is kept (FR-6)."""
    if not policy.rule_enabled("fix.drop_duplicate_row"):
        return []
    seen: dict[tuple[str | None, ...], int] = {}
    proposals: list[Proposal] = []
    for row_index, row in enumerate(table.rows):
        signature = row_signature(row)
        first = seen.get(signature)
        if first is None:
            seen[signature] = row_index
            continue
        proposals.append(
            Proposal(
                rule="fix.drop_duplicate_row",
                kind=AuditKind.ROW_DROP,
                row=row_index,
                before_row=list(row),
                evidence={"first_occurrence_row": first},
            )
        )
    return proposals


# --------------------------------------------------------------------------
# OUT — robust outlier flagging, report-only (D11)
# --------------------------------------------------------------------------


def detect_out(
    table: RawTable,
    profile: TableProfile,
    policy: Policy,
    *,
    exclude: set[tuple[int, int]] | None = None,
    row_map: list[int] | None = None,
) -> list[Proposal]:
    """Tukey fences at k *and* modified z > 3.5 — a value must trip both."""
    if not policy.rule_enabled("detect.outlier"):
        return []
    thresholds = policy.thresholds
    exclude = exclude or set()
    row_map = row_map or list(range(table.n_rows))
    proposals: list[Proposal] = []
    for col in range(table.n_cols):
        if profile.type_of(col) not in {ColumnType.INTEGER, ColumnType.FLOAT}:
            continue
        parsed: list[tuple[int, float]] = []
        for row, value in enumerate(table.column(col)):
            if value is None:
                continue
            canonical = parse_numeric(value)
            if canonical is not None:
                parsed.append((row, float(canonical)))
        if len(parsed) < thresholds.outlier_min_n:
            continue
        summary = summarize([number for _, number in parsed], thresholds.outlier_iqr_k)
        for row, number in parsed:
            if (row_map[row], col) in exclude:
                continue
            if not summary.outside_fences(number):
                continue
            modified_z = summary.modified_z(number)
            if abs(modified_z) <= thresholds.outlier_mad_z:
                continue
            proposals.append(
                Proposal(
                    rule="detect.outlier",
                    row=row,
                    col=col,
                    before=table.cell(row, col),
                    after=table.cell(row, col),
                    evidence={
                        "q1": round(summary.q1, 6),
                        "q3": round(summary.q3, 6),
                        "iqr": round(summary.iqr, 6),
                        "lower_fence": round(summary.lower_fence, 6),
                        "upper_fence": round(summary.upper_fence, 6),
                        "mad": round(summary.mad, 6),
                        "mad_z": round(modified_z, 6),
                        "note": "reported only; never winsorized, clamped or dropped",
                    },
                )
            )
    return proposals
