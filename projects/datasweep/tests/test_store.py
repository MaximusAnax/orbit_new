"""FR-12 run persistence and FR-11 review-queue mechanics, on both backends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from datasweep.engine.models import (
    ColumnProfile,
    ColumnType,
    Disposition,
    FileFormat,
    IssueClass,
    IssueSummary,
    ReviewItem,
    ReviewStatus,
    Revision,
    Run,
    RunStatus,
    TriggerKind,
    WatchedFolder,
)
from datasweep.errors import DatasweepError, ItemAlreadyDecidedError, UnknownRunError
from datasweep.store import InMemoryRepository, SqliteRepository
from support_datasweep import CONTENT_SHA, ENGINE_VERSION

NOW = datetime(2026, 1, 1, tzinfo=UTC)
POLICY_HASH = "0123456789abcdef"


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    if request.param == "memory":
        store = InMemoryRepository()
    else:
        store = SqliteRepository(tmp_path / "datasweep.db")
    yield store
    store.close()


def make_folder(path: str = "/data/exports") -> WatchedFolder:
    return WatchedFolder(id="folder-1", path=path, output_dir=f"{path}/.datasweep", created_at=NOW)


def make_run(
    repo,
    *,
    run_id: str = "run-1",
    status: RunStatus = RunStatus.SUCCEEDED,
    content: str = CONTENT_SHA,
    path: str = "/data/exports/sales.csv",
    finished: bool = True,
) -> Run:
    source = repo.upsert_source_file(id=f"file-{path}", path=path, folder_id=None, now=NOW)
    produced = status in (RunStatus.SUCCEEDED, RunStatus.REVIEW_PENDING)
    run = Run(
        id=run_id,
        file_id=source.id,
        content_sha256=content,
        policy_hash=POLICY_HASH,
        policy_snapshot={"general": {"settle_seconds": 5}},
        engine_version=ENGINE_VERSION,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2) if finished else None,
        status=status,
        trigger=TriggerKind.SCAN,
        format=FileFormat.CSV,
        encoding="utf-8",
        dialect={"delimiter": ",", "quotechar": '"', "has_header": True, "sheet": None},
        n_rows=3,
        n_cols=2,
        issue_counts={"WS": 2, "DUP": 1},
        change_counts={"auto": 3, "review": 0, "report": 0},
        artifact_dir="/data/exports/.datasweep/sales.9f86d081" if produced else None,
        error="boom" if status is RunStatus.FAILED else None,
    )
    return repo.add_run(run)


# --------------------------------------------------------------------------
# folders and source files
# --------------------------------------------------------------------------


def test_fr1_folder_crud_and_unique_path(repo) -> None:
    repo.add_folder(make_folder())
    assert [folder.path for folder in repo.list_folders()] == ["/data/exports"]
    with pytest.raises(DatasweepError):
        repo.add_folder(make_folder().model_copy(update={"id": "other"}))
    assert repo.delete_folder("folder-1") is True
    assert repo.delete_folder("folder-1") is False


def test_fr12_deleting_a_folder_leaves_runs_with_a_null_folder_id(repo) -> None:
    repo.add_folder(make_folder())
    repo.upsert_source_file(
        id="file-1", path="/data/exports/sales.csv", folder_id="folder-1", now=NOW
    )
    repo.delete_folder("folder-1")
    source = repo.get_source_file("/data/exports/sales.csv")
    assert source is not None
    assert source.folder_id is None


def test_fr12_source_file_upsert_keeps_first_seen(repo) -> None:
    first = repo.upsert_source_file(id="file-1", path="/a.csv", folder_id=None, now=NOW)
    later = repo.upsert_source_file(
        id="file-2", path="/a.csv", folder_id=None, now=NOW + timedelta(days=1)
    )
    assert later.id == first.id
    assert later.first_seen_at == NOW
    assert later.last_seen_at > first.last_seen_at


# --------------------------------------------------------------------------
# runs (append-only)
# --------------------------------------------------------------------------


def test_fr12_runs_are_append_only(repo) -> None:
    run = make_run(repo)
    with pytest.raises(DatasweepError):
        repo.add_run(run)
    with pytest.raises(DatasweepError):
        repo.finalize_run(run)


def test_fr12_run_is_finalized_once(repo) -> None:
    run = make_run(repo, finished=False, status=RunStatus.SUCCEEDED)
    assert repo.get_run(run.id).finished_at is None
    finished = run.model_copy(update={"finished_at": NOW + timedelta(seconds=1)})
    repo.finalize_run(finished)
    assert repo.get_run(run.id).finished_at is not None
    with pytest.raises(DatasweepError):
        repo.finalize_run(finished)


def test_fr12_run_round_trips_every_field(repo) -> None:
    run = make_run(repo)
    stored = repo.get_run(run.id)
    assert stored == run


def test_fr2_identity_lookup_only_matches_completed_runs(repo) -> None:
    make_run(repo, run_id="failed", status=RunStatus.FAILED)
    make_run(repo, run_id="skipped", status=RunStatus.SKIPPED, path="/data/exports/b.csv")
    identity = {
        "content_sha256": CONTENT_SHA,
        "policy_hash": POLICY_HASH,
        "engine_version": ENGINE_VERSION,
    }
    assert repo.find_completed_run(**identity) is None
    make_run(repo, run_id="ok", path="/data/exports/c.csv")
    assert repo.find_completed_run(**identity).id == "ok"


def test_fr2_identity_lookup_is_content_and_policy_scoped(repo) -> None:
    make_run(repo)
    assert (
        repo.find_completed_run(
            content_sha256="b" * 64, policy_hash=POLICY_HASH, engine_version=ENGINE_VERSION
        )
        is None
    )
    assert (
        repo.find_completed_run(
            content_sha256=CONTENT_SHA, policy_hash="f" * 16, engine_version=ENGINE_VERSION
        )
        is None
    )


def test_fr12_runs_are_queryable_by_path_and_status(repo) -> None:
    make_run(repo, run_id="a")
    make_run(repo, run_id="b", path="/data/exports/other.csv", status=RunStatus.REVIEW_PENDING)
    assert [run.id for run in repo.list_runs(path="/data/exports/sales.csv")] == ["a"]
    assert [run.id for run in repo.list_runs(status=RunStatus.REVIEW_PENDING)] == ["b"]
    assert len(repo.list_runs()) == 2


def test_fr12_artifact_dir_is_required_exactly_when_a_run_produced_one() -> None:
    with pytest.raises(ValueError, match="artifact_dir"):
        Run(
            id="x",
            file_id="f",
            content_sha256=CONTENT_SHA,
            policy_hash=POLICY_HASH,
            engine_version=ENGINE_VERSION,
            started_at=NOW,
            status=RunStatus.SUCCEEDED,
            trigger=TriggerKind.MANUAL,
        )


def test_fr12_failed_run_must_carry_its_error() -> None:
    with pytest.raises(ValueError, match="error"):
        Run(
            id="x",
            file_id="f",
            content_sha256=CONTENT_SHA,
            policy_hash=POLICY_HASH,
            engine_version=ENGINE_VERSION,
            started_at=NOW,
            status=RunStatus.FAILED,
            trigger=TriggerKind.MANUAL,
        )


# --------------------------------------------------------------------------
# profiles and issue rollups
# --------------------------------------------------------------------------


def test_fr12_column_profiles_round_trip(repo) -> None:
    run = make_run(repo)
    profiles = [
        ColumnProfile(
            col_index=0,
            name="amount_2",
            original_name="amount",
            inferred_type=ColumnType.FLOAT,
            type_coverage=0.978,
            non_null=405,
            null_count=7,
            distinct_count=388,
            stats={"min": 0.99, "convention": {"decimal": "."}},
        )
    ]
    repo.add_column_profiles(run.id, profiles)
    stored = repo.list_column_profiles(run.id)
    assert stored[0].original_name == "amount"
    assert stored[0].stats["convention"]["decimal"] == "."
    assert stored[0].run_id == run.id


def test_fr12_issue_summaries_reconcile_with_run_counts(repo) -> None:
    run = make_run(repo)
    summaries = [
        IssueSummary(
            id="s1",
            run_id=run.id,
            klass=IssueClass.WS,
            col_index=0,
            cell_count=2,
            disposition=Disposition.FIXED,
            samples=[{"row": 1, "before": " a ", "after": "a"}],
        ),
        IssueSummary(
            id="s2",
            run_id=run.id,
            klass=IssueClass.DUP,
            col_index=None,
            cell_count=1,
            disposition=Disposition.FIXED,
        ),
    ]
    repo.add_issue_summaries(summaries)
    stored = repo.list_issue_summaries(run.id)
    totals: dict[str, int] = {}
    for summary in stored:
        totals[summary.klass.value] = totals.get(summary.klass.value, 0) + summary.cell_count
    assert totals == run.issue_counts


def test_fr12_issue_summary_samples_are_capped_at_ten() -> None:
    with pytest.raises(ValueError, match="capped"):
        IssueSummary(
            id="s",
            run_id="r",
            klass=IssueClass.WS,
            cell_count=11,
            disposition=Disposition.FIXED,
            samples=[{"row": index} for index in range(11)],
        )


def test_fr12_writes_require_a_known_run(repo) -> None:
    with pytest.raises(UnknownRunError):
        repo.add_column_profiles("nope", [])


# --------------------------------------------------------------------------
# review items and revisions
# --------------------------------------------------------------------------


def make_item(run_id: str, item_id: str = "a3f1c2d9") -> ReviewItem:
    return ReviewItem(
        id=item_id,
        run_id=run_id,
        rule="fix.date_canon_ambiguous",
        col_index=4,
        description="order_date: ambiguous d/m",
        proposal={"cells": [{"row": 0, "col": 4, "before": "03/04/2021", "after": "2021-04-03"}]},
        affected_cells=1,
        confidence=0.31,
    )


def test_fr11_review_item_transitions_exactly_once(repo) -> None:
    run = make_run(repo)
    repo.add_review_items([make_item(run.id)])
    decided = repo.decide_review_item(run.id, "a3f1c2d9", ReviewStatus.ACCEPTED, NOW)
    assert decided.status is ReviewStatus.ACCEPTED
    assert decided.decided_at == NOW
    with pytest.raises(ItemAlreadyDecidedError) as excinfo:
        repo.decide_review_item(run.id, "a3f1c2d9", ReviewStatus.REJECTED, NOW)
    assert excinfo.value.code == "item_already_decided"


def test_fr11_decided_at_is_set_exactly_when_decided() -> None:
    payload = make_item("r").model_dump() | {"status": "accepted"}
    with pytest.raises(ValueError, match="decided_at"):
        ReviewItem.model_validate(payload)


def test_fr11_affected_cells_must_match_the_proposal() -> None:
    payload = make_item("r").model_dump() | {"affected_cells": 9}
    with pytest.raises(ValueError, match="affected_cells"):
        ReviewItem.model_validate(payload)


def test_fr11_a_decided_item_cannot_transition_again() -> None:
    accepted = make_item("r").decide(ReviewStatus.ACCEPTED, NOW)
    assert accepted.decided_at == NOW
    with pytest.raises(ValueError, match="already"):
        accepted.decide(ReviewStatus.REJECTED, NOW)


def test_fr11_rejected_items_are_findable_by_content_hash(repo) -> None:
    run = make_run(repo)
    repo.add_review_items([make_item(run.id)])
    repo.decide_review_item(run.id, "a3f1c2d9", ReviewStatus.REJECTED, NOW)
    assert repo.rejected_item_ids(CONTENT_SHA) == {"a3f1c2d9"}
    assert repo.rejected_item_ids("b" * 64) == set()


def test_fr11_unknown_item_raises(repo) -> None:
    run = make_run(repo)
    with pytest.raises(UnknownRunError):
        repo.decide_review_item(run.id, "nope", ReviewStatus.ACCEPTED, NOW)


def test_fr11_revisions_are_append_only_and_dense(repo) -> None:
    run = make_run(repo)
    first = Revision(
        id="rev-1",
        run_id=run.id,
        revision_no=1,
        created_at=NOW,
        accepted_item_ids=[],
        cleaned_path="/out/cleaned.csv",
        audit_path="/out/audit.jsonl",
    )
    repo.add_revision(first)
    second = first.model_copy(
        update={
            "id": "rev-2",
            "revision_no": 2,
            "accepted_item_ids": ["a3f1c2d9"],
            "cleaned_path": "/out/cleaned.r2.csv",
            "audit_path": "/out/audit.r2.jsonl",
        }
    )
    repo.add_revision(second)
    assert [rev.revision_no for rev in repo.list_revisions(run.id)] == [1, 2]
    with pytest.raises(DatasweepError):
        repo.add_revision(second)


def test_fr11_revision_one_accepts_no_items() -> None:
    with pytest.raises(ValueError, match="revision 1"):
        Revision(
            id="r",
            run_id="run",
            revision_no=1,
            created_at=NOW,
            accepted_item_ids=["x"],
            cleaned_path="a",
            audit_path="b",
        )


def test_sqlite_schema_enforces_the_status_check(tmp_path) -> None:
    import sqlite3

    store = SqliteRepository(tmp_path / "db.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO runs (id, file_id, content_sha256, policy_hash, policy_snapshot,"
            " engine_version, started_at, status, trigger_kind, artifact_dir)"
            " VALUES ('x','f','h','p','{}','v','t','succeeded','scan', NULL)"
        )
    store.close()
