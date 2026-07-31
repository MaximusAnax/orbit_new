"""Per-cell micro-parsers, column type voting, number conventions, dates.

SCOPE.md FR-5 (type inference), D5 (ptype-lite voting), D7 (number
canonicalization), D9 (date handling).

Everything is a pure function of cell *values*.  Header names are never an
input to type inference — asserted by
``tests/test_inference.py::test_fr5_headers_do_not_influence_type``.

No ``strptime`` anywhere: ``%b``/``%B`` are locale-dependent, and FR-16 demands
byte-identical output across platforms, so month names are parsed from fixed
English tables.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from functools import lru_cache

from .models import (
    STRUCTURED_TYPES,
    TYPE_PRIORITY,
    ColumnProfile,
    ColumnType,
    Policy,
    RawTable,
    TableProfile,
    Thresholds,
)
from .stats import summarize

# --------------------------------------------------------------------------
# Missing-value vocabulary (SCOPE.md D8)
# --------------------------------------------------------------------------

#: Unambiguous missing markers.  Auto-nullable in non-``text`` columns.
HARD_SENTINELS: frozenset[str] = frozenset({"", "na", "n/a", "null", "nan", "#n/a", "#value!"})
#: Plausible-but-real values ("None" is a real category in a medication
#: column).  Review tier, always.
SOFT_SENTINELS: frozenset[str] = frozenset({"-", ".", "?", "none", "missing", "unknown"})


def is_null(cell: str | None) -> bool:
    """Canonical null (D8): ``None`` in :class:`RawTable`."""
    return cell is None


def sentinel_kind(cell: str | None) -> str | None:
    """``"hard"``, ``"soft"`` or ``None`` — case-insensitive after trim."""
    if cell is None:
        return None
    token = cell.strip().casefold()
    if token in HARD_SENTINELS:
        return "hard"
    if token in SOFT_SENTINELS:
        return "soft"
    return None


# --------------------------------------------------------------------------
# Micro-parsers
# --------------------------------------------------------------------------

#: SCOPE.md FR-5: exactly these tokens, case-insensitive after trim.
#: ``0`` and ``1`` are deliberately *not* boolean, so numeric flag columns
#: type as ``integer``.
BOOL_TOKENS: frozenset[str] = frozenset({"true", "false", "yes", "no", "y", "n", "t", "f"})

#: D7.1 — the fixed currency affix set.  Longest first so ``USD`` wins over
#: a bare ``$`` prefix check.
CURRENCY_AFFIXES: tuple[str, ...] = ("USD", "EUR", "GBP", "CHF", "$", "€", "£", "¥")

#: Zero-width characters stripped by the WS detector (FR-6): ZWSP, ZWNJ, ZWJ,
#: and the zero-width no-break space (a BOM used mid-string).
ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"


def is_ascii_digits(text: str) -> bool:
    """True for a non-empty run of ASCII 0-9 (``str.isdigit`` accepts ² and ٣)."""
    return bool(text) and text.isascii() and text.isdigit()


def parse_bool(cell: str) -> bool | None:
    token = cell.strip().casefold()
    if token in BOOL_TOKENS:
        return token in {"true", "yes", "y", "t"}
    return None


@dataclass(frozen=True)
class Affixes:
    """A cell split into its numeric core and the affixes around it (D7.1)."""

    core: str
    apostrophe: bool
    currency: str | None
    currency_leading: bool


@lru_cache(maxsize=1 << 16)
def split_affixes(cell: str) -> Affixes:
    """Strip surrounding whitespace, one leading ``'``, one currency affix."""
    text = cell.strip()
    apostrophe = False
    if text.startswith("'") and len(text) > 1:
        text = text[1:].strip()
        apostrophe = True
    currency: str | None = None
    leading = False
    for symbol in CURRENCY_AFFIXES:
        if text.startswith(symbol) and len(text) > len(symbol):
            currency, text, leading = symbol, text[len(symbol) :].strip(), True
            break
    else:
        for symbol in CURRENCY_AFFIXES:
            if text.endswith(symbol) and len(text) > len(symbol):
                currency, text, leading = symbol, text[: -len(symbol)].strip(), False
                break
    return Affixes(core=text, apostrophe=apostrophe, currency=currency, currency_leading=leading)


DOT = "DOT"
COMMA = "COMMA"
CONVENTIONS: tuple[str, str] = (DOT, COMMA)


