"""Content-derived identifiers and canonical JSON (FR-14, DATA_MODEL "Id scheme").

All primary ids are ``hex16(x) = sha256(x)[:16]`` over a canonical natural-key
string whose fields are joined with ``|``. Money entering an id key is
serialised as integer cents so no float repr ever reaches a hash and replays are
byte-identical across platforms. Absent optional fields serialise as ``""``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .weeks import week_key

__all__ = [
    "advice_id",
    "backtest_run_id",
    "canonical_json",
    "cents",
    "compute_inputs_hash",
    "event_id",
    "garment_id",
    "hex16",
    "listing_id",
]


def hex16(text: str) -> str:
    """The first 16 hex characters of the SHA-256 of ``text`` (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def canonical_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, compact separators, stable across runs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cents(usd: float) -> int:
    """Serialise a USD amount as integer cents (no float repr enters a hash)."""
    return round(usd * 100)


def listing_id(source: str, external_id: str) -> str:
    return hex16(f"listing|{source}|{external_id}")


def event_id(
    event_type: str,
    brand_id: str,
    era_id: str | None,
    occurred_on: str,
    identity_attrs: Mapping[str, Any],
) -> str:
    """Event identity (FR-5): factual discriminators only, never judgement attrs."""
    return hex16(
        "event|"
        + event_type
        + "|"
        + brand_id
        + "|"
        + (era_id or "")
        + "|"
        + week_key(occurred_on)
        + "|"
        + canonical_json(dict(identity_attrs))
    )


def garment_id(stratum_path: str, anchor_date: str, anchor_price: float, added_at: str) -> str:
    """Garment identity: no editable field is in the key (REVIEW D10)."""
    return hex16(f"garment|{stratum_path}|{anchor_date}|{cents(anchor_price)}|{added_at}")


def advice_id(garment: str, as_of_week: str, inputs_hash: str) -> str:
    return hex16(f"advice|{garment}|{as_of_week}|{inputs_hash}")


def backtest_run_id(params: Mapping[str, Any], as_of: str) -> str:
    return hex16("bt|" + canonical_json(dict(params)) + "|" + as_of)


def compute_inputs_hash(
    events: Iterable[tuple[str, str, int]],
    stratum_id: str,
    index_built_as_of: str,
    config_version: str,
) -> str:
    """Hash the exact advice inputs (FR-8 identity / supersession).

    ``events`` is an iterable of ``(event_id, event_week, corroboration)`` for the
    active confirmed events; it is sorted by event id before hashing.
    """
    rows = sorted(([eid, week, int(corr)] for eid, week, corr in events), key=lambda r: r[0])
    return hex16(
        canonical_json(
            {
                "events": rows,
                "stratum": stratum_id,
                "index_built_as_of": index_built_as_of,
                "config_version": config_version,
            }
        )
    )
