"""The Repository port (SCOPE.md §Store, DATA_MODEL §2/§4).

Two backends implement it: :class:`~datasweep.store.sqlite.SqliteRepository`
(default) and :class:`~datasweep.store.memory.InMemoryRepository` (tests).  No
engine logic lives here — the store only persists and queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

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

#: Statuses that suppress reprocessing of the same (content, policy, engine)
#: triple (FR-2).  ``failed`` and ``skipped`` deliberately do not.
SUPPRESSING_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.REVIEW_PENDING}
)


@runtime_checkable
class Repository(Protocol):
    # -- watched folders ---------------------------------------------------
    def add_folder(self, folder: WatchedFolder) -> WatchedFolder: ...

    def list_folders(self) -> list[WatchedFolder]: ...

    def get_folder(self, folder_id: str) -> WatchedFolder | None: ...

    def delete_folder(self, folder_id: str) -> bool: ...

    # -- source files ------------------------------------------------------
    def upsert_source_file(
        self, *, id: str, path: str, folder_id: str | None, now: datetime
    ) -> SourceFile: ...

    def get_source_file(self, path: str) -> SourceFile | None: ...

    def get_source_file_by_id(self, file_id: str) -> SourceFile | None: ...

    # -- runs --------------------------------------------------------------
    def add_run(self, run: Run) -> Run: ...

    def finalize_run(self, run: Run) -> Run: ...

    def get_run(self, run_id: str) -> Run | None: ...

    def list_runs(
        self, *, path: str | None = None, status: RunStatus | None = None
    ) -> list[Run]: ...

    def find_completed_run(
        self, *, content_sha256: str, policy_hash: str, engine_version: str
    ) -> Run | None: ...

    # -- profiles and issue rollups ---------------------------------------
    def add_column_profiles(self, run_id: str, profiles: list[ColumnProfile]) -> None: ...

    def list_column_profiles(self, run_id: str) -> list[ColumnProfile]: ...

    def add_issue_summaries(self, summaries: list[IssueSummary]) -> None: ...

    def list_issue_summaries(self, run_id: str) -> list[IssueSummary]: ...

    # -- review queue ------------------------------------------------------
    def add_review_items(self, items: list[ReviewItem]) -> None: ...

    def list_review_items(self, run_id: str) -> list[ReviewItem]: ...

    def get_review_item(self, run_id: str, item_id: str) -> ReviewItem | None: ...

    def decide_review_item(
        self, run_id: str, item_id: str, status: ReviewStatus, now: datetime
    ) -> ReviewItem: ...

    def rejected_item_ids(self, content_sha256: str) -> set[str]: ...

    # -- revisions ---------------------------------------------------------
    def add_revision(self, revision: Revision) -> Revision: ...

    def list_revisions(self, run_id: str) -> list[Revision]: ...

    def close(self) -> None: ...
