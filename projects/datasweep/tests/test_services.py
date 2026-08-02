"""Service orchestration: idempotence, settling, policy, artifacts (FR-1/2/12).

The service is the only layer that touches both I/O and the engine, so these
are the tests that pin the rules the API and CLI inherit rather than restate.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from datasweep.adapters.watcher import FileObservation
from datasweep.engine.models import Policy, Run, RunStatus, TriggerKind
from datasweep.errors import DatasweepError, UnsupportedFormatError
from datasweep.services import DatasweepService
from support_datasweep import ENGINE_VERSION, FIXED_NOW, SAMPLE_ROWS, service_for, write_sample


@pytest.fixture
def service(tmp_path: Path) -> DatasweepService:
    return service_for(tmp_path)


def _clean(service: DatasweepService, tmp_path: Path, **kwargs) -> Run:
    source = write_sample(tmp_path)
    return service.clean_file(source, out=str(tmp_path / "out"), **kwargs)


# -- FR-2 idempotence ------------------------------------------------------


def test_fr2_idempotent_skip(service: DatasweepService, tmp_path: Path) -> None:
    first = _clean(service, tmp_path)
    assert first.status is not RunStatus.SKIPPED
    second = _clean(service, tmp_path)
    assert second.status is RunStatus.SKIPPED
    assert second.artifact_dir is None
    assert second.id != first.id  # the skip is itself an append-only Run record


def test_fr2_force_rerun(service: DatasweepService, tmp_path: Path) -> None:
    first = _clean(service, tmp_path)
    forced = _clean(service, tmp_path, force=True)
    assert forced.status is first.status
    assert forced.artifact_dir == first.artifact_dir
    assert forced.id != first.id


def test_fr2_failed_run_does_not_suppress(service: DatasweepService, tmp_path: Path) -> None:
    """`failed` and `skipped` runs never suppress reprocessing (SCOPE.md FR-2)."""
    source = write_sample(tmp_path)
    policy_hash = Policy().policy_hash()
    from datasweep.services import sha256_file

    stored = service.repo.upsert_source_file(
        id=str(uuid.uuid4()), path=source, folder_id=None, now=FIXED_NOW
    )
    service.repo.add_run(
        Run(
            id=str(uuid.uuid4()),
            file_id=stored.id,
            content_sha256=sha256_file(source),
            policy_hash=policy_hash,
            policy_snapshot=Policy().model_dump(mode="json"),
            engine_version=ENGINE_VERSION,
            started_at=FIXED_NOW,
            finished_at=FIXED_NOW,
            status=RunStatus.FAILED,
            trigger=TriggerKind.MANUAL,
            error="earlier attempt blew up",
        )
    )
    run = service.clean_file(source, out=str(tmp_path / "out"))
    assert run.status is not RunStatus.SKIPPED


def test_fr2_policy_change_reprocesses(service: DatasweepService, tmp_path: Path) -> None:
    """The idempotence key is (content, policy, engine version) — D16."""
    _clean(service, tmp_path)
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text('[tiers]\n"fix.drop_duplicate_row" = "off"\n', encoding="utf-8")
    run = _clean(service, tmp_path, policy_path=str(policy_file))
    assert run.status is not RunStatus.SKIPPED


# -- US-6 policy overrides -------------------------------------------------


def test_us6_policy_disables_a_rule(service: DatasweepService, tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text('[tiers]\n"fix.drop_duplicate_row" = "off"\n', encoding="utf-8")
    run = _clean(service, tmp_path, policy_path=str(policy_file))

    audit = [
        json.loads(line)
        for line in Path(run.artifact_dir, "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert not [entry for entry in audit if entry.get("kind") == "row_drop"]
    assert "DUP" not in run.issue_counts
    report = Path(run.artifact_dir, "report.md").read_text(encoding="utf-8")
    assert "fix.drop_duplicate_row" in report
    assert "disabled by policy" in report
    assert run.policy_snapshot["tiers"]["fix.drop_duplicate_row"] == "off"
    assert run.policy_hash != Policy().policy_hash()


def test_policy_unknown_key_is_a_hard_error(service: DatasweepService, tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("[general]\nnot_a_setting = 3\n", encoding="utf-8")
    with pytest.raises(DatasweepError):
        service.effective_policy(str(policy_file))


# -- FR-1 settle check -----------------------------------------------------


def _observation(path: str, size: int, mtime: float, at) -> FileObservation:
    return FileObservation(path=path, size=size, mtime=mtime, observed_at=at)


def test_fr1_settle_defers_a_file_that_is_still_being_written(
    service: DatasweepService, tmp_path: Path
) -> None:
    """Times are injected; the engine and services never read the wall clock."""
    now = FIXED_NOW
    mtime = (now - timedelta(seconds=1)).timestamp()
    first = _observation("/data/a.csv", 10, mtime, now)
    ready, deferred = service.settle_partition([first], now, settle_seconds=5)
    assert (ready, [obs.path for obs in deferred]) == ([], ["/data/a.csv"])

    quiet = _observation("/data/a.csv", 10, (now - timedelta(seconds=30)).timestamp(), now)
    ready, deferred = service.settle_partition([quiet], now, settle_seconds=5)
    assert [obs.path for obs in ready] == ["/data/a.csv"]


def test_fr1_changed_signature_defers_until_stable(
    service: DatasweepService, tmp_path: Path
) -> None:
    old_mtime = (FIXED_NOW - timedelta(minutes=5)).timestamp()
    seen = _observation("/data/a.csv", 10, old_mtime, FIXED_NOW)
    service._observations = {seen.path: seen}
    grown = _observation("/data/a.csv", 4096, old_mtime, FIXED_NOW + timedelta(seconds=10))
    ready, deferred = service.settle_partition(
        [grown], FIXED_NOW + timedelta(seconds=10), settle_seconds=5
    )
    assert not ready and [obs.path for obs in deferred] == ["/data/a.csv"]


def test_fr1_scan_processes_settled_files_and_skips_the_output_dir(tmp_path: Path) -> None:
    watched = tmp_path / "drop"
    watched.mkdir()
    write_sample(watched)
    service = service_for(tmp_path)
    folder = service.add_folder(str(watched), output_dir=str(watched / ".datasweep"))
    later = FIXED_NOW.replace(year=2030)

    result = service.scan_once(now=later)
    assert len(result.processed) == 1
    run = service.get_run(result.processed[0])
    assert run.artifact_dir is not None
    assert run.artifact_dir.startswith(folder.output_dir)
    assert run.trigger is TriggerKind.SCAN

    # A second pass over unchanged content records only `skipped` runs (US-7)
    # and never re-reads the artifacts it just wrote.
    again = service.scan_once(now=later)
    assert again.processed == [] and len(again.skipped) == 1


# -- FR-10 artifacts and FR-12 persistence --------------------------------


def test_fr10_source_bytes_are_never_modified(service: DatasweepService, tmp_path: Path) -> None:
    source = Path(write_sample(tmp_path))
    before = source.read_bytes()
    service.clean_file(str(source), out=str(tmp_path / "out"))
    assert source.read_bytes() == before


def test_fr12_run_records_counts_profiles_and_summaries(
    service: DatasweepService, tmp_path: Path
) -> None:
    run = _clean(service, tmp_path)
    detail = service.run_detail(run.id)
    assert [profile.col_index for profile in detail.columns] == [0, 1, 2, 3]
    assert detail.revisions[0].revision_no == 1
    for klass, count in run.issue_counts.items():
        rolled = sum(
            summary.cell_count for summary in detail.issues if summary.klass.value == klass
        )
        assert rolled == count, f"{klass} rollup drifted from Run.issue_counts"


def test_unsupported_format_is_a_clear_error(service: DatasweepService, tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    path.write_bytes(b"PAR1")
    with pytest.raises(UnsupportedFormatError) as excinfo:
        service.clean_file(str(path), out=str(tmp_path / "out"))
    assert excinfo.value.code == "unsupported_format"
    failed = [run for run in service.list_runs() if run.status is RunStatus.FAILED]
    assert failed and failed[-1].error.startswith("unsupported_format")


def test_missing_file_is_reported_not_crashed(service: DatasweepService, tmp_path: Path) -> None:
    with pytest.raises(DatasweepError):
        service.clean_file(str(tmp_path / "nope.csv"))


def test_profile_writes_nothing(service: DatasweepService, tmp_path: Path) -> None:
    source = write_sample(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    result = service.profile_file(source)
    assert result.n_rows == SAMPLE_ROWS
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert service.list_runs() == []
