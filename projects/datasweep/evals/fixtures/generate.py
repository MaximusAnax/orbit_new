"""Seeded fixture generator for the datasweep evals (EVALS.md §4).

Two jobs, deliberately kept apart:

1. **Golden writers** — five canonical, alarm-free tables (UTF-8, ISO dates,
   dot-decimal, trimmed, no sentinels, no duplicate rows, tame numeric
   distributions).  These are the ground truth for repair.
2. **The corrupter** — a seeded, per-cell defect injector that records a
   manifest of every op it applied.  Detection truth and repair truth are
   therefore known *by construction*.

The corrupter shares **no code with the engine**: it writes ``1,234.56`` by
formatting a string, never by calling a datasweep parser, and its statistics
(Tukey fences, Damerau-Levenshtein) are reimplemented here.  That is what stops
the engine from grading its own homework (EVALS.md §2).

Every corruption is *information-preserving*: the golden value is recoverable
from the corrupted cell by the documented rules, so one uniform repair-truth
definition (``correct_fix(corrupted) == golden``) holds for every class.

Usage::

    python evals/fixtures/generate.py --seed 1337
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE / "golden"
CORRUPT_DIR = HERE / "corrupted"

DEFAULT_SEED = 1337

# --------------------------------------------------------------------------
# Pinned op mix (EVALS.md §4.2).  The generator asserts the realized global
# share of every class is within ±2 percentage points of these targets.
# --------------------------------------------------------------------------

CLASS_SHARES: dict[str, float] = {
    "ENC": 0.10,
    "WS": 0.22,
    "MISS": 0.10,
    "TYPE": 0.18,
    "DATE": 0.15,
    "CAT_AUTO": 0.10,
    "CAT_TYPO": 0.05,
    "DUP": 0.05,
    "OUT": 0.05,
}
SHARE_TOLERANCE = 0.02

#: Fraction of a categorical label's occurrences that may be rewritten into a
#: fingerprint variant.  Stricter than EVALS.md §4.2's ≤25% hygiene rule
#: because cluster dominance *is* the merge's confidence (SCOPE.md D12): at
#: ≤4% the golden spelling keeps dominance ≥ 0.96, which is what makes the
#: pinned "auto" tier for `case_label` / `punct_label` true rather than hoped.
CAT_VARIANT_MAX_RATIO = 0.04

#: Share of cells that are genuinely empty in each golden's two nullable,
#: non-`text` columns (EVALS.md §4.1) — the information-preserving target for
#: `sentinel_missing`.
NULL_RATE = 0.06

HARD_SENTINEL_TOKENS = ("NA", "N/A", "null", "NaN", "#N/A")
CURRENCY_SYMBOL = "€"
NBSP = " "
ZWSP = "​"

EXCEL_EPOCH = date(1899, 12, 30)

#: Alternate date formats used by `date_reformat`, one per corrupted column.
#: Two-digit-year forms are excluded on purpose: they are review-capped
#: (SCOPE.md D9), and every DATE op in the pinned mix is auto-expected.
DATE_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%Y/%m/%d",
)
#: Formats whose day/month order needs a deciding component > 12 to be proven.
AMBIGUOUS_FAMILY = {"%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"}

MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MONTH_FULL = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


# --------------------------------------------------------------------------
# Vocabularies — invented data only: no real people, merchants or addresses.
# --------------------------------------------------------------------------

FIRST_NAMES = (
    "José",
    "Zoë",
    "Renée",
    "Mikael",
    "Aurora",
    "Ingrid",
    "Björn",
    "Chloé",
    "Emil",
    "Noor",
    "Amara",
    "Théo",
    "Lucia",
    "Hugo",
    "Freya",
    "Óscar",
    "Nadia",
    "Piotr",
    "Selma",
    "Tomás",
)
LAST_NAMES = (
    "Novak",
    "Delacroix",
    "Fernández",
    "Okafor",
    "Lindqvist",
    "Moreau",
    "Haddad",
    "Sørensen",
    "Ramírez",
    "Weiss",
    "Kovács",
    "Bergström",
    "Marchetti",
    "Dupont",
    "Ivanov",
    "Yilmaz",
    "Nakamura",
    "Silva",
    "Andersen",
    "Costa",
)
CITIES = (
    "Málaga",
    "New Harbor",
    "Silver Falls",
    "Brightwater",
    "Coldspring",
    "Dunmore",
    "Ironwood",
    "Quarrytown",
)
COUNTRIES = (
    "Canada",
    "Denmark",
    "Finland",
    "Japan",
    "Mexico",
    "Norway",
    "Portugal",
    "Uruguay",
)
#: Twelve, not twenty: every whitespace, encoding and label op on a categorical
#: column adds a distinct raw value, and FR-5 drops a column past 50 distinct
#: values out of `categorical` — at which point the CAT detector would never
#: run on it and the fixture, not the engine, would be setting the score.
MERCHANTS = (
    "Café Norte",
    "Blue Harbor Goods",
    "Northwind Supply",
    "Petit Marché",
    "Orchard and Vine",
    "Statler Hardware",
    "Kestrel Outfitters",
    "Delta Pharmacy",
    "Vela Cycles",
    "Fjord Interiors",
    "Trailhead Sports",
    "Nordic Fjäll",
)
CATEGORIES = ("grocery", "hardware", "travel", "utilities", "apparel", "fuel", "dining")
STATUSES = ("paid", "refunded", "pending", "chargeback")
CURRENCIES = ("USD", "EUR", "GBP")
DEVICES = (
    "café",
    "grenier",
    "cellier",
    "véranda",
    "atelier",
    "réserve",
    "salon",
    "cuisine",
)
SENSOR_STATUS = ("ok", "needs service", "offline", "calibrating")
GENDERS = ("female", "male", "nonbinary", "prefer not to say")
FIELD3 = ("alpha", "bravo", "charlie", "delta")
PRODUCT_HEADS = (
    "Café",
    "Fjäll",
    "Orchard",
    "Kestrel",
    "Lumen",
    "Willow",
    "Copper",
    "Nordic",
    "Vela",
    "Rivera",
)
PRODUCT_TAILS = (
    "Chain Set",
    "Rain Shell",
    "Grinder",
    "Lantern",
    "Trowel",
    "Notebook",
    "Kettle",
    "Bracket",
    "Panel",
    "Cartridge",
)
COMMENT_OPENERS = (
    "Delivery arrived",
    "The café order",
    "Support replied",
    "Setup took",
    "Packaging was",
    "The réservation",
    "Checkout felt",
    "Onboarding was",
    "Returns handling was",
    "The manual was",
)
COMMENT_MIDDLES = (
    "well ahead of schedule",
    "about what I expected",
    "later than promised",
    "surprisingly straightforward",
    "confusing in places",
    "clear and unhurried",
    "hard to follow",
    "better than last time",
    "roughly average",
    "genuinely excellent",
)
COMMENT_CLOSERS = (
    "and I would order again.",
    "though the packaging tore.",
    "so no complaints here.",
    "but the invoice was wrong.",
    "and support fixed it fast.",
    "which cost me an afternoon.",
    "and the price felt fair.",
    "although shipping was slow.",
    "so I upgraded the plan.",
    "but I still had questions.",
)

#: Characters whose NFKD form keeps a base letter, plus the ones that do not.
_TRANSLIT = {"ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "Æ": "AE", "ł": "l", "Ł": "L"}


def ascii_fold(text: str) -> str:
    """ASCII transliteration for e-mail local parts (no accents in addresses)."""
    folded = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    stripped = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


# --------------------------------------------------------------------------
# Local statistics — reimplemented so the fixtures never consult the engine.
# --------------------------------------------------------------------------


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[int(position)]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _median(values: list[float]) -> float:
    return _quantile(sorted(values), 0.5)


@dataclass(frozen=True)
class RobustStats:
    """Everything D11's two-test conjunction needs, computed once per column."""

    q1: float
    q3: float
    iqr: float
    median: float
    mad: float
    mean_ad: float

    def trips_both(self, candidate: float) -> bool:
        """Tukey k=3.0 *and* modified z > 3.5 — a value must fail both tests."""
        if self.q1 - 3.0 * self.iqr <= candidate <= self.q3 + 3.0 * self.iqr:
            return False
        if self.mad == 0.0:
            if self.mean_ad == 0.0:
                return False
            modified_z = (candidate - self.median) / (1.253314 * self.mean_ad)
        else:
            modified_z = 0.6745 * (candidate - self.median) / self.mad
        return abs(modified_z) > 3.5


