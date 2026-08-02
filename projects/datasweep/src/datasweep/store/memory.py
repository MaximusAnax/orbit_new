"""In-memory Repository backend for tests (SCOPE.md §Store).

Enforces the same invariants as the SQLite backend — unique paths, append-only
runs, one review-item transition, dense revision numbers — so a test that
passes here is not passing because the store was lenient.
"""

from __future__ import annotations

from datetime import datetime

from ..engine.models import (
    ColumnProfile,
    IssueSummary,
    ReviewItem,
    ReviewStatus,
    Revision,
    Run,
    RunStatus,
    SourceFile,
    WatchedFolder,
)
from ..errors import DatasweepError, ItemAlreadyDecidedError, UnknownRunError
from .base import SUPPRESSING_STATUSES


class InMemoryRepository:
    """Dict-backed store with SQLite-equivalent semantics."""

    def __init__(self) -> None:
        self._folders: dict[str, WatchedFolder] = {}
        self._files: dict[str, SourceFile] = {}
        self._runs: dict[str, Run] = {}
        self._run_order: list[str] = []
        self._profiles: dict[str, list[ColumnProfile]] = {}
        self._summaries: dict[str, list[IssueSummary]] = {}
        self._items: dict[tuple[str, str], ReviewItem] = {}
        self._revisions: dict[str, list[Revision]] = {}

    # -- watched folders ---------------------------------------------------

    def add_folder(self, folder: WatchedFolder) -> WatchedFolder:
        if any(existing.path == folder.path for existing in self._folders.values()):
            raise DatasweepError(f"watched folder already exists: {folder.path}")
        self._folders[folder.id] = folder
        return folder

    def list_folders(self) -> list[WatchedFolder]:
        return sorted(self._folders.values(), key=lambda folder: folder.path)

    def get_folder(self, folder_id: str) -> WatchedFolder | None:
        return self._folders.get(folder_id)

    def delete_folder(self, folder_id: str) -> bool:
        if folder_id not in self._folders:
            return False
        del self._folders[folder_id]
        for path, source in list(self._files.items()):
            if source.folder_id == folder_id:
                self._files[path] = source.model_copy(update={"folder_id": None})
        return True

    # -- source files ------------------------------------------------------

    def upsert_source_file(
        self, *, id: str, path: str, folder_id: str | None, now: datetime
    ) -> SourceFile:
        existing = self._files.get(path)
        if existing is None:
            record = SourceFile(
                id=id, path=path, folder_id=folder_id, first_seen_at=now, last_seen_at=now
            )
        else:
            record = existing.model_copy(
                update={
                    "last_seen_at": now,
                    "folder_id": folder_id if folder_id is not None else existing.folder_id,
                }
            )
        self._files[path] = record
        return record

    def get_source_file(self, path: str) -> SourceFile | None:
        return self._files.get(path)

    def get_source_file_by_id(self, file_id: str) -> SourceFile | None:
        return next((record for record in self._files.values() if record.id == file_id), None)

    # -- runs --------------------------------------------------------------

    def add_run(self, run: Run) -> Run:
        if run.id in self._runs:
            raise DatasweepError(f"run {run.id} already exists (runs are append-only)")
        self._runs[run.id] = run
        self._run_order.append(run.id)
        return run

    def finalize_run(self, run: Run) -> Run:
        existing = self._runs.get(run.id)
        if existing is None:
            raise UnknownRunError(f"unknown run: {run.id}", run_id=run.id)
        if existing.finished_at is not None:
            raise DatasweepError(f"run {run.id} is already finalized (append-only)")
        self._runs[run.id] = run
        return run

    def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list_runs(self, *, path: str | None = None, status: RunStatus | None = None) -> list[Run]:
        file_id = None
        if path is not None:
            source = self._files.get(path)
            if source is None:
                return []
            file_id = source.id
        out = [self._runs[run_id] for run_id in self._run_order]
        if file_id is not None:
            out = [run for run in out if run.file_id == file_id]
        if status is not None:
            out = [run for run in out if run.status is status]
        return out

    def find_completed_run(
        self, *, content_sha256: str, policy_hash: str, engine_version: str
    ) -> Run | None:
        for run_id in self._run_order:
            run = self._runs[run_id]
            if (
                run.content_sha256 == content_sha256
                and run.policy_hash == policy_hash
                and run.engine_version == engine_version
                and run.status in SUPPRESSING_STATUSES
            ):
                return run
        return None

    # -- profiles and rollups ---------------------------------------------

    def add_column_profiles(self, run_id: str, profiles: list[ColumnProfile]) -> None:
        self._require_run(run_id)
        self._profiles[run_id] = [
            profile.model_copy(update={"run_id": run_id}) for profile in profiles
        ]

    def list_column_profiles(self, run_id: str) -> list[ColumnProfile]:
        return sorted(self._profiles.get(run_id, []), key=lambda p: p.col_index)

    def add_issue_summaries(self, summaries: list[IssueSummary]) -> None:
        for summary in summaries:
            self._require_run(summary.run_id)
            self._summaries.setdefault(summary.run_id, []).append(summary)

    def list_issue_summaries(self, run_id: str) -> list[IssueSummary]:
        return sorted(
            self._summaries.get(run_id, []),
            key=lambda s: (s.klass.value, -1 if s.col_index is None else s.col_index),
        )

    # -- review queue ------------------------------------------------------

    def add_review_items(self, items: list[ReviewItem]) -> None:
        for item in items:
            self._require_run(item.run_id)
            key = (item.run_id, item.id)
            if key in self._items:
                raise DatasweepError(f"review item {item.id} already exists for run {item.run_id}")
            self._items[key] = item

    def list_review_items(self, run_id: str) -> list[ReviewItem]:
        return sorted(
            (item for (run, _), item in self._items.items() if run == run_id),
            key=lambda item: item.id,
        )

    def get_review_item(self, run_id: str, item_id: str) -> ReviewItem | None:
        return self._items.get((run_id, item_id))

    def decide_review_item(
        self, run_id: str, item_id: str, status: ReviewStatus, now: datetime
    ) -> ReviewItem:
        item = self._items.get((run_id, item_id))
        if item is None:
            raise UnknownRunError(f"unknown review item: {item_id}", item_id=item_id)
        if item.status is not ReviewStatus.PENDING:
            raise ItemAlreadyDecidedError(
                f"review item {item_id} is already {item.status.value}",
                item_id=item_id,
                status=item.status.value,
            )
        decided = item.decide(status, now)
        self._items[(run_id, item_id)] = decided
        return decided

    def rejected_item_ids(self, content_sha256: str) -> set[str]:
        runs = {run.id for run in self._runs.values() if run.content_sha256 == content_sha256}
        return {
            item.id
            for (run_id, _), item in self._items.items()
            if run_id in runs and item.status is ReviewStatus.REJECTED
        }

    # -- revisions ---------------------------------------------------------

    def add_revision(self, revision: Revision) -> Revision:
        self._require_run(revision.run_id)
        existing = self._revisions.setdefault(revision.run_id, [])
        if any(rev.revision_no == revision.revision_no for rev in existing):
            raise DatasweepError(
                f"revision {revision.revision_no} already exists for run {revision.run_id}"
            )
        expected = len(existing) + 1
        if revision.revision_no != expected:
            raise DatasweepError(
                f"revision numbers must be dense from 1: expected {expected}, "
                f"got {revision.revision_no}"
            )
        existing.append(revision)
        return revision

    def list_revisions(self, run_id: str) -> list[Revision]:
        return sorted(self._revisions.get(run_id, []), key=lambda rev: rev.revision_no)

    def close(self) -> None:
        return None

    # -- helpers -----------------------------------------------------------

    def _require_run(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise UnknownRunError(f"unknown run: {run_id}", run_id=run_id)
        return run