def parse_number(core: str, convention: str) -> str | None:
    """Parse ``core`` under one convention, returning canonical ``-?d[.d]``.

    ``DOT``: grouping ``,`` decimal ``.``   ``COMMA``: grouping ``.`` decimal ``,``.
    Every grouping run must be exactly three digits (the first 1-3) and at most
    one decimal separator may appear (D7.2).
    """
    grouping, decimal = (",", ".") if convention == DOT else (".", ",")
    text = core
    sign = ""
    if text[:1] in {"+", "-"}:
        sign = "-" if text[0] == "-" else ""
        text = text[1:]
    if not text or text.count(decimal) > 1:
        return None
    if decimal in text:
        int_part, _, frac = text.partition(decimal)
        if not is_ascii_digits(frac):
            return None
    else:
        int_part, frac = text, ""
    groups = int_part.split(grouping)
    if not all(is_ascii_digits(g) for g in groups):
        return None
    if len(groups) > 1:
        if not 1 <= len(groups[0]) <= 3:
            return None
        if any(len(g) != 3 for g in groups[1:]):
            return None
    digits = "".join(groups)
    return f"{sign}{digits}.{frac}" if frac else f"{sign}{digits}"


@lru_cache(maxsize=1 << 16)
def _readings(core: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (convention, parsed)
        for convention in CONVENTIONS
        if (parsed := parse_number(core, convention)) is not None
    )


def number_readings(core: str) -> dict[str, str]:
    """Canonical value under each convention that parses ``core`` at all."""
    return dict(_readings(core))


def is_convention_bearing(core: str) -> bool:
    """D7.2 — the remainder carries a ``,`` or a ``.``."""
    return "," in core or "." in core


@lru_cache(maxsize=1 << 16)
def parse_numeric(cell: str) -> str | None:
    """Canonical numeric value of a cell under *some* reading, else ``None``.

    Used only by the type vote, which asks "does this cell mean a number?" —
    the *which* reading question belongs to D7 and lives in
    :func:`analyze_numbers`.
    """
    core = split_affixes(cell).core
    readings = number_readings(core)
    if not readings:
        return None
    return readings[DOT] if DOT in readings else readings[COMMA]


@lru_cache(maxsize=1 << 16)
def digits_core(cell: str) -> str | None:
    """The digit run of an identifier-like cell (``'0123`` → ``0123``)."""
    affixes = split_affixes(cell)
    if affixes.currency is not None:
        return None
    return affixes.core if is_ascii_digits(affixes.core) else None


# --------------------------------------------------------------------------
# Dates (SCOPE.md D9)
# --------------------------------------------------------------------------

_MONTH_ABBR = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_MONTH_FULL = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}


@dataclass(frozen=True)
class DateSpec:
    """One candidate format (D9's exhaustive list, in priority order)."""

    spec: str
    pattern: re.Pattern[str]
    order: str  # "ymd" | "dmy" | "mdy" | "mon"
    two_digit_year: bool = False
    partner: str | None = None  # the format it is ambiguous with

    @property
    def ambiguous_family(self) -> bool:
        return self.partner is not None


