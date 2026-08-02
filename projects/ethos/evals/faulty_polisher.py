"""The eval-only adversarial `ProsePolisher` behind M4 (EVALS § M4).

Mutations are **structured region operations** over the parsed envelope, never
committed byte offsets: a byte offset silently becomes a no-op (or drifts into
another region) after a `composer_version` bump, and a no-op mutation would be
scored as a verifier success. Each case names regions and substrings, so a stale
case fails loudly as a fixture bug — the harness asserts every mutated render
differs from its clean render.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SENTINEL_RE = re.compile(r"\[\[(/?)([^\]]+)\]\]")


class MutationError(RuntimeError):
    """A case named a region or substring the envelope does not contain."""


@dataclass(frozen=True)
class Region:
    tag: str
    payload: str
    start: int
    end: int


def regions(envelope: str) -> list[Region]:
    """Every `[[tag]]…[[/tag]]` span, pairing each open sentinel with the first
    matching close (so perspective blocks do not swallow the regions inside them)."""
    opens: list[tuple[str, int, int]] = []
    closes: dict[str, list[tuple[int, int]]] = {}
    for match in SENTINEL_RE.finditer(envelope):
        slash, tag = match.group(1), match.group(2)
        if slash:
            closes.setdefault(tag, []).append((match.start(), match.end()))
        else:
            opens.append((tag, match.start(), match.end()))
    out: list[Region] = []
    for tag, start, payload_start in opens:
        candidates = [pair for pair in closes.get(tag, []) if pair[0] >= payload_start]
        if not candidates:
            continue
        close_start, close_end = candidates[0]
        out.append(Region(tag, envelope[payload_start:close_start], start, close_end))
    return sorted(out, key=lambda region: region.start)


def _find(envelope: str, tag: str) -> Region:
    """Exact tag, else the first region whose tag extends it (`M:summary` matches
    `M:summary:<tradition>`), else a suffix match."""
    found = regions(envelope)
    for region in found:
        if region.tag == tag:
            return region
    for region in found:
        if region.tag.startswith(f"{tag}:") or region.tag.endswith(f":{tag}"):
            return region
    raise MutationError(f"no region matching {tag!r}")


def _rewrite(envelope: str, region: Region, payload: str) -> str:
    body = f"[[{region.tag}]]{payload}[[/{region.tag}]]"
    return envelope[: region.start] + body + envelope[region.end :]


def apply_op(envelope: str, op: dict[str, Any]) -> str:
    """Apply one structured region operation and return the new envelope."""
    kind = op["op"]
    if kind == "replace_in_region":
        region = _find(envelope, op["region"])
        if op["find"] not in region.payload:
            raise MutationError(f"{op['region']}: {op['find']!r} not present")
        return _rewrite(envelope, region, region.payload.replace(op["find"], op["replace"], 1))
    if kind == "replace_region":
        region = _find(envelope, op["region"])
        return _rewrite(envelope, region, op["replace"])
    if kind == "append_to_region":
        region = _find(envelope, op["region"])
        return _rewrite(envelope, region, region.payload + op["replace"])
    if kind == "delete_region":
        region = _find(envelope, op["region"])
        tail = envelope[region.end :]
        if tail.startswith("\n"):
            tail = tail[1:]
        return envelope[: region.start] + tail
    if kind == "duplicate_region":
        region = _find(envelope, op["region"])
        body = f"[[{region.tag}]]{region.payload}[[/{region.tag}]]"
        return envelope[: region.end] + "\n" + body + envelope[region.end :]
    if kind == "swap_region_payloads":
        first = _find(envelope, op["region"])
        second = _find(envelope, op["other"])
        if first.start > second.start:
            first, second = second, first
        head = envelope[: first.start]
        middle = envelope[first.end : second.start]
        tail = envelope[second.end :]
        first_body = f"[[{first.tag}]]{second.payload}[[/{first.tag}]]"
        second_body = f"[[{second.tag}]]{first.payload}[[/{second.tag}]]"
        return head + first_body + middle + second_body + tail
    if kind == "move_perspective_last":
        marker = f"[[P:{op['region']}]]"
        closer = f"[[/P:{op['region']}]]"
        start = envelope.index(marker)
        end = envelope.index(closer) + len(closer) + 1
        block = envelope[start:end]
        rest = envelope[:start] + envelope[end:]
        anchor = rest.index("[[I:agreement]]")
        return rest[:anchor] + block + rest[anchor:]
    if kind == "drop_close_sentinel":
        region = _find(envelope, op["region"])
        return envelope.replace(f"[[/{region.tag}]]", "", 1)
    if kind == "insert_after_region":
        region = _find(envelope, op["region"])
        return envelope[: region.end] + "\n" + op["replace"] + envelope[region.end :]
    raise MutationError(f"unknown op {kind!r}")


class FaultyPolisher:
    """Applies one scripted case's operations to the envelope it is handed."""

    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case
        self.calls = 0

    @property
    def id(self) -> str:
        return str(self.case["id"])

    @property
    def mode(self) -> str:
        return str(self.case["mode"])

    @property
    def clean(self) -> bool:
        return self.mode == "clean"

    def polish(self, envelope: str) -> str:
        self.calls += 1
        out = envelope
        for op in self.case.get("ops", []):
            out = apply_op(out, op)
        return out


__all__ = ["FaultyPolisher", "MutationError", "apply_op", "regions"]
