"""Metric computation for Orbit evals."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MetricResult:
    name: str
    value: float
    gate: float
    passed: bool
    detail: str = ""


@dataclass
class EvalReport:
    metrics: list[MetricResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(m.passed for m in self.metrics)

    def to_dict(self) -> dict:
        return {
            "passed": self.all_passed,
            "metrics": [
                {
                    "name": m.name,
                    "value": m.value,
                    "gate": m.gate,
                    "passed": m.passed,
                    "detail": m.detail,
                }
                for m in self.metrics
            ],
        }


def precision_recall(predicted: set[str], golden: set[str]) -> tuple[float, float, float]:
    if not predicted and not golden:
        return 1.0, 1.0, 1.0
    if not predicted or not golden:
        return 0.0, 0.0, 0.0
    tp = len(predicted & golden)
    precision = tp / len(predicted)
    recall = tp / len(golden)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def normalize_value(v: str) -> str:
    return " ".join(v.lower().strip().split())


def proposal_key(category: str, value: str) -> str:
    return f"{category}:{normalize_value(value)}"
