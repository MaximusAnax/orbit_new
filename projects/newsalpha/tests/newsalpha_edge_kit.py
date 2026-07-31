"""Shared helpers for the API and CLI tests: a tiny corpus and tiny price series.

The committed eval corpus is 223 articles and takes seconds to process; the edge
tests only need a handful of events, so they build their own miniature fixture
files in `tmp_path` and point the same offline adapters at them.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

AS_OF = "2026-03-20T07:00:00Z"
CORPUS_START = date(2026, 3, 1)

_HACK_HEAD = (
    "The Solana bridge was exploited for $12.00 million in the early hours of {weekday}, "
    "researchers at Kestrel Analytics said. On-chain traces put the outflow across nine "
    "addresses, with the first transfer clearing shortly after midnight local time. "
    "Deposits were paused within the hour and the validator set rotated its signing keys "
    "before the market reopened. A first account published by the team put the affected "
    "balances at roughly four thousand accounts."
)

#: (external_id, domain, published_at, title, body)
ARTICLES: tuple[tuple[str, str, str, str, str], ...] = (
    # The two hack articles share their headline and the first 400 characters of
    # their body apart from one weekday token, so FR-2 clusters them (Jaccard well
    # above 0.60) while their diverging tails keep the content hashes distinct.
    (
        "hack-1",
        "coindesk.example",
        "2026-03-16T08:00:00Z",
        "An incident report lands on a network",
        _HACK_HEAD.format(weekday="Monday") + " The team said a fuller write-up would follow.",
    ),
    (
        "hack-2",
        "theblock.example",
        "2026-03-16T13:30:00Z",
        "An incident report lands on a network",
        _HACK_HEAD.format(weekday="Tuesday") + " The wire carried an updated version later.",
    ),
    (
        "earn-1",
        "reuters.example",
        "2026-03-17T09:00:00Z",
        "A firmer quarterly scorecard from a software vendor",
        "Microsoft Corporation (MSFT) beat estimates for the third quarter by 4.2 %, the company "
        "said on Tuesday in a statement issued from Redmond. Management pointed to firmer pricing "
        "in two reporting segments and a modest improvement in gross margin.",
    ),
    (
        "mna-1",
        "businesswire.example",
        "2026-03-18T11:00:00Z",
        "Two software companies outline a transaction",
        "Adobe agreed to acquire Okta in a transaction valued at $4.00 billion, the two boards "
        "said on Wednesday. Completion is targeted for the fourth quarter and is subject to the "
        "customary antitrust review, which usually runs nine months.",
    ),
    (
        "quiet-1",
        "marketchatter.example",
        "2026-03-19T10:00:00Z",
        "A quiet tape through the afternoon session",
        "Volumes drifted lower through the afternoon, with two-way interest thin across the "
        "board. Kestrel Analytics said desks had been positioning rather than reacting to "
        "anything specific. No single sector led the tape.",
    ),
)

ASSETS: tuple[tuple[str, str], ...] = (
    ("cx:SOL", "crypto"),
    ("eq:MSFT", "equity"),
    ("eq:ADBE", "equity"),
    ("eq:OKTA", "equity"),
    ("idx:US", "equity"),
    ("idx:CX", "crypto"),
)


def write_corpus(directory: Path) -> Path:
    """Write the miniature JSONL corpus and return its path."""
    path = directory / "articles.jsonl"
    rows = [
        {
            "external_id": external_id,
            "url": f"https://{domain}/{external_id}",
            "source_domain": domain,
            "published_at": published_at,
            "fetched_at": published_at,
            "title": title,
            "body": body,
        }
        for external_id, domain, published_at, title, body in ARTICLES
    ]
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )
    return path


#: A denial of the corpus's M&A story, five days later and in its own cluster, so
#: FR-15's cross-cluster supersession has something to demote (2 < 5 <= 21 days).
DENIAL = {
    "external_id": "mna-denied-1",
    "url": "https://reuters.example/mna-denied-1",
    "source_domain": "reuters.example",
    "published_at": "2026-03-23T09:00:00Z",
    "fetched_at": "2026-03-23T09:00:00Z",
    "title": "One software company pushes back on the reported terms",
    "body": (
        "Adobe denied reports that it is in talks to acquire Okta, calling the weekend story "
        "inaccurate in a statement issued on Sunday. The company said no approach had been made "
        "and that it would not comment further on speculation of this kind."
    ),
}


def write_denial(directory: Path) -> Path:
    """Write a one-article corpus that denies the main corpus's M&A story."""
    path = directory / "denial.jsonl"
    path.write_text(json.dumps(DENIAL, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_market(directory: Path, *, days: int = 45, drift: float = -0.004) -> Path:
    """Write tiny per-asset CSV series the fixture market adapter can read."""
    market = directory / "market"
    market.mkdir(parents=True, exist_ok=True)
    for asset_id, kind in ASSETS:
        level = 100.0
        lines = ["date,open,high,low,close,volume"]
        current = CORPUS_START
        step = 0.0 if asset_id.startswith("idx:") else drift
        for _ in range(days):
            if kind == "equity" and current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            open_level = level
            level = open_level * math.exp(step)
            lines.append(
                f"{current.isoformat()},{open_level:.4f},{max(open_level, level):.4f},"
                f"{min(open_level, level):.4f},{level:.4f},1000"
            )
            current += timedelta(days=1)
        (market / f"{asset_id.replace(':', '_')}.csv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return market


__all__ = [
    "ARTICLES",
    "ASSETS",
    "AS_OF",
    "DENIAL",
    "write_corpus",
    "write_denial",
    "write_market",
]