def _num(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


DATE_SPECS: tuple[DateSpec, ...] = (
    DateSpec("%Y-%m-%d", _num(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
    DateSpec("%Y/%m/%d", _num(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), "ymd"),
    DateSpec("%d/%m/%Y", _num(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "dmy", partner="%m/%d/%Y"),
    DateSpec("%m/%d/%Y", _num(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "mdy", partner="%d/%m/%Y"),
    DateSpec("%d-%m-%Y", _num(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "dmy", partner="%m-%d-%Y"),
    DateSpec("%m-%d-%Y", _num(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "mdy", partner="%d-%m-%Y"),
    DateSpec("%d.%m.%Y", _num(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "dmy"),
    DateSpec("%b %d, %Y", _num(r"^([A-Za-z]{3})\.? (\d{1,2}), ?(\d{4})$"), "mon"),
    DateSpec("%d %b %Y", _num(r"^(\d{1,2}) ([A-Za-z]{3})\.? (\d{4})$"), "mon"),
    DateSpec("%B %d, %Y", _num(r"^([A-Za-z]{4,9}) (\d{1,2}), ?(\d{4})$"), "mon"),
    DateSpec(
        "%d/%m/%y",
        _num(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"),
        "dmy",
        two_digit_year=True,
        partner="%m/%d/%y",
    ),
    DateSpec(
        "%m/%d/%y",
        _num(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"),
        "mdy",
        two_digit_year=True,
        partner="%d/%m/%y",
    ),
)

DATE_SPEC_BY_ID: dict[str, DateSpec] = {s.spec: s for s in DATE_SPECS}

#: RFC 3339 / ISO 8601 datetime, with optional fraction and offset.
_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})[Tt ](\d{1,2}):(\d{2})(?::(\d{2}))?(\.\d+)?"
    r"([Zz]|[+-]\d{2}:?\d{2})?$"
)

#: Excel's 1900 epoch is really 1899-12-30 (Lotus 1-2-3's fictitious
#: 1900-02-29 shifted everything by one day).
EXCEL_EPOCH = date_cls(1899, 12, 30)
EXCEL_SERIAL_MIN = 20000
EXCEL_SERIAL_MAX = 60000

#: Two-digit-year pivot, matching the POSIX/`%y` convention.
_YEAR_PIVOT = 69


def _two_digit_year(value: int) -> int:
    return 1900 + value if value >= _YEAR_PIVOT else 2000 + value


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date_cls(year, month, day).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class DateCandidate:
    spec: str
    iso: str


def date_candidates(cell: str) -> list[DateCandidate]:
    """All date-only formats that parse ``cell``, in D9 priority order."""
    return list(_date_candidates(cell))


@lru_cache(maxsize=1 << 16)
def _date_candidates(cell: str) -> tuple[DateCandidate, ...]:
    text = cell.strip()
    if not text:
        return ()
    out: list[DateCandidate] = []
    for spec in DATE_SPECS:
        match = spec.pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        if spec.order == "ymd":
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        elif spec.order == "dmy":
            day, month = int(groups[0]), int(groups[1])
            year = int(groups[2])
            if spec.two_digit_year:
                year = _two_digit_year(year)
        elif spec.order == "mdy":
            month, day = int(groups[0]), int(groups[1])
            year = int(groups[2])
            if spec.two_digit_year:
                year = _two_digit_year(year)
        else:  # month name
            if spec.spec == "%d %b %Y":
                day, name, year = int(groups[0]), groups[1], int(groups[2])
            else:
                name, day, year = groups[0], int(groups[1]), int(groups[2])
            key = name.casefold()
            month = _MONTH_ABBR.get(key) or _MONTH_FULL.get(key) or 0
            if not month:
                continue
        iso = _iso(year, month, day)
        if iso is not None:
            out.append(DateCandidate(spec=spec.spec, iso=iso))
    return tuple(out)


@lru_cache(maxsize=1 << 16)
def parse_datetime_cell(cell: str) -> str | None:
    """Canonical RFC 3339 rendering of a datetime cell, or ``None``."""
    match = _DATETIME_RE.match(cell.strip())
    if not match:
        return None
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    fraction = match.group(7) or ""
    offset = match.group(8) or ""
    if _iso(year, month, day) is None or hour > 23 or minute > 59 or second > 59:
        return None
    if offset in {"Z", "z"}:
        offset = "Z"
    elif offset:
        sign, rest = offset[0], offset[1:].replace(":", "")
        offset = f"{sign}{rest[:2]}:{rest[2:]}"
    return (
        f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}{fraction}{offset}"
    )


def excel_serial_to_iso(serial: int) -> str:
    return (EXCEL_EPOCH + timedelta(days=serial)).isoformat()


# --------------------------------------------------------------------------
# Per-cell type matching
# --------------------------------------------------------------------------


@lru_cache(maxsize=1 << 16)
def cell_types(cell: str) -> frozenset[ColumnType]:
    """Every type whose micro-parser accepts ``cell`` (D5's per-cell vote).

    ``text`` and ``categorical`` accept everything — the distinction between
    them is made at column level from distinct counts (FR-5).
    """
    matched: set[ColumnType] = {ColumnType.TEXT, ColumnType.CATEGORICAL}
    text = cell.strip()
    if not text:
        return frozenset(matched)
    if parse_bool(text) is not None:
        matched.add(ColumnType.BOOL)
    if digits_core(text) is not None:
        matched.add(ColumnType.DIGITS)
    numeric = parse_numeric(text)
    if numeric is not None:
        matched.add(ColumnType.FLOAT)
        if "." not in numeric:
            matched.add(ColumnType.INTEGER)
    if parse_datetime_cell(text) is not None:
        matched.add(ColumnType.DATETIME)
    elif date_candidates(text):
        matched.add(ColumnType.DATE)
    return frozenset(matched)


def matches_type(cell: str, column_type: ColumnType) -> bool:
    return column_type in cell_types(cell)


# --------------------------------------------------------------------------
# Column type vote (FR-5)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeVote:
    column_type: ColumnType
    coverage: float
    non_null: int
    null_count: int
    distinct_count: int
    coverages: dict[ColumnType, float] = field(default_factory=dict)


def _digits_qualifies(values: list[str], thresholds: Thresholds) -> bool:
    """FR-5: any leading zero, or fixed width ≥ 4 with distinct-ratio > 0.5."""
    if not values:
        return False
    if any(len(v) > 1 and v.startswith("0") for v in values):
        return True
    widths = {len(v) for v in values}
    if len(widths) == 1 and widths.pop() >= thresholds.digits_min_width:
        ratio = len(set(values)) / len(values)
        return ratio > thresholds.digits_min_distinct_ratio
    return False


def infer_column_type(values: list[str | None], thresholds: Thresholds) -> TypeVote:
    """Coverage vote over per-cell micro-parsers (FR-5 / D5).

    Hard missing-value tokens are excluded from the denominator: type,
    missingness and anomaly are one joint decision per column (D5), so a
    sentinel-polluted numeric column still types as numeric.
    """
    null_count = sum(1 for v in values if is_null(v))
    considered = [v for v in values if v is not None and sentinel_kind(v) != "hard"]
    non_null = len(values) - null_count
    distinct = len({v for v in values if v is not None})
    if not considered:
        return TypeVote(ColumnType.TEXT, 1.0, non_null, null_count, distinct)

    counts: Counter[ColumnType] = Counter()
    for value in considered:
        for column_type in cell_types(value):
            counts[column_type] += 1
    total = len(considered)
    coverages = {t: counts.get(t, 0) / total for t in TYPE_PRIORITY}

    digit_values = [d for v in considered if (d := digits_core(v.strip())) is not None]
    for column_type in TYPE_PRIORITY:
        coverage = coverages[column_type]
        if coverage < thresholds.type_majority:
            continue
        if column_type is ColumnType.DIGITS and not _digits_qualifies(digit_values, thresholds):
            continue
        if column_type is ColumnType.CATEGORICAL:
            distinct_considered = len(set(considered))
            ratio = distinct_considered / total
            if (
                distinct_considered > thresholds.categorical_max_distinct
                or ratio > thresholds.categorical_max_ratio
            ):
                continue
        return TypeVote(column_type, coverage, non_null, null_count, distinct, coverages)
    return TypeVote(ColumnType.TEXT, 1.0, non_null, null_count, distinct, coverages)


# --------------------------------------------------------------------------
# Column-level number convention analysis (D7.2 - D7.6)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberAnalysis:
    decisive_counts: dict[str, int]
    n_decisive: int
    majority: str
    agree: float
    currency_counts: dict[str, int]
    currency_cells: int
    non_null: int

    @property
    def mixed(self) -> bool:
        return self.n_decisive > 0 and self.agree < 1.0

    @property
    def single_currency(self) -> str | None:
        return next(iter(self.currency_counts)) if len(self.currency_counts) == 1 else None

    def currency_coverage(self) -> float:
        return self.currency_cells / self.non_null if self.non_null else 0.0


def analyze_numbers(values: list[str | None]) -> NumberAnalysis:
    """D7.2-D7.3: count decisive cells per convention and the currency affixes."""
    decisive: Counter[str] = Counter()
    currency: Counter[str] = Counter()
    currency_cells = 0
    non_null = 0
    for value in values:
        if value is None:
            continue
        non_null += 1
        affixes = split_affixes(value)
        if affixes.currency is not None and number_readings(affixes.core):
            currency[affixes.currency] += 1
            currency_cells += 1
        if not is_convention_bearing(affixes.core):
            continue
        readings = number_readings(affixes.core)
        if len(readings) == 1:
            decisive[next(iter(readings))] += 1
    n_decisive = sum(decisive.values())
    # Ties resolve to DOT (D7.3).
    majority = DOT
    if decisive:
        best = max(decisive.values())
        majority = DOT if decisive.get(DOT, 0) == best else COMMA
    agree = (decisive.get(majority, 0) / n_decisive) if n_decisive else 0.0
    return NumberAnalysis(
        decisive_counts=dict(sorted(decisive.items())),
        n_decisive=n_decisive,
        majority=majority,
        agree=agree,
        currency_counts=dict(sorted(currency.items())),
        currency_cells=currency_cells,
        non_null=non_null,
    )


# --------------------------------------------------------------------------
# Column-level date analysis (D9)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DateAnalysis:
    """The two structurally different outcomes of D9's proof search."""

    candidates: dict[int, list[DateCandidate]]
    order: str | None  # "day-first" | "month-first" | None
    proof_row: int | None
    proof_spec: str | None
    ambiguous_rows: list[int]
    conflict: bool
    invariant_share: float
    formats: dict[str, int]

    @property
    def is_ambiguous(self) -> bool:
        """No cell proves the order *and* at least one cell needs it."""
        return self.order is None and bool(self.ambiguous_rows)

    def chosen(self, row: int) -> DateCandidate | None:
        """Highest-priority candidate compatible with the proven order."""
        for candidate in self.candidates.get(row, []):
            spec = DATE_SPEC_BY_ID[candidate.spec]
            if self.order == "day-first" and spec.order == "mdy":
                continue
            if self.order == "month-first" and spec.order == "dmy":
                continue
            return candidate
        return None

    def readings(self, row: int) -> dict[str, DateCandidate]:
        """The ambiguous pair for a cell, keyed by spec."""
        return {c.spec: c for c in self.candidates.get(row, [])}


def analyze_dates(values: list[str | None]) -> DateAnalysis:
    """Assign formats to a date column and prove day-first vs month-first.

    Day/month order is *proven* only by a cell whose deciding component is
    > 12 anywhere in the column (D9); a column with no such cell and at least
    one two-way cell is ambiguous, which is a structurally different outcome
    (exactly one column-level review item, zero cell-level auto fixes).
    """
    candidates: dict[int, list[DateCandidate]] = {}
    day_first_proofs: list[int] = []
    month_first_proofs: list[int] = []
    ambiguous_rows: list[int] = []
    for row, value in enumerate(values):
        if value is None:
            continue
        found = date_candidates(value)
        if not found:
            continue
        candidates[row] = found
        specs = {c.spec for c in found}
        has_dmy = any(
            DATE_SPEC_BY_ID[s].order == "dmy" and DATE_SPEC_BY_ID[s].partner for s in specs
        )
        has_mdy = any(
            DATE_SPEC_BY_ID[s].order == "mdy" and DATE_SPEC_BY_ID[s].partner for s in specs
        )
        if has_dmy and has_mdy:
            ambiguous_rows.append(row)
        elif has_dmy:
            day_first_proofs.append(row)
        elif has_mdy:
            month_first_proofs.append(row)

    conflict = bool(day_first_proofs and month_first_proofs)
    order: str | None = None
    proof_row: int | None = None
    proof_spec: str | None = None
    if day_first_proofs and not month_first_proofs:
        order, proof_row = "day-first", day_first_proofs[0]
    elif month_first_proofs and not day_first_proofs:
        order, proof_row = "month-first", month_first_proofs[0]
    if proof_row is not None:
        proof_spec = candidates[proof_row][0].spec

    invariant = 0
    for row in ambiguous_rows:
        readings = {c.spec: c.iso for c in candidates[row]}
        values_seen = set(readings.values())
        if len(values_seen) == 1:
            invariant += 1
    invariant_share = invariant / len(ambiguous_rows) if ambiguous_rows else 1.0

    formats: Counter[str] = Counter()
    analysis = DateAnalysis(
        candidates=candidates,
        order=order,
        proof_row=proof_row,
        proof_spec=proof_spec,
        ambiguous_rows=ambiguous_rows,
        conflict=conflict,
        invariant_share=invariant_share,
        formats={},
    )
    for row in candidates:
        chosen = analysis.chosen(row)
        if chosen is not None:
            formats[chosen.spec] += 1
    return DateAnalysis(
        candidates=candidates,
        order=order,
        proof_row=proof_row,
        proof_spec=proof_spec,
        ambiguous_rows=ambiguous_rows,
        conflict=conflict,
        invariant_share=invariant_share,
        formats=dict(sorted(formats.items())),
    )


def has_date_evidence(values: list[str | None], header: str) -> bool:
    """D9: Excel serials are only proposed where the column looks like dates."""
    for value in values:
        if value is not None and date_candidates(value):
            return True
    token = re.split(r"[^a-z0-9]+", header.casefold())
    return bool({"date", "day", "dt", "time", "at", "on", "when"} & set(token))


# --------------------------------------------------------------------------
# Table profiling
# --------------------------------------------------------------------------


def _numeric_values(values: list[str | None]) -> list[float]:
    out: list[float] = []
    for value in values:
        if value is None:
            continue
        canonical = parse_numeric(value)
        if canonical is None:
            continue
        try:
            out.append(float(canonical))
        except ValueError:  # pragma: no cover - canonical form is always float-able
            continue
    return out


def column_stats(
    values: list[str | None], column_type: ColumnType, policy: Policy
) -> dict[str, object]:
    """Type-dependent column statistics (DATA_MODEL §2.4)."""
    stats: dict[str, object] = {}
    present = [v for v in values if v is not None]
    if column_type in {ColumnType.INTEGER, ColumnType.FLOAT}:
        numbers = _numeric_values(values)
        if numbers:
            summary = summarize(numbers, policy.thresholds.outlier_iqr_k)
            stats |= {
                "min": summary.minimum,
                "q1": summary.q1,
                "median": summary.median,
                "q3": summary.q3,
                "max": summary.maximum,
                "mad": summary.mad,
            }
    elif column_type in {ColumnType.DATE, ColumnType.DATETIME}:
        analysis = analyze_dates(values)
        isos = sorted(
            c.iso for row in analysis.candidates for c in [analysis.chosen(row)] if c is not None
        )
        if column_type is ColumnType.DATETIME:
            isos = sorted(
                canonical for v in present if (canonical := parse_datetime_cell(v)) is not None
            )
        if isos:
            stats |= {"min": isos[0], "max": isos[-1]}
        stats |= {"formats": analysis.formats, "ambiguous": analysis.is_ambiguous}
    elif column_type is ColumnType.CATEGORICAL:
        counts = Counter(present)
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        stats["top"] = [[label, count] for label, count in top]

    numbers_analysis = analyze_numbers(values)
    if column_type in {ColumnType.INTEGER, ColumnType.FLOAT} or numbers_analysis.currency_cells:
        convention: dict[str, object] = {
            "grouping": "," if numbers_analysis.majority == DOT else ".",
            "decimal": "." if numbers_analysis.majority == DOT else ",",
            "currency": numbers_analysis.single_currency,
            "decisive_agree": round(numbers_analysis.agree, 6),
        }
        if numbers_analysis.currency_cells:
            convention["currency_coverage"] = round(numbers_analysis.currency_coverage(), 6)
        stats["convention"] = convention
    return stats


def profile_table(
    table: RawTable,
    policy: Policy,
    *,
    original_headers: list[str] | None = None,
    with_stats: bool = True,
) -> TableProfile:
    """Profile every column of ``table`` (types + stats), purely from values.

    ``with_stats=False`` skips the type-dependent statistics, which the
    pipeline uses for its nine per-stage re-typings — detectors need the type,
    and every one of them computes the statistics it needs itself.
    """
    profiles: list[ColumnProfile] = []
    for col in range(table.n_cols):
        values = table.column(col)
        vote = infer_column_type(values, policy.thresholds)
        original = None
        if (
            original_headers is not None
            and col < len(original_headers)
            and original_headers[col] != table.headers[col]
        ):
            original = original_headers[col]
        profiles.append(
            ColumnProfile(
                col_index=col,
                name=table.header(col),
                original_name=original,
                inferred_type=vote.column_type,
                type_coverage=round(vote.coverage, 6),
                non_null=vote.non_null,
                null_count=vote.null_count,
                distinct_count=vote.distinct_count,
                stats=column_stats(values, vote.column_type, policy) if with_stats else {},
            )
        )
    return TableProfile(n_rows=table.n_rows, n_cols=table.n_cols, columns=profiles)


def looks_headerless(headers: list[str], rows: list[list[str | None]], policy: Policy) -> bool:
    """FR-3: is row 0 (currently read as the header) actually data?

    True iff, after typing rows 1..n, *every* header cell matches the
    micro-parser of its column's body type and at least one body column has a
    structured (non-text/categorical) type.  A header cell sitting in a text or
    categorical column is not evidence — which is why a normal header row can
    never trigger this.
    """
    if not headers or not rows:
        return False
    body_types: list[ColumnType] = []
    for col in range(len(headers)):
        values = [row[col] if col < len(row) else None for row in rows]
        body_types.append(infer_column_type(values, policy.thresholds).column_type)
    if not any(t in STRUCTURED_TYPES for t in body_types):
        return False
    for name, body_type in zip(headers, body_types, strict=True):
        if body_type in STRUCTURED_TYPES and not matches_type(name, body_type):
            return False
    return True


def normalize_nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def strip_zero_width(text: str) -> str:
    return text.translate({ord(c): None for c in ZERO_WIDTH_CHARS})