def fences_and_mad(values: list[float]) -> RobustStats:
    """(q1, q3, iqr, median, mad, meanAD) — Tukey inputs plus the MAD scale."""
    ordered = sorted(values)
    q1, q3 = _quantile(ordered, 0.25), _quantile(ordered, 0.75)
    med = _quantile(ordered, 0.5)
    deviations = sorted(abs(value - med) for value in values)
    mad = _quantile(deviations, 0.5)
    mean_ad = sum(deviations) / max(len(deviations), 1)
    return RobustStats(q1=q1, q3=q3, iqr=q3 - q1, median=med, mad=mad, mean_ad=mean_ad)


def nn_window(left: str, right: str) -> int:
    """D10b's merge window: ≤ 1 edit, or ≤ 2 once a label reaches 8 characters."""
    return 2 if max(len(left), len(right)) >= 8 else 1


def damerau_levenshtein(left: str, right: str) -> int:
    """Optimal string alignment distance — enough for the ≥4 spacing assert."""
    rows, cols = len(left) + 1, len(right) + 1
    grid = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        grid[i][0] = i
    for j in range(cols):
        grid[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            grid[i][j] = min(grid[i - 1][j] + 1, grid[i][j - 1] + 1, grid[i - 1][j - 1] + cost)
            if (
                i > 1
                and j > 1
                and left[i - 1] == right[j - 2]
                and left[i - 2] == right[j - 1]
            ):
                grid[i][j] = min(grid[i][j], grid[i - 2][j - 2] + cost)
    return grid[-1][-1]


# --------------------------------------------------------------------------
# Golden table model
# --------------------------------------------------------------------------


@dataclass
class Golden:
    """One canonical fixture table plus the schema facts the corrupter needs."""

    name: str
    fmt: str  # csv | tsv | jsonl | xlsx
    headers: list[str]
    kinds: list[str]  # text|categorical|integer|float|digits|date|datetime
    rows: list[list[str | None]]
    nullable: tuple[str, str]
    currency_columns: tuple[str, ...]
    outlier_columns: tuple[str, ...]
    thousands_columns: tuple[str, ...]
    decimals: dict[str, int] = field(default_factory=dict)
    legit_extremes: list[dict[str, Any]] = field(default_factory=list)

    def index(self, header: str) -> int:
        return self.headers.index(header)

    def columns_of_kind(self, *kinds: str) -> list[int]:
        return [i for i, kind in enumerate(self.kinds) if kind in kinds]

    def column(self, col: int) -> list[str | None]:
        return [row[col] for row in self.rows]


def uniform_floats(rng: random.Random, n: int, low: float, high: float, places: int) -> list[str]:
    """Bounded uniform draws — Tukey-tame by construction (EVALS.md §4.1).

    A uniform sample's fences sit at Q1 − 1.5·range and Q3 + 1.5·range, i.e.
    outside its own support, so a golden numeric column cannot contain a
    self-inflicted outlier.  That is what makes ``M3_clean_findings = 0``
    an honest bar rather than a lucky one.
    """
    return [f"{rng.uniform(low, high):.{places}f}" for _ in range(n)]


def uniform_ints(rng: random.Random, n: int, low: int, high: int) -> list[str]:
    return [str(rng.randint(low, high)) for _ in range(n)]


def balanced_labels(rng: random.Random, n: int, labels: tuple[str, ...]) -> list[str]:
    """Each label used ⌈n/k⌉ times, then shuffled — no label is ever rare."""
    out = [labels[i % len(labels)] for i in range(n)]
    rng.shuffle(out)
    return out


def spread_dates(rng: random.Random, n: int, start: date, span_days: int) -> list[str]:
    return [(start + timedelta(days=rng.randint(0, span_days))).isoformat() for _ in range(n)]


def apply_nulls(rng: random.Random, values: list[str | None], rate: float) -> list[str | None]:
    """Seed genuinely-empty cells: data, not defects (SCOPE.md FR-6)."""
    count = round(len(values) * rate)
    positions = rng.sample(range(len(values)), count)
    for position in positions:
        values[position] = None
    return values


def distinct_ids(rng: random.Random, n: int, low: int, high: int) -> list[str]:
    """Distinct ids of *varying* width, so they type `integer`, not `digits`.

    A fixed-width id column with a high distinct ratio is exactly FR-5's
    `digits` rule; drawing across a range that spans several widths keeps the
    two anti-overfit `code`/`txn_id` columns honest.
    """
    return [str(value) for value in sorted(rng.sample(range(low, high), n))]


# --------------------------------------------------------------------------
# The five goldens
# --------------------------------------------------------------------------


def build_contacts(rng: random.Random) -> Golden:
    n = 400
    combos = [(first, last) for first in FIRST_NAMES for last in LAST_NAMES]
    rng.shuffle(combos)
    combos = combos[:n]
    names = [f"{first} {last}" for first, last in combos]
    emails = [
        f"{ascii_fold(first).lower()}.{ascii_fold(last).lower()}{index}@example.net"
        for index, (first, last) in enumerate(combos)
    ]
    cities = balanced_labels(rng, n, CITIES)
    countries = balanced_labels(rng, n, COUNTRIES)
    zips = [f"{rng.randint(1000, 99999):05d}" for _ in range(n)]
    for position in rng.sample(range(n), 40):  # guarantee leading zeros exist
        zips[position] = f"{rng.randint(100, 9999):05d}"
    signup = spread_dates(rng, n, date(2021, 1, 1), 1300)
    ages: list[str | None] = list(uniform_ints(rng, n, 18, 90))
    values: list[str | None] = list(uniform_floats(rng, n, 120.0, 9800.0, 2))
    apply_nulls(rng, ages, NULL_RATE)
    apply_nulls(rng, values, NULL_RATE)
    rows = [
        [names[i], emails[i], cities[i], countries[i], zips[i], signup[i], ages[i], values[i]]
        for i in range(n)
    ]
    return Golden(
        name="contacts.csv",
        fmt="csv",
        headers=["name", "email", "city", "country", "zip", "signup_date", "age", "value"],
        kinds=[
            "text",
            "text",
            "categorical",
            "categorical",
            "digits",
            "date",
            "integer",
            "float",
        ],
        rows=rows,
        nullable=("age", "value"),
        currency_columns=("value",),
        outlier_columns=("age", "value"),
        thousands_columns=("value",),
        decimals={"value": 2},
    )


def build_transactions(rng: random.Random) -> Golden:
    n = 1200
    txn = distinct_ids(rng, n, 1000, 999_999)
    dates = spread_dates(rng, n, date(2023, 1, 1), 700)
    amounts = uniform_floats(rng, n, 3.0, 4800.0, 2)
    currency = balanced_labels(rng, n, CURRENCIES)
    merchant = balanced_labels(rng, n, MERCHANTS)
    category: list[str | None] = list(balanced_labels(rng, n, CATEGORIES))
    status = balanced_labels(rng, n, STATUSES)
    code: list[str | None] = list(uniform_ints(rng, n, 1, 99))
    apply_nulls(rng, category, NULL_RATE)
    apply_nulls(rng, code, NULL_RATE)
    rows = [
        [
            txn[i],
            dates[i],
            amounts[i],
            currency[i],
            merchant[i],
            category[i],
            status[i],
            code[i],
        ]
        for i in range(n)
    ]
    return Golden(
        name="transactions.csv",
        fmt="csv",
        headers=["txn_id", "date", "amount", "currency", "merchant", "category", "status", "code"],
        kinds=[
            "integer",
            "date",
            "float",
            "categorical",
            "categorical",
            "categorical",
            "categorical",
            "integer",
        ],
        rows=rows,
        nullable=("code", "category"),
        currency_columns=("amount",),
        outlier_columns=("amount", "code"),
        thousands_columns=("amount",),
        decimals={"amount": 2},
    )


def build_sensors(rng: random.Random) -> Golden:
    n = 1200
    start = date(2024, 3, 1)
    stamps = []
    for index in range(n):
        moment = start + timedelta(days=index // 24)
        hour, minute, second = index % 24, (index * 7) % 60, (index * 13) % 60
        stamps.append(f"{moment.isoformat()}T{hour:02d}:{minute:02d}:{second:02d}")
    devices = balanced_labels(rng, n, DEVICES)
    temperature = uniform_floats(rng, n, 8.0, 34.0, 1)
    humidity: list[str | None] = list(uniform_floats(rng, n, 28.0, 76.0, 1))
    status: list[str | None] = list(balanced_labels(rng, n, SENSOR_STATUS))
    apply_nulls(rng, humidity, NULL_RATE)
    apply_nulls(rng, status, NULL_RATE)

    # EVALS §4.1: four *legitimate* heat-wave readings, placed inside both the
    # Tukey-3.0 and the MAD-3.5 fence by construction — outlier precision has
    # to survive real extremes, not just injected ones.
    extremes: list[dict[str, Any]] = []
    for offset, reading in enumerate(("40.8", "41.1", "41.4", "41.6")):
        row_index = 200 + offset * 137
        temperature[row_index] = reading
        extremes.append({"row": row_index, "col": 2, "value": reading})

    rows = [
        [stamps[i], devices[i], temperature[i], humidity[i], status[i]] for i in range(n)
    ]
    return Golden(
        name="sensors.jsonl",
        fmt="jsonl",
        headers=["ts", "device_id", "temperature_c", "humidity_pct", "status"],
        kinds=["datetime", "categorical", "float", "float", "categorical"],
        rows=rows,
        nullable=("humidity_pct", "status"),
        currency_columns=(),
        outlier_columns=("temperature_c", "humidity_pct"),
        thousands_columns=(),
        decimals={"temperature_c": 1, "humidity_pct": 1},
        legit_extremes=extremes,
    )


def build_inventory(rng: random.Random) -> Golden:
    n = 300
    skus = [f"{value:06d}" for value in sorted(rng.sample(range(1, 400_000), n))]
    products = []
    seen: set[str] = set()
    while len(products) < n:
        candidate = (
            f"{rng.choice(PRODUCT_HEADS)} {rng.choice(PRODUCT_TAILS)} {rng.randint(10, 999)}"
        )
        if candidate not in seen:
            seen.add(candidate)
            products.append(candidate)
    qty = uniform_ints(rng, n, 1, 500)
    price = uniform_floats(rng, n, 1.5, 990.0, 2)
    restock: list[str | None] = list(spread_dates(rng, n, date(2024, 1, 1), 500))
    codes: list[str | None] = [f"{rng.randint(0, 9999):04d}" for _ in range(n)]
    apply_nulls(rng, restock, NULL_RATE)
    apply_nulls(rng, codes, NULL_RATE)
    rows = [[skus[i], products[i], qty[i], price[i], restock[i], codes[i]] for i in range(n)]
    return Golden(
        name="inventory.xlsx",
        fmt="xlsx",
        headers=["sku", "product", "qty", "unit_price", "restock_date", "code"],
        kinds=["digits", "text", "integer", "float", "date", "digits"],
        rows=rows,
        nullable=("restock_date", "code"),
        currency_columns=("unit_price",),
        outlier_columns=("qty", "unit_price"),
        thousands_columns=(),
        decimals={"unit_price": 2},
    )


def build_survey(rng: random.Random) -> Golden:
    n = 600
    ids = distinct_ids(rng, n, 1000, 999_999)
    gender = balanced_labels(rng, n, GENDERS)
    satisfaction: list[str | None] = list(uniform_ints(rng, n, 1, 5))
    comments: list[str] = []
    seen: set[str] = set()
    while len(comments) < n:
        candidate = (
            f"{rng.choice(COMMENT_OPENERS)} {rng.choice(COMMENT_MIDDLES)} "
            f"{rng.choice(COMMENT_CLOSERS)}"
        )
        if candidate not in seen:
            seen.add(candidate)
            comments.append(candidate)
    submitted = spread_dates(rng, n, date(2022, 6, 1), 900)
    income: list[str | None] = list(uniform_floats(rng, n, 24000.0, 148000.0, 2))
    field_three = balanced_labels(rng, n, FIELD3)
    apply_nulls(rng, income, NULL_RATE)
    apply_nulls(rng, satisfaction, NULL_RATE)
    rows = [
        [
            ids[i],
            gender[i],
            satisfaction[i],
            comments[i],
            submitted[i],
            income[i],
            field_three[i],
        ]
        for i in range(n)
    ]
    return Golden(
        name="survey.tsv",
        fmt="tsv",
        headers=[
            "respondent_id",
            "gender",
            "satisfaction",
            "comment",
            "submitted",
            "income",
            "field3",
        ],
        kinds=[
            "integer",
            "categorical",
            "integer",
            "text",
            "date",
            "float",
            "categorical",
        ],
        rows=rows,
        nullable=("income", "satisfaction"),
        currency_columns=("income",),
        outlier_columns=("income", "satisfaction"),
        thousands_columns=("income",),
        decimals={"income": 2},
    )


GOLDEN_BUILDERS = (
    build_contacts,
    build_transactions,
    build_sensors,
    build_inventory,
    build_survey,
)

#: How many corrupted variants each golden gets (EVALS.md §4: 3/3/2/2/3 = 13).
CORRUPTIONS_PER_GOLDEN = {
    "contacts.csv": 3,
    "transactions.csv": 3,
    "sensors.jsonl": 2,
    "inventory.xlsx": 2,
    "survey.tsv": 3,
}


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------


def write_delimited(path: Path, golden: Golden, rows: list[list[str | None]]) -> None:
    delimiter = "\t" if golden.fmt == "tsv" else ","
    buffer = io.StringIO(newline="")
    writer = csv.writer(
        buffer, delimiter=delimiter, lineterminator="\n", quoting=csv.QUOTE_MINIMAL
    )
    writer.writerow(golden.headers)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    path.write_text(buffer.getvalue(), encoding="utf-8")


_NUMERIC_KINDS = {"integer", "float"}


def _json_scalar(text: str | None, kind: str) -> str:
    """Numbers stay JSON numbers while they are still canonical.

    A corrupted numeric cell (``"€21.4"``, ``"21,4"``) is emitted as a JSON
    string, which is exactly what a real export does once a formatter has been
    at it — and it keeps the reader's verbatim-string contract intact.
    """
    if text is None:
        return "null"
    if kind in _NUMERIC_KINDS and _is_canonical_number(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _is_canonical_number(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    if not body:
        return False
    if body.count(".") > 1:
        return False
    return all(part.isdigit() and part.isascii() for part in body.split(".") if part != "") and any(
        ch.isdigit() for ch in body
    )


def write_jsonl(path: Path, golden: Golden, rows: list[list[str | None]]) -> None:
    lines = []
    for row in rows:
        parts = [
            f"{json.dumps(header, ensure_ascii=False)}: {_json_scalar(row[i], golden.kinds[i])}"
            for i, header in enumerate(golden.headers)
        ]
        lines.append("{" + ", ".join(parts) + "}")
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def write_xlsx(path: Path, golden: Golden, rows: list[list[str | None]]) -> None:
    """Byte-reproducible xlsx (EVALS.md §4.1).

    openpyxl stamps ``docProps/core.xml`` from the wall clock and the zip
    container records entry mtimes, so a plain ``wb.save`` is not diffable
    across regenerations.  Both are pinned here, then the archive is rewritten
    with fixed entry timestamps and a fixed entry order.
    """
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "inventory"
    sheet.append(golden.headers)
    for row in rows:
        sheet.append(["" if cell is None else cell for cell in row])
    pinned = datetime(2020, 1, 1, 0, 0, 0)
    workbook.properties.creator = "datasweep-evals"
    workbook.properties.lastModifiedBy = "datasweep-evals"
    workbook.properties.created = pinned
    workbook.properties.modified = pinned

    raw = io.BytesIO()
    workbook.save(raw)
    workbook.close()

    source = zipfile.ZipFile(io.BytesIO(raw.getvalue()))
    names = sorted(source.namelist())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name in names:
            payload = source.read(name)
            if name == "docProps/core.xml":
                payload = _pin_core_properties(payload)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, payload)
    source.close()
    path.write_bytes(out.getvalue())


def _pin_core_properties(payload: bytes) -> bytes:
    import re

    text = payload.decode("utf-8")
    text = re.sub(
        r"<dcterms:(created|modified)([^>]*)>[^<]*</dcterms:\1>",
        r"<dcterms:\1\2>2020-01-01T00:00:00Z</dcterms:\1>",
        text,
    )
    return text.encode("utf-8")


def write_table(path: Path, golden: Golden, rows: list[list[str | None]]) -> None:
    if golden.fmt == "jsonl":
        write_jsonl(path, golden, rows)
    elif golden.fmt == "xlsx":
        write_xlsx(path, golden, rows)
    else:
        write_delimited(path, golden, rows)


# --------------------------------------------------------------------------
# The corrupter
# --------------------------------------------------------------------------


@dataclass
class Op:
    op: str
    klass: str
    row: int
    col: int | None
    before: str | None
    after: str | None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "class": self.klass,
            "row": self.row,
            "col": self.col,
            "before": self.before,
            "after": self.after,
            **self.extra,
        }


class Corrupter:
    """Injects a pinned mix of information-preserving defects (EVALS.md §4.2)."""

    def __init__(self, golden: Golden, rng: random.Random, op_rate: float, variant: int) -> None:
        self.golden = golden
        self.rng = rng
        self.op_rate = op_rate
        self.variant = variant
        self.rows: list[list[str | None]] = [list(row) for row in golden.rows]
        self.ops: list[Op] = []
        self.used: set[tuple[int, int]] = set()
        self.frozen_rows: set[int] = set()
        self.column_truth: dict[str, dict[str, Any]] = {}
        self._variant_budget: dict[tuple[int, str], int] = {}
        self._typo_variants: set[str] = set()
        self._extremes: dict[int, set[str]] = {}
        self._stats_cache: dict[int, RobustStats] = {}
        self._variant_spelling: dict[tuple[int, str], tuple[str, str]] = {}

    # -- pools ------------------------------------------------------------

    def _cells(self, columns: list[int], predicate: Any) -> list[tuple[int, int]]:
        out = []
        for row_index in range(len(self.rows)):
            if row_index in self.frozen_rows:
                continue
            for col in columns:
                if (row_index, col) in self.used:
                    continue
                value = self.golden.rows[row_index][col]
                if predicate(value):
                    out.append((row_index, col))
        return out

    def _take(self, pool: list[tuple[int, int]], count: int) -> list[tuple[int, int]]:
        if count <= 0 or not pool:
            return []
        chosen = self.rng.sample(pool, min(count, len(pool)))
        return sorted(chosen)

    def _set(self, row: int, col: int, value: str | None) -> None:
        self.rows[row][col] = value
        self.used.add((row, col))

    # -- ops --------------------------------------------------------------

    def duplicate_rows(self, count: int) -> None:
        """Allocated first; both the source row and its copy are then frozen."""
        n = len(self.rows)
        candidates = list(range(n // 2, n))
        self.rng.shuffle(candidates)
        sources = list(range(0, n // 2))
        self.rng.shuffle(sources)
        taken = 0
        for target in candidates:
            if taken >= count:
                break
            if target in self.frozen_rows:
                continue
            source = next((s for s in sources if s not in self.frozen_rows), None)
            if source is None:
                break
            sources.remove(source)
            self.rows[target] = list(self.golden.rows[source])
            self.frozen_rows.add(target)
            self.frozen_rows.add(source)
            self.ops.append(
                Op(
                    op="duplicate_row",
                    klass="DUP",
                    row=target,
                    col=None,
                    before=None,
                    after=None,
                    extra={"source_row": source},
                )
            )
            taken += 1

    def sentinel_missing(self, count: int) -> None:
        columns = [self.golden.index(name) for name in self.golden.nullable]
        pool = self._cells(columns, lambda value: value is None)
        for row, col in self._take(pool, count):
            token = HARD_SENTINEL_TOKENS[self.rng.randrange(len(HARD_SENTINEL_TOKENS))]
            self._set(row, col, token)
            self.ops.append(Op("sentinel_missing", "MISS", row, col, None, token))

    def mojibake(self, count: int) -> None:
        columns = list(range(len(self.golden.headers)))
        pool = self._cells(
            columns, lambda value: value is not None and any(ord(ch) > 127 for ch in value)
        )
        for row, col in self._take(pool, count):
            before = self.golden.rows[row][col]
            assert before is not None
            damaged = before.encode("utf-8").decode("cp1252")
            self._set(row, col, damaged)
            self.ops.append(Op("mojibake_encode", "ENC", row, col, before, damaged))

    def whitespace(self, count: int) -> None:
        every = list(range(len(self.golden.headers)))
        spaced_categorical = [
            index
            for index, kind in enumerate(self.golden.kinds)
            if kind == "categorical"
            and any(" " in (value or "") for value in self.golden.column(index))
        ]
        plans: list[tuple[str, list[int], Any]] = [
            ("pad_whitespace", every, lambda value: value is not None),
            ("insert_nbsp", every, lambda value: value is not None),
            ("insert_zero_width", every, lambda value: value is not None),
            (
                "double_internal_space",
                spaced_categorical,
                lambda value: value is not None and " " in value,
            ),
        ]
        for (op_name, columns, predicate), quota in _split(plans, count):
            pool = self._cells(columns, predicate)
            for row, col in self._take(pool, quota):
                before = self.golden.rows[row][col]
                assert before is not None
                if op_name == "pad_whitespace":
                    after = f"  {before} "
                elif op_name == "insert_nbsp":
                    after = f"{NBSP}{before}{NBSP}"
                elif op_name == "insert_zero_width":
                    after = f"{before}{ZWSP}"
                else:
                    head, _, tail = before.partition(" ")
                    after = f"{head}  {tail}"
                self._set(row, col, after)
                self.ops.append(Op(op_name, "WS", row, col, before, after))

    def type_ops(self, count: int) -> None:
        golden = self.golden
        float_cols = [i for i, kind in enumerate(golden.kinds) if kind == "float"]
        thousands_cols = [golden.index(name) for name in golden.thousands_columns]
        currency_cols = [golden.index(name) for name in golden.currency_columns]
        apostrophe_cols = [
            i for i, kind in enumerate(golden.kinds) if kind in {"digits", "integer", "float"}
        ]

        def big_decimal(value: str | None) -> bool:
            if value is None or "." not in value:
                return False
            integer = value.split(".")[0].lstrip("-")
            return len(integer) >= 4

        plans: list[tuple[str, list[int], Any]] = [
            ("thousands_sep", thousands_cols, big_decimal),
            ("decimal_comma", float_cols, lambda value: value is not None and "." in value),
            (
                "currency_prefix",
                currency_cols,
                lambda value: value is not None and _is_canonical_number(value),
            ),
            ("leading_apostrophe", apostrophe_cols, lambda value: value is not None),
        ]
        for (op_name, columns, predicate), quota in _split(plans, count):
            pool = self._cells(columns, predicate)
            for row, col in self._take(pool, quota):
                before = self.golden.rows[row][col]
                assert before is not None
                if op_name == "thousands_sep":
                    after = self._group_thousands(before)
                elif op_name == "decimal_comma":
                    after = before.replace(".", ",")
                elif op_name == "currency_prefix":
                    after = f"{CURRENCY_SYMBOL}{before}"
                else:
                    after = f"'{before}"
                self._set(row, col, after)
                self.ops.append(Op(op_name, "TYPE", row, col, before, after))

    @staticmethod
    def _group_thousands(value: str) -> str:
        sign = "-" if value.startswith("-") else ""
        body = value.lstrip("-")
        integer, _, fraction = body.partition(".")
        grouped = f"{int(integer):,}"
        return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"

    def date_ops(self, count: int, serial_count: int) -> None:
        golden = self.golden
        date_cols = [i for i, kind in enumerate(golden.kinds) if kind == "date"]
        datetime_cols = [i for i, kind in enumerate(golden.kinds) if kind == "datetime"]
        if not date_cols and not datetime_cols:
            return

        if date_cols:
            col = date_cols[0]
            spec = DATE_FORMATS[(self.variant + col) % len(DATE_FORMATS)]
            self._reformat_dates(col, spec, count - serial_count)
            if serial_count:
                self._excel_serials(col, serial_count)
        else:
            col = datetime_cols[0]
            self._reformat_datetimes(col, count)

    def _reformat_dates(self, col: int, spec: str, count: int) -> None:
        pool = self._cells([col], lambda value: value is not None)
        if not pool:
            return
        chosen = self._take(pool, count)
        proof_row: int | None = None
        if spec in AMBIGUOUS_FAMILY:
            # D9: the order is *proven* only by a cell whose deciding component
            # is > 12, so the corrupter guarantees the column stays provable.
            deciding = [cell for cell in chosen if self._day_of(cell[0], col) > 12]
            if not deciding:
                extra = [cell for cell in pool if self._day_of(cell[0], col) > 12]
                if extra:
                    swap = self.rng.choice(extra)
                    chosen = sorted({swap, *chosen[1:]}) if chosen else [swap]
                    deciding = [swap]
            proof_row = min(row for row, _ in deciding) if deciding else None

        for row, column in chosen:
            before = self.golden.rows[row][column]
            assert before is not None
            after = self._format_date(before, spec)
            self._set(row, column, after)
            self.ops.append(Op("date_reformat", "DATE", row, column, before, after))

        self.column_truth[self.golden.headers[col]] = {
            "date_order": "provable",
            "format": spec,
            "proof_row": proof_row,
        }

    def _reformat_datetimes(self, col: int, count: int) -> None:
        pool = self._cells([col], lambda value: value is not None)
        for row, column in self._take(pool, count):
            before = self.golden.rows[row][column]
            assert before is not None
            after = before.replace("T", " ")
            self._set(row, column, after)
            self.ops.append(Op("date_reformat", "DATE", row, column, before, after))
        self.column_truth[self.golden.headers[col]] = {
            "date_order": "provable",
            "format": "rfc3339-space",
            "proof_row": None,
        }

    def _excel_serials(self, col: int, count: int) -> None:
        pool = self._cells([col], lambda value: value is not None)
        for row, column in self._take(pool, count):
            before = self.golden.rows[row][column]
            assert before is not None
            year, month, day = (int(part) for part in before.split("-"))
            serial = (date(year, month, day) - EXCEL_EPOCH).days
            self._set(row, column, str(serial))
            self.ops.append(Op("excel_serial", "DATE", row, column, before, str(serial)))

    def _day_of(self, row: int, col: int) -> int:
        value = self.golden.rows[row][col]
        assert value is not None
        return int(value.split("-")[2])

    @staticmethod
    def _format_date(iso: str, spec: str) -> str:
        year, month, day = (int(part) for part in iso.split("-"))
        return {
            "%d/%m/%Y": f"{day:02d}/{month:02d}/{year}",
            "%m/%d/%Y": f"{month:02d}/{day:02d}/{year}",
            "%d-%m-%Y": f"{day:02d}-{month:02d}-{year}",
            "%d.%m.%Y": f"{day:02d}.{month:02d}.{year}",
            "%b %d, %Y": f"{MONTH_ABBR[month - 1]} {day:02d}, {year}",
            "%d %b %Y": f"{day:02d} {MONTH_ABBR[month - 1]} {year}",
            "%B %d, %Y": f"{MONTH_FULL[month - 1]} {day}, {year}",
            "%Y/%m/%d": f"{year}/{month:02d}/{day:02d}",
        }[spec]

    # -- categorical ------------------------------------------------------

    def _categorical_columns(self) -> list[int]:
        return [index for index, kind in enumerate(self.golden.kinds) if kind == "categorical"]

    def _variant_allowance(self, col: int, label: str) -> int:
        key = (col, label)
        if key not in self._variant_budget:
            occurrences = sum(1 for value in self.golden.column(col) if value == label)
            self._variant_budget[key] = int(occurrences * CAT_VARIANT_MAX_RATIO)
        return self._variant_budget[key]

    def cat_variants(self, count: int) -> None:
        """`case_label` / `punct_label` — fingerprint-mergeable, auto-expected.

        Exactly **one** variant spelling per (column, label): repeated ops on
        the same label reuse it.  Two reasons.  A second spelling would split
        the fingerprint cluster and drop its dominance — which *is* the merge's
        confidence (D12) — below the 0.95 auto threshold; and every extra
        spelling is another distinct value in a column that must stay under
        FR-5's categorical ceiling, or the CAT detector would never run on it.
        """
        columns = self._categorical_columns()
        if not columns:
            return
        for col, quota in _even_split(columns, count):
            pool = self._cells([col], lambda value: value is not None)
            self.rng.shuffle(pool)
            applied = 0
            for row, column in pool:
                if applied >= quota:
                    break
                label = self.golden.rows[row][column]
                assert label is not None
                if self._variant_allowance(column, label) <= 0:
                    continue
                self._variant_budget[(column, label)] -= 1
                op_name, after = self._variant_for(column, label)
                self._set(row, column, after)
                self.ops.append(Op(op_name, "CAT_AUTO", row, column, label, after))
                applied += 1

    def _variant_for(self, col: int, label: str) -> tuple[str, str]:
        cached = self._variant_spelling.get((col, label))
        if cached is not None:
            return cached
        labels = sorted({value for value in self.golden.column(col) if value})
        use_case = labels.index(label) % 2 == 0
        if use_case:
            upper = label.upper()
            spelling = ("case_label", upper if upper != label else label.lower())
        else:
            spelling = ("punct_label", f"({label})")
        if spelling[1] == label:  # pragma: no cover - defensive
            spelling = ("punct_label", f"{label}.")
        self._variant_spelling[(col, label)] = spelling
        return spelling

    def cat_typos(self, count: int) -> None:
        """`typo_label` — a distance-1 edit, unique so it stays a rare minority.

        D10b only proposes a nearest-neighbour merge when the minority is rare
        (ratio ≤ 5%) *and* the majority has ≥ 20 occurrences, so a typo that
        repeated dozens of times would be correctly ignored by the engine and
        would silently cost CAT recall.  One cell per variant keeps the eval
        measuring the detector rather than the fixture.
        """
        columns = self._categorical_columns()
        if not columns:
            return
        for col, quota in _even_split(columns, count):
            legit = {value for value in self.golden.column(col) if value}
            pool = self._cells([col], lambda value: value is not None)
            self.rng.shuffle(pool)
            applied = 0
            for row, column in pool:
                if applied >= quota:
                    break
                label = self.golden.rows[row][column]
                assert label is not None
                if self._variant_allowance(column, label) <= 0:
                    continue
                typo = self._make_typo(label, legit)
                if typo is None:
                    continue
                self._variant_budget[(column, label)] -= 1
                self._typo_variants.add(typo)
                self._set(row, column, typo)
                self.ops.append(Op("typo_label", "CAT_TYPO", row, column, label, typo))
                applied += 1

    def _make_typo(self, label: str, legit: set[str]) -> str | None:
        """A distance-1 edit of ``label`` that is ≥ 2 edits from every *other* label.

        The source label is excluded from the spacing test on purpose — the
        typo is supposed to sit one edit from it.  What must not happen is a
        typo landing inside D10b's window around a *different* legitimate
        label, which would make the correct merge target ambiguous.
        """
        others = [value for value in legit if value != label]
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        positions = [index for index in range(1, len(label)) if label[index] != " "]
        self.rng.shuffle(positions)
        for position in positions:
            for letter in alphabet:
                candidate = label[:position] + letter + label[position + 1 :]
                if candidate == label or candidate in legit or candidate in self._typo_variants:
                    continue
                if all(
                    damerau_levenshtein(candidate, other) > nn_window(candidate, other)
                    for other in others
                ):
                    return candidate
        return None

    # -- outliers ---------------------------------------------------------

    def outliers(self, count: int) -> None:
        golden = self.golden
        columns = [golden.index(name) for name in golden.outlier_columns]
        if not columns:
            return
        pool = self._cells(columns, lambda value: value is not None)
        for row, col in self._take(pool, count):
            before = self.golden.rows[row][col]
            assert before is not None
            after = self._extreme_value(col)
            self._set(row, col, after)
            self.ops.append(Op("inject_outlier", "OUT", row, col, before, after))

    def _extreme_value(self, col: int) -> str:
        values = [
            float(value)
            for value in self.golden.column(col)
            if value is not None and _is_canonical_number(value)
        ]
        stats = self._column_stats(col, values)
        places = self.golden.decimals.get(self.golden.headers[col], 0)
        for _ in range(64):
            multiplier = self.rng.uniform(8.0, 15.0)
            sign = 1 if self.rng.random() < 0.5 else -1
            candidate = stats.median + sign * multiplier * max(stats.iqr, 1.0)
            text = f"{candidate:.{places}f}" if places else str(int(round(candidate)))
            if not stats.trips_both(float(text)):
                continue
            used = self._extremes.setdefault(col, set())
            if _looks_like_missing_code(text) or text in used:
                continue
            used.add(text)
            return text
        raise AssertionError(f"could not build a clean outlier for column {col}")

    def _column_stats(self, col: int, values: list[float]) -> RobustStats:
        cached = self._stats_cache.get(col)
        if cached is None:
            cached = fences_and_mad(values)
            self._stats_cache[col] = cached
        return cached


def _even_split(items: list[int], count: int) -> list[tuple[int, int]]:
    """Spread ``count`` ops evenly over columns, so no column absorbs them all."""
    if not items:
        return []
    base, remainder = divmod(count, len(items))
    return [
        (item, base + (1 if index < remainder else 0)) for index, item in enumerate(items)
    ]


def _split(
    plans: list[tuple[str, list[int], Any]], count: int
) -> list[tuple[tuple[str, list[int], Any], int]]:
    """Spread ``count`` ops evenly over the sub-ops that have a usable pool."""
    live = [plan for plan in plans if plan[1]]
    if not live:
        return []
    base, remainder = divmod(count, len(live))
    return [(plan, base + (1 if index < remainder else 0)) for index, plan in enumerate(live)]


def _looks_like_missing_code(text: str) -> bool:
    """Mirror of D8's numeric-sentinel shape, so injected outliers avoid it."""
    integer = text.split(".")[0].lstrip("-")
    if not integer.isdigit() or len(integer) < 2 or int(integer) < 99:
        return False
    return set(integer) == {"9"} or integer.endswith("00")


# --------------------------------------------------------------------------
# Allocation
# --------------------------------------------------------------------------


def allocate(golden: Golden, n_ops: int) -> dict[str, int]:
    """Per-file class targets, with impossible classes redistributed.

    ``inventory.xlsx`` has no categorical column at all, so its CAT budget
    cannot be spent; rather than silently under-filling the file, the budget is
    redistributed over the classes that file *can* carry.  The global mix is
    still asserted against EVALS.md §4.2's pinned shares.
    """
    possible: dict[str, bool] = {
        "ENC": any(
            any(ord(ch) > 127 for ch in (value or ""))
            for column in range(len(golden.headers))
            for value in golden.column(column)
        ),
        "WS": True,
        "MISS": True,
        "TYPE": bool(golden.columns_of_kind("float", "integer", "digits")),
        "DATE": bool(golden.columns_of_kind("date", "datetime")),
        "CAT_AUTO": bool(golden.columns_of_kind("categorical")),
        "CAT_TYPO": bool(golden.columns_of_kind("categorical")),
        "DUP": True,
        "OUT": bool(golden.outlier_columns),
    }
    live = {name: share for name, share in CLASS_SHARES.items() if possible[name]}
    total = sum(live.values())
    counts = {name: int(n_ops * share / total) for name, share in live.items()}
    shortfall = n_ops - sum(counts.values())
    for name in sorted(live, key=lambda key: -live[key])[: max(shortfall, 0)]:
        counts[name] += 1
    return {name: counts.get(name, 0) for name in CLASS_SHARES}


def corrupt(golden: Golden, variant: int, seed: int) -> tuple[Corrupter, dict[str, Any]]:
    rng = random.Random(f"{seed}:{golden.name}:{variant}")
    n_cells = len(golden.rows) * len(golden.headers)
    op_rate = 0.06 if golden.fmt == "xlsx" else round(rng.uniform(0.03, 0.06), 4)
    n_ops = round(op_rate * n_cells)
    targets = allocate(golden, n_ops)

    corrupter = Corrupter(golden, rng, op_rate, variant)
    corrupter.duplicate_rows(targets["DUP"])
    corrupter.sentinel_missing(targets["MISS"])
    corrupter.mojibake(targets["ENC"])
    corrupter.whitespace(targets["WS"])
    corrupter.type_ops(targets["TYPE"])
    serials = round(targets["DATE"] * 7 / 15) if golden.fmt == "xlsx" else 0
    corrupter.date_ops(targets["DATE"], serials)
    corrupter.cat_variants(targets["CAT_AUTO"])
    corrupter.cat_typos(targets["CAT_TYPO"])
    corrupter.outliers(targets["OUT"])

    manifest = {
        "golden": golden.name,
        "seed": seed,
        "variant": variant,
        "op_rate": op_rate,
        "n_ops": len(corrupter.ops),
        "targets": targets,
        "columns": list(golden.headers),
        "kinds": list(golden.kinds),
        "nullable_columns": list(golden.nullable),
        "column_truth": corrupter.column_truth,
        "class_shares": _realized_shares(corrupter.ops),
        "ops": [op.to_json() for op in corrupter.ops],
    }
    return corrupter, manifest


def _realized_shares(ops: list[Op]) -> dict[str, float]:
    total = len(ops) or 1
    counts: dict[str, int] = {}
    for op in ops:
        counts[op.klass] = counts.get(op.klass, 0) + 1
    return {name: round(counts.get(name, 0) / total, 6) for name in CLASS_SHARES}


# --------------------------------------------------------------------------
# Hygiene assertions (EVALS.md §4.2) — a violation raises; nothing is written.
# --------------------------------------------------------------------------


def assert_label_spacing(golden: Golden) -> None:
    """No two legitimate labels may be a single edit apart.

    A distance-1 pair inside a golden column would make the file itself
    ambiguous.  The stronger property — that a *typo* is never closer to a
    different label than to the one it came from — cannot be a vocabulary rule
    (``female``/``male`` are legitimately two edits apart), so it is enforced
    per generated typo in :meth:`Corrupter._make_typo`, which rejects any
    candidate that lands inside D10b's window around another label.
    """
    for col in golden.columns_of_kind("categorical"):
        labels = sorted({value for value in golden.column(col) if value})
        for i, left in enumerate(labels):
            for right in labels[i + 1 :]:
                distance = damerau_levenshtein(left, right)
                assert distance >= 2, (
                    f"{golden.name}:{golden.headers[col]} labels {left!r}/{right!r} "
                    f"are only {distance} edits apart"
                )


def assert_golden_is_tame(golden: Golden) -> None:
    """No golden numeric cell may trip both outlier tests (M3_clean_findings)."""
    for col, kind in enumerate(golden.kinds):
        if kind not in {"integer", "float"}:
            continue
        values = [
            float(value)
            for value in golden.column(col)
            if value is not None and _is_canonical_number(value)
        ]
        if len(values) < 20:
            continue
        stats = fences_and_mad(values)
        for value in values:
            assert not stats.trips_both(value), (
                f"{golden.name}:{golden.headers[col]} contains a golden outlier {value}"
            )


def assert_unique_rows(name: str, rows: list[list[str | None]]) -> None:
    seen: set[tuple[str | None, ...]] = set()
    for index, row in enumerate(rows):
        signature = tuple(row)
        assert signature not in seen, f"{name}: row {index} duplicates an earlier row"
        seen.add(signature)


def assert_corruption_hygiene(corrupter: Corrupter, golden: Golden) -> None:
    ops = corrupter.ops
    # 1. ops never stack
    cells = [(op.row, op.col) for op in ops if op.col is not None]
    assert len(cells) == len(set(cells)), f"{golden.name}: two ops landed on one cell"
    # 2/3. the only duplicate rows are the injected ones
    injected = {op.row for op in ops if op.op == "duplicate_row"}
    seen: dict[tuple[str | None, ...], int] = {}
    for index, row in enumerate(corrupter.rows):
        signature = tuple(row)
        first = seen.get(signature)
        if first is None:
            seen[signature] = index
            continue
        assert index in injected, f"{golden.name}: accidental duplicate at row {index}"
    assert len(injected) == len([op for op in ops if op.op == "duplicate_row"])
    # 4. no CAT edit collides with a legitimate label
    for op in ops:
        if op.klass not in {"CAT_AUTO", "CAT_TYPO"} or op.col is None:
            continue
        legit = {value for value in golden.column(op.col) if value}
        assert op.after not in legit, f"{golden.name}: CAT edit {op.after!r} collides"
    # 5. variant budget honoured: the golden spelling stays the cluster canonical
    touched: dict[tuple[int, str], int] = {}
    for op in ops:
        if op.klass in {"CAT_AUTO", "CAT_TYPO"} and op.col is not None and op.before:
            touched[(op.col, op.before)] = touched.get((op.col, op.before), 0) + 1
    for (col, label), count in touched.items():
        occurrences = sum(1 for value in golden.column(col) if value == label)
        assert count <= occurrences * CAT_VARIANT_MAX_RATIO + 1e-9, (
            f"{golden.name}: {count} variants of {label!r} exceed the dominance budget"
        )
    # 6. MISS ops only ever target golden-null cells
    for op in ops:
        if op.op == "sentinel_missing":
            assert op.col is not None and golden.rows[op.row][op.col] is None
    # 7. every op's recorded `before` equals the golden cell
    for op in ops:
        if op.col is None:
            continue
        assert op.before == golden.rows[op.row][op.col], (
            f"{golden.name}: op {op.op} at ({op.row},{op.col}) recorded a stale before-value"
        )
    # Frozen duplicate rows carry no other op.
    frozen = injected | {op.extra["source_row"] for op in ops if op.op == "duplicate_row"}
    for op in ops:
        if op.op == "duplicate_row":
            continue
        assert op.row not in frozen, f"{golden.name}: op {op.op} touched a frozen duplicate row"


def assert_categorical_still_categorical(corrupter: Corrupter, golden: Golden) -> None:
    """Corruption must not push a categorical column past FR-5's thresholds.

    Checked on the **raw** corrupted column, which is the strictest form: the
    pipeline re-profiles the working table at every one of D13's nine stages,
    and the earliest stages still see every whitespace and encoding variant as
    its own distinct value.  A column that slipped to `text` at, say, the WS
    stage would silently demote `fix.collapse_spaces` to review (D12 scores it
    0.6 in text columns) and leave un-repaired variants for CAT to re-detect —
    the eval would be scoring the fixture, not the engine.
    """
    for col in golden.columns_of_kind("categorical"):
        present = [row[col] for row in corrupter.rows if row[col] is not None]
        distinct = len(set(present))
        assert distinct <= 50, (
            f"{golden.name}:{golden.headers[col]} has {distinct} raw values — over FR-5's "
            f"categorical ceiling, so the column would type as text at an early stage"
        )
        ratio = distinct / max(len(present), 1)
        assert ratio <= 0.10, (
            f"{golden.name}:{golden.headers[col]} distinct-ratio {ratio:.3f} > 0.10"
        )


def assert_global_shares(all_ops: list[Op]) -> dict[str, float]:
    realized = _realized_shares(all_ops)
    for name, target in CLASS_SHARES.items():
        assert abs(realized[name] - target) <= SHARE_TOLERANCE, (
            f"class {name} realized share {realized[name]:.3f} is more than "
            f"{SHARE_TOLERANCE} from the pinned {target:.3f}"
        )
    return realized


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def corrupted_name(golden: Golden, variant: int) -> str:
    stem, _, extension = golden.name.rpartition(".")
    return f"{stem}.c{variant}.{extension}"


def generate(seed: int = DEFAULT_SEED, out_dir: Path | None = None) -> dict[str, Any]:
    """Write every golden, every corruption and its manifest.  Deterministic."""
    root = out_dir or HERE
    golden_dir, corrupt_dir = root / "golden", root / "corrupted"
    golden_dir.mkdir(parents=True, exist_ok=True)
    corrupt_dir.mkdir(parents=True, exist_ok=True)

    expected: dict[str, Any] = {"seed": seed, "goldens": {}, "corrupted": {}}
    all_ops: list[Op] = []

    for builder in GOLDEN_BUILDERS:
        golden = builder(random.Random(f"{seed}:{builder.__name__}"))
        assert_label_spacing(golden)
        assert_golden_is_tame(golden)
        assert_unique_rows(golden.name, golden.rows)
        write_table(golden_dir / golden.name, golden, golden.rows)
        expected["goldens"][golden.name] = {
            "rows": len(golden.rows),
            "cols": len(golden.headers),
            "nullable_columns": list(golden.nullable),
            "legit_extremes": golden.legit_extremes,
        }

        for variant in range(1, CORRUPTIONS_PER_GOLDEN[golden.name] + 1):
            corrupter, manifest = corrupt(golden, variant, seed)
            assert_corruption_hygiene(corrupter, golden)
            assert_categorical_still_categorical(corrupter, golden)
            all_ops.extend(corrupter.ops)
            name = corrupted_name(golden, variant)
            manifest["file"] = name
            write_table(corrupt_dir / name, golden, corrupter.rows)
            stem = name.rpartition(".")[0]
            (corrupt_dir / f"{stem}.manifest.json").write_text(
                json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            expected["corrupted"][name] = {
                "golden": golden.name,
                "op_rate": manifest["op_rate"],
                "n_ops": manifest["n_ops"],
                "class_shares": manifest["class_shares"],
            }

    expected["totals"] = {
        "n_ops": len(all_ops),
        "class_shares": assert_global_shares(all_ops),
        "class_counts": {
            name: sum(1 for op in all_ops if op.klass == name) for name in CLASS_SHARES
        },
    }
    (root / "expected.json").write_text(
        json.dumps(expected, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the datasweep eval fixtures.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    summary = generate(args.seed)
    print(f"seed={args.seed}  ops={summary['totals']['n_ops']}")
    for name, share in sorted(summary["totals"]["class_shares"].items()):
        print(f"  {name:<9} {share:.3f}  (target {CLASS_SHARES[name]:.2f})")


if __name__ == "__main__":
    main()
