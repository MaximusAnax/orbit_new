"""FR-1 watch configuration, scan order, ignore patterns and settle deferral."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from datasweep.adapters.watcher import (
    IGNORE_PATTERNS,
    FileObservation,
    PollingScanner,
    is_ignored,
)
from datasweep.engine.models import WatchedFolder

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def folder(path: Path, **kwargs) -> WatchedFolder:
    payload = {
        "id": "f1",
        "path": str(path),
        "output_dir": str(path / ".datasweep"),
        "created_at": NOW,
    }
    payload.update(kwargs)
    return WatchedFolder(**payload)


def write(path: Path, text: str = "a,b\n1,2\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_fr1_scan_is_lexicographic_and_deterministic(tmp_path: Path) -> None:
    for name in ["c.csv", "a.csv", "b.csv"]:
        write(tmp_path / name)
    observations = PollingScanner().scan([folder(tmp_path)], NOW)
    assert [Path(item.path).name for item in observations] == ["a.csv", "b.csv", "c.csv"]
    assert all(item.observed_at == NOW for item in observations)


def test_fr1_include_globs_filter_candidates(tmp_path: Path) -> None:
    write(tmp_path / "keep.csv")
    write(tmp_path / "skip.txt")
    write(tmp_path / "keep.jsonl", '{"a": 1}\n')
    observations = PollingScanner().scan([folder(tmp_path)], NOW)
    assert [Path(item.path).name for item in observations] == ["keep.csv", "keep.jsonl"]


def test_fr1_custom_include_globs(tmp_path: Path) -> None:
    write(tmp_path / "a.csv")
    write(tmp_path / "b.tsv")
    observations = PollingScanner().scan([folder(tmp_path, include=["*.tsv"])], NOW)
    assert [Path(item.path).name for item in observations] == ["b.tsv"]


@pytest.mark.parametrize("name", ["~$book.csv", "export.partial", "export.tmp", ".hidden.csv"])
def test_fr1_temp_and_hidden_patterns_are_ignored(name: str, tmp_path: Path) -> None:
    assert is_ignored(name)
    write(tmp_path / name)
    write(tmp_path / "real.csv")
    observations = PollingScanner().scan([folder(tmp_path)], NOW)
    assert [Path(item.path).name for item in observations] == ["real.csv"]


def test_fr1_ignore_pattern_set_is_the_documented_one() -> None:
    assert set(IGNORE_PATTERNS) == {"~$*", "*.partial", "*.tmp", ".*"}


def test_fr1_output_dir_is_never_scanned(tmp_path: Path) -> None:
    write(tmp_path / "real.csv")
    write(tmp_path / "out" / "cleaned.csv")
    watched = folder(tmp_path, output_dir=str(tmp_path / "out"))
    observations = PollingScanner().scan([watched], NOW)
    assert [Path(item.path).name for item in observations] == ["real.csv"]


def test_fr1_recursive_flag(tmp_path: Path) -> None:
    write(tmp_path / "top.csv")
    write(tmp_path / "sub" / "nested.csv")
    recursive = PollingScanner().scan([folder(tmp_path)], NOW)
    assert len(recursive) == 2
    flat = PollingScanner().scan([folder(tmp_path, recursive=False)], NOW)
    assert [Path(item.path).name for item in flat] == ["top.csv"]


def test_fr1_disabled_folder_is_skipped(tmp_path: Path) -> None:
    write(tmp_path / "a.csv")
    assert PollingScanner().scan([folder(tmp_path, enabled=False)], NOW) == []


def test_fr1_missing_folder_does_not_raise(tmp_path: Path) -> None:
    assert PollingScanner().scan([folder(tmp_path / "nope")], NOW) == []


# --------------------------------------------------------------------------
# settle check — time is injected, never read
# --------------------------------------------------------------------------


def observation(path: str, size: int, mtime: float, at: datetime) -> FileObservation:
    return FileObservation(path=path, size=size, mtime=mtime, observed_at=at)


def test_fr1_stable_when_size_and_mtime_hold_across_the_settle_window() -> None:
    first = observation("/x/a.csv", 100, 1000.0, NOW)
    later = observation("/x/a.csv", 100, 1000.0, NOW + timedelta(seconds=5))
    assert PollingScanner.is_stable(first, later, 5) is True


def test_fr1_unstable_when_the_file_is_still_being_written() -> None:
    first = observation("/x/a.csv", 100, 1000.0, NOW)
    grown = observation("/x/a.csv", 250, 1001.0, NOW + timedelta(seconds=30))
    assert PollingScanner.is_stable(first, grown, 5) is False


def test_fr1_unstable_before_the_settle_window_elapses() -> None:
    first = observation("/x/a.csv", 100, 1000.0, NOW)
    soon = observation("/x/a.csv", 100, 1000.0, NOW + timedelta(seconds=2))
    assert PollingScanner.is_stable(first, soon, 5) is False


def test_fr1_a_first_sighting_is_never_stable() -> None:
    assert PollingScanner.is_stable(None, observation("/x/a.csv", 1, 1.0, NOW), 0) is False


def test_fr1_partition_splits_ready_from_deferred() -> None:
    previous = {
        "/x/a.csv": observation("/x/a.csv", 10, 1.0, NOW),
        "/x/b.csv": observation("/x/b.csv", 10, 1.0, NOW),
    }
    current = [
        observation("/x/a.csv", 10, 1.0, NOW + timedelta(seconds=5)),
        observation("/x/b.csv", 99, 2.0, NOW + timedelta(seconds=5)),
        observation("/x/c.csv", 10, 1.0, NOW + timedelta(seconds=5)),
    ]
    ready, deferred = PollingScanner.partition(previous, current, 5)
    assert [Path(item.path).name for item in ready] == ["a.csv"]
    assert [Path(item.path).name for item in deferred] == ["b.csv", "c.csv"]


def test_fr1_observations_record_size_and_mtime(tmp_path: Path) -> None:
    path = write(tmp_path / "a.csv", "hello\n")
    observation_ = PollingScanner().scan([folder(tmp_path)], NOW)[0]
    assert observation_.size == len("hello\n")
    assert observation_.mtime == pytest.approx(os.stat(path).st_mtime)
