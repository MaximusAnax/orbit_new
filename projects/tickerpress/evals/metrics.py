"""Metric implementations for the TickerPress eval suite (EVALS §3, §5, §6).

Every gated metric reads its truth from a committed file under
``evals/fixtures/labels/``; nothing here derives truth from the system under
test. The naive baseline at the bottom is the honest afternoon script the gates
are measured against — whole-word alias matching, dedup by identical canonical
URL, relevance = mention count — implemented live so ``run.py`` prints numbers
rather than remembered ones.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

TIER_ORDER = {"passing": 1, "context": 2, "primary": 3}

__all__ = [
    "EvalReport",
    "Fixtures",
    "MetricResult",
    "PairPrediction",
    "clustering_scores",
    "f1_scores",
    "lexicon_document_frequency",
    "load_fixtures",
    "naive_appearances",
    "naive_story_of",
    "relevance_ordering",
    "u_amb_pairs",
]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixtures:
    """Every committed truth artifact, loaded once."""

    root: Path
    watchlist: dict
    corpus: dict
    labels: list[dict]
    weak_surfaces: dict[str, list[str]]
    story_groups: dict[int, int]
    base_of: dict[int, int]
    base_article_ids: dict[int, int]
    no_link: list[dict]
    expected_digest: dict
    lexicon_manifest: dict

    @property
    def tickers(self) -> list[str]:
        return [company["ticker"] for company in self.watchlist["companies"]]

    @property
    def companies(self) -> dict[str, dict]:
        return {company["ticker"]: company for company in self.watchlist["companies"]}

    @property
    def label_index(self) -> dict[tuple[int, str], dict]:
        return {(row["base_id"], row["ticker"]): row for row in self.labels}

    def truth(self, article_id: int, ticker: str) -> str:
        """``absent`` | ``passing`` | ``context`` | ``primary`` for one pair."""

        row = self.label_index.get((self.base_of[article_id], ticker))
        if row is None or not row.get("present"):
            return "absent"
        return str(row["tier"])

    def label_of(self, article_id: int, ticker: str) -> dict | None:
        return self.label_index.get((self.base_of[article_id], ticker))


def load_fixtures(root: Path = FIXTURES) -> Fixtures:
    def read(*parts: str) -> dict:
        return json.loads(root.joinpath(*parts).read_text(encoding="utf-8"))

    groups = read("labels", "story_groups.json")
    return Fixtures(
        root=root,
        watchlist=read("watchlist.json"),
        corpus=read("corpus", "articles.json"),
        labels=read("labels", "mentions.json")["labels"],
        weak_surfaces=read("labels", "weak_surfaces.json")["weak_surfaces"],
        story_groups={int(k): v for k, v in groups["groups"].items()},
        base_of={int(k): v for k, v in groups["base_of"].items()},
        base_article_ids={int(k): v for k, v in groups["base_article_ids"].items()},
        no_link=read("labels", "no_link.json")["pairs"],
        expected_digest=read("labels", "expected_digest.json"),
        lexicon_manifest=read("lexicon_manifest.json"),
    )


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: float
    gate: float
    passed: bool
    detail: str = ""
    comparison: str = ">="

    @classmethod
    def at_least(cls, name: str, value: float, gate: float, detail: str = "") -> MetricResult:
        return cls(name, value, gate, value >= gate - 1e-9, detail)

    @classmethod
    def at_most(cls, name: str, value: float, gate: float, detail: str = "") -> MetricResult:
        return cls(name, value, gate, value <= gate + 1e-9, detail, comparison="<=")

    def line(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"  {self.name:<26} {self.value:>8.3f}  gate {self.comparison} {self.gate:<6.2f} "
            f"[{status}]  {self.detail}"
        )


@dataclass
class EvalReport:
    metrics: list[MetricResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extras: dict[str, object] = field(default_factory=dict)

    def add(self, metric: MetricResult) -> MetricResult:
        self.metrics.append(metric)
        return metric

    @property
    def passed(self) -> bool:
        return all(metric.passed for metric in self.metrics)

    def by_name(self, name: str) -> MetricResult:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(name)

    def as_json(self) -> dict:
        return {
            "passed": self.passed,
            "metrics": [
                {
                    "name": metric.name,
                    "value": round(metric.value, 6),
                    "gate": metric.gate,
                    "comparison": metric.comparison,
                    "passed": metric.passed,
                }
                for metric in self.metrics
            ],
            **{key: value for key, value in self.extras.items()},
        }


# ---------------------------------------------------------------------------
# pair-level detection metrics (M1 family)
# ---------------------------------------------------------------------------


def f1_scores(
    universe: Iterable[tuple[int, str]],
    truth: Mapping[tuple[int, str], str],
    predicted: Iterable[tuple[int, str]],
) -> tuple[float, float, float, int, int, int]:
    """Precision, recall, F1 and the raw TP/FP/FN counts over ``universe``."""

    pairs = set(universe)
    positives = {pair for pair in pairs if truth.get(pair, "absent") != "absent"}
    predicted_set = set(predicted) & pairs
    tp = len(positives & predicted_set)
    fp = len(predicted_set - positives)
    fn = len(positives - predicted_set)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1, tp, fp, fn


def _whole_token_pattern(surface: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(surface)}(?![A-Za-z0-9_])")


def u_amb_pairs(
    texts: Mapping[int, str], weak_surfaces: Mapping[str, Sequence[str]]
) -> set[tuple[int, str]]:
    """The ambiguous subset (EVALS §3, M1_amb).

    Membership is decided by a plain, case-sensitive whole-token scan for the
    surfaces in the **frozen** ``labels/weak_surfaces.json`` inventory — never
    from ``data/common_words.txt``, so editing engine data cannot move a trap
    out of the hard subset.
    """

    patterns = {
        ticker: [_whole_token_pattern(surface) for surface in surfaces]
        for ticker, surfaces in weak_surfaces.items()
    }
    subset: set[tuple[int, str]] = set()
    for article_id, text in texts.items():
        for ticker, compiled in patterns.items():
            if any(pattern.search(text) for pattern in compiled):
                subset.add((article_id, ticker))
    return subset


# ---------------------------------------------------------------------------
# clustering metrics (M2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairPrediction:
    precision: float
    recall: float
    f1: float
    predicted: int
    true_pairs: int
    correct: int
    over_merged: list[tuple[int, int]]
    missed: list[tuple[int, int]]


def clustering_scores(
    article_ids: Sequence[int],
    true_group: Mapping[int, int],
    predicted_story: Mapping[int, int],
) -> PairPrediction:
    """Pair-counting (pairwise link) precision/recall — not B-CUBED (EVALS §3).

    ``precision`` is defined as 0.0 when nothing is clustered, so the
    never-cluster degenerate fails on precision as well as recall.
    """

    correct = predicted = true_pairs = 0
    over_merged: list[tuple[int, int]] = []
    missed: list[tuple[int, int]] = []
    for left, right in itertools.combinations(sorted(article_ids), 2):
        same_true = true_group[left] == true_group[right]
        same_pred = predicted_story[left] == predicted_story[right]
        true_pairs += int(same_true)
        predicted += int(same_pred)
        if same_pred and same_true:
            correct += 1
        elif same_pred:
            over_merged.append((left, right))
        elif same_true:
            missed.append((left, right))
    precision = correct / predicted if predicted else 0.0
    recall = correct / true_pairs if true_pairs else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return PairPrediction(
        precision=precision,
        recall=recall,
        f1=f1,
        predicted=predicted,
        true_pairs=true_pairs,
        correct=correct,
        over_merged=over_merged,
        missed=missed,
    )


# ---------------------------------------------------------------------------
# ranking metric (M3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderingScore:
    value: float
    pairs: int
    correct: float
    trap_groups_present: set[int]
    inversions: list[tuple[tuple[int, str], tuple[int, str], int, int]]


def relevance_ordering(
    fixtures: Fixtures,
    predicted: Mapping[tuple[int, str], int],
) -> OrderingScore:
    """Tier-separation accuracy over predicted appearances (EVALS §3, M3).

    ``O`` holds every same-company pair whose hand-assigned tiers differ and
    whose appearances the system actually predicted; ties score half credit.
    """

    by_ticker: dict[str, list[tuple[int, str]]] = {}
    for pair in predicted:
        by_ticker.setdefault(pair[1], []).append(pair)

    total = 0
    credit = 0.0
    traps: set[int] = set()
    inversions: list[tuple[tuple[int, str], tuple[int, str], int, int]] = []
    for ticker, pairs in sorted(by_ticker.items()):
        for left, right in itertools.combinations(sorted(pairs), 2):
            tier_left = TIER_ORDER.get(fixtures.truth(left[0], ticker), 0)
            tier_right = TIER_ORDER.get(fixtures.truth(right[0], ticker), 0)
            if tier_left == tier_right or 0 in (tier_left, tier_right):
                continue
            high, low = (left, right) if tier_left > tier_right else (right, left)
            total += 1
            if predicted[high] > predicted[low]:
                credit += 1.0
            elif predicted[high] == predicted[low]:
                credit += 0.5
                inversions.append((high, low, predicted[high], predicted[low]))
            else:
                inversions.append((high, low, predicted[high], predicted[low]))

            label_high = fixtures.label_of(high[0], ticker) or {}
            label_low = fixtures.label_of(low[0], ticker) or {}
            group = label_high.get("ordering_trap_group")
            if group is not None and group == label_low.get("ordering_trap_group"):
                traps.add(int(group))
    return OrderingScore(
        value=credit / total if total else 0.0,
        pairs=total,
        correct=credit,
        trap_groups_present=traps,
        inversions=inversions,
    )


def designed_trap_groups(fixtures: Fixtures) -> set[int]:
    """The ordering-trap groups the corpus declares (EVALS §3, M3_cov)."""

    groups: dict[int, set[str]] = {}
    for row in fixtures.labels:
        group = row.get("ordering_trap_group")
        if group is None:
            continue
        groups.setdefault(int(group), set()).add(str(row["tier"]))
    return {group for group, tiers in groups.items() if len(tiers) > 1}


# ---------------------------------------------------------------------------
# lexicon specificity (M6_lex)
# ---------------------------------------------------------------------------


def lexicon_document_frequency(
    entries: Iterable[str], base_texts: Mapping[int, str]
) -> dict[str, int]:
    """How many base articles contain each lexicon entry (whole-token, folded)."""

    tokenized = {
        article_id: re.findall(r"[a-z0-9]+", text.casefold())
        for article_id, text in base_texts.items()
    }
    frequency: dict[str, int] = {}
    for entry in entries:
        needle = re.findall(r"[a-z0-9]+", entry.casefold())
        if not needle:
            continue
        count = 0
        width = len(needle)
        for tokens in tokenized.values():
            if any(tokens[i : i + width] == needle for i in range(len(tokens) - width + 1)):
                count += 1
        frequency[entry] = count
    return frequency


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# the naive baseline (EVALS §5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NaiveAlias:
    ticker: str
    surface: str
    case_sensitive: bool


def naive_alias_table(fixtures: Fixtures) -> list[NaiveAlias]:
    """The afternoon script's alias table: names loose, tickers exact-case."""

    table: list[NaiveAlias] = []
    for company in fixtures.watchlist["companies"]:
        ticker = company["ticker"]
        name = company["name"]
        table.append(NaiveAlias(ticker, ticker, True))
        table.append(NaiveAlias(ticker, f"${ticker}", True))
        table.append(NaiveAlias(ticker, name, False))
        parts = name.replace(",", " ").split()
        while parts and parts[-1].strip(".").lower() in {
            "inc",
            "corp",
            "corporation",
            "co",
            "ltd",
            "plc",
            "company",
            "holdings",
            "group",
        }:
            parts.pop()
        short = " ".join(parts)
        if short and short != name:
            table.append(NaiveAlias(ticker, short, False))
        for alias in company.get("aliases", []):
            table.append(NaiveAlias(ticker, alias["text"], False))
    return table


