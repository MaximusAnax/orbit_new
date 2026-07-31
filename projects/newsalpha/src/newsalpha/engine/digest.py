"""FR-8: the morning digest -- window filter, supersession filter, ranking.

`digest(date, watchlist_only=True, include_superseded=False)` returns the latest
revision of each signal whose event date falls in `(date - 5 days, date]`,
filtered to watchlist assets by default, excluding superseded signals unless
asked, ranked by `|score|` desc, then confidence desc, then asset id asc.
An empty news day renders an explicit "no notable events" state, never an error.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta

from ..datasets import Datasets
from ..models import Digest, DigestEntry, Signal
from .brief import one_line_summary
from .revise import latest_revisions

DIGEST_WINDOW_DAYS = 5
EMPTY_STATE = "No notable events in the last 5 days for the selected assets."


def in_window(event_date: str, as_of: str, window_days: int = DIGEST_WINDOW_DAYS) -> bool:
    """`event_date in (as_of - window_days, as_of]` -- exclusive lower bound (FR-8)."""
    day = date_cls.fromisoformat(event_date)
    end = date_cls.fromisoformat(as_of)
    return end - timedelta(days=window_days) < day <= end


def build_digest(
    signals: list[Signal],
    datasets: Datasets,
    *,
    as_of_date: str,
    watchlist: set[str] | None = None,
    watchlist_only: bool = True,
    include_superseded: bool = False,
) -> Digest:
    """Rank the digest exactly as FR-8 documents it."""
    latest = latest_revisions(signals)
    by_key = {signal.signal_key: signal for signal in latest}

    superseded: dict[str, Signal] = {}
    for signal in latest:
        if signal.supersedes_key is not None and signal.supersedes_key in by_key:
            superseded[signal.supersedes_key] = signal

    rows: list[DigestEntry] = []
    for signal in latest:
        if not in_window(signal.event_snapshot.event_date, as_of_date):
            continue
        if watchlist_only and (watchlist is None or signal.asset_id not in watchlist):
            continue
        if signal.signal_key in superseded and not include_superseded:
            continue
        note = None
        if signal.supersedes_key is not None and signal.supersedes_key in by_key:
            older = by_key[signal.supersedes_key]
            note = (
                f"supersedes an earlier {older.event_snapshot.stage.value} signal from "
                f"{older.event_snapshot.event_date}"
            )
        asset = datasets.assets.get(signal.asset_id)
        rows.append(
            DigestEntry(
                signal_id=signal.id,
                signal_key=signal.signal_key,
                asset_id=signal.asset_id,
                asset_name=asset.name if asset is not None else signal.asset_id,
                event_type=signal.event_snapshot.event_type,
                stage=signal.event_snapshot.stage,
                direction=signal.direction,
                magnitude=signal.magnitude,
                confidence=signal.confidence,
                horizon_bars=signal.horizon_bars,
                score=signal.score,
                event_date=signal.event_snapshot.event_date,
                summary=one_line_summary(signal, datasets),
                supersession_note=note,
            )
        )

    rows.sort(key=lambda row: (-abs(row.score), -row.confidence, row.asset_id))
    return Digest(
        date=as_of_date,
        watchlist_only=watchlist_only,
        include_superseded=include_superseded,
        entries=tuple(rows),
        empty_state=None if rows else EMPTY_STATE,
    )


__all__ = ["DIGEST_WINDOW_DAYS", "EMPTY_STATE", "build_digest", "in_window"]