def naive_appearances(
    fixtures: Fixtures, texts: Mapping[int, str]
) -> dict[tuple[int, str], int]:
    """Whole-word alias matching; the value is the naive relevance (hit count)."""

    by_ticker: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for alias in naive_alias_table(fixtures):
        by_ticker.setdefault(alias.ticker, []).append(
            (
                alias.surface,
                re.compile(
                    rf"(?<![A-Za-z0-9_]){re.escape(alias.surface)}(?![A-Za-z0-9_])",
                    0 if alias.case_sensitive else re.IGNORECASE,
                ),
            )
        )
    counts: dict[tuple[int, str], int] = {}
    for article_id, text in texts.items():
        for ticker, aliases in by_ticker.items():
            # longest surface first, non-overlapping: "Apple Inc." is one hit,
            # not two, so the baseline really is "accepted mention count".
            taken: list[tuple[int, int]] = []
            for _, pattern in sorted(aliases, key=lambda item: -len(item[0])):
                for match in pattern.finditer(text):
                    span = match.span()
                    if any(span[0] < end and start < span[1] for start, end in taken):
                        continue
                    taken.append(span)
            if taken:
                counts[(article_id, ticker)] = len(taken)
    return counts


def naive_story_of(canonical_urls: Mapping[int, str]) -> dict[int, int]:
    """Dedup by identical canonical URL — the naive clusterer's whole idea."""

    story_of: dict[int, int] = {}
    seen: dict[str, int] = {}
    for article_id in sorted(canonical_urls):
        url = canonical_urls[article_id]
        story_of[article_id] = seen.setdefault(url, article_id)
    return story_of


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)
