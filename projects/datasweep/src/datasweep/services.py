"""Orchestration: watcher → reader → engine → store → artifacts.

The only layer that touches both I/O and the engine (SCOPE.md §Architecture).
API and CLI call *this* and nothing below it, so every business rule has one
home.  Timestamps enter through the :class:`~datasweep.adapters.clock.Clock`
port and are passed into the engine as data — the engine never reads a clock,
and no timestamp ever reaches an artifact (FR-16).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from . import __version__
from .adapters.artifacts import (
    ArtifactWriter,
    LocalArtifactWriter,
    artifact_dir_name,
    audit_filename,
    cleaned_filename,
)
from .adapters.clock import Clock, SystemClock
from .adapters.notifier import Notifier, NullNotifier
from .adapters.readers import FileMeta, TableReader, default_readers, reader_for
from .adapters.watcher import FileObservation, PollingScanner, Watcher
from .engine.models import (
    DEFAULT_INCLUDE,
    AuditEntry,
    ColumnProfile,
    Disposition,
    FileFormat,
    Issue,
    IssueClass,
    IssueSummary,
    Policy,
    RawTable,
    ReviewItem,
    ReviewStatus,
    Revision,
    Run,
    RunStatus,
    RunSummary,
    Tier,
    TriggerKind,
    WatchedFolder,
)
from .engine.pipeline import apply_revision, clean_table
from .engine.report import render_report
from .engine.transforms import revert
from .errors import DatasweepError, UnknownItemError, UnknownRunError

#: Per-folder default artifact directory (DATA_MODEL §2.1).
DEFAULT_OUTPUT_DIRNAME = ".datasweep"

#: Cap on the per-(class, column) examples stored in SQLite (DATA_MODEL §2.5).
MAX_SUMMARY_SAMPLES = 10


# --------------------------------------------------------------------------
# Service-level result models
# --------------------------------------------------------------------------


class ScanResult(BaseModel):
    """One scan pass (FR-1/FR-2/FR-13)."""

    model_config = ConfigDict(frozen=True)

    processed: list[str] = []
    skipped: list[str] = []
    deferred: list[str] = []
    failed: list[str] = []


class RevertCheck(BaseModel):
    """FR-9 verification against the *persisted* artifacts."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    revision: int
    match: bool
    mismatches: list[str] = []


class ProfileResult(BaseModel):
    """``datasweep profile`` — everything the pipeline learned, nothing written."""

    model_config = ConfigDict(frozen=True)

    path: str
    format: FileFormat
    encoding: str | None = None
    dialect: dict[str, Any] | None = None
    n_rows: int
    n_cols: int
    columns: list[ColumnProfile] = []
    issues: list[Issue] = []
    review_items: list[ReviewItem] = []
    issue_counts: dict[str, int] = {}
    change_counts: dict[str, int] = {}


class RunDetail(BaseModel):
    """``GET /runs/{id}`` — the run plus everything the store hangs off it."""

    model_config = ConfigDict(frozen=True)

    run: Run
    columns: list[ColumnProfile] = []
    issues: list[IssueSummary] = []
    revisions: list[Revision] = []
    review_items: list[ReviewItem] = []


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_table_artifact(path: str, fmt: FileFormat) -> RawTable:
    """Parse a written ``cleaned.*`` back into a table, verbatim.

    Deliberately *not* the reader stack: the reader applies FR-3's headerless
    heuristic and dialect sniffing, which are decisions about an unknown file.
    A cleaned artifact's shape is known exactly, so reading it back must be a
    mechanical inverse of :func:`serialize_table` or the FR-9 check would be
    testing the sniffer instead of the audit.
    """
    text = Path(path).read_text(encoding="utf-8")
    if fmt is FileFormat.JSONL:
        headers: list[str] = []
        rows: list[list[str | None]] = []
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        for record in records:
            for key in record:
                if key not in headers:
                    headers.append(key)
        for record in records:
            rows.append([record.get(key) for key in headers])
        return RawTable(headers=headers, rows=rows)

    import csv
    import io

    delimiter = "\t" if fmt is FileFormat.TSV else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"')
    grid = [row for row in reader if row]
    if not grid:
        return RawTable(headers=[], rows=[])
    return RawTable(
        headers=list(grid[0]),
        rows=[[None if cell == "" else cell for cell in row] for row in grid[1:]],
    )


def _read_audit_artifact(path: str) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("kind") == "header":
            continue
        entries.append(AuditEntry.model_validate(payload))
    return entries


def _table_mismatches(expected: RawTable, actual: RawTable) -> list[str]:
    """Cell-exact comparison, reported in a form a human can act on."""
    problems: list[str] = []
    if expected.headers != actual.headers:
        problems.append(f"headers differ: {expected.headers} != {actual.headers}")
    if expected.n_rows != actual.n_rows:
        problems.append(f"row count differs: {expected.n_rows} != {actual.n_rows}")
    for index in range(min(expected.n_rows, actual.n_rows)):
        left, right = expected.rows[index], actual.rows[index]
        if left != right:
            problems.append(f"row {index} differs: {left!r} != {right!r}")
        if len(problems) >= 20:
            problems.append("… further differences suppressed")
            break
    return problems


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


class DatasweepService:
    """Everything the API and the CLI are allowed to do."""

    def __init__(
        self,
        repository: Any,
        *,
        clock: Clock | None = None,
        watcher: Watcher | None = None,
        artifact_writer: ArtifactWriter | None = None,
        notifier: Notifier | None = None,
        readers: dict[str, TableReader] | None = None,
        base_policy: Policy | None = None,
        engine_version: str = __version__,
    ) -> None:
        self.repo = repository
        self.clock = clock or SystemClock()
        self.watcher = watcher or PollingScanner()
        self.artifacts = artifact_writer or LocalArtifactWriter()
        self.notifier = notifier or NullNotifier()
        self.readers = readers if readers is not None else default_readers()
        self.base_policy = base_policy or Policy()
        self.engine_version = engine_version
        self._observations: dict[str, FileObservation] = {}

    # -- policy ------------------------------------------------------------

    def effective_policy(
        self,
        policy_path: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Policy:
        """Built-in defaults ⊕ policy file ⊕ CLI/API overrides (DATA_MODEL §3.3)."""
        policy = self.base_policy
        if policy_path:
            text = Path(policy_path).read_text(encoding="utf-8")
            try:
                policy = policy.merged(Policy.from_toml(text))
            except ValueError as exc:
                raise DatasweepError(f"invalid policy {policy_path}: {exc}") from exc
        if overrides:
            policy = policy.merged(overrides)
        return policy

    # -- watched folders (FR-1) -------------------------------------------

    def add_folder(
        self,
        path: str,
        *,
        recursive: bool = True,
        include: list[str] | None = None,
        policy_path: str | None = None,
        output_dir: str | None = None,
        now: datetime | None = None,
    ) -> WatchedFolder:
        absolute = str(Path(path).expanduser().resolve())
        folder = WatchedFolder(
            id=str(uuid.uuid4()),
            path=absolute,
            recursive=recursive,
            include=list(include) if include else list(DEFAULT_INCLUDE),
            policy_path=str(Path(policy_path).resolve()) if policy_path else None,
            output_dir=str(Path(output_dir).expanduser().resolve())
            if output_dir
            else str(Path(absolute) / DEFAULT_OUTPUT_DIRNAME),
            created_at=now or self.clock.now(),
        )
        return self.repo.add_folder(folder)

    def list_folders(self) -> list[WatchedFolder]:
        return self.repo.list_folders()

    def delete_folder(self, folder_id: str) -> bool:
        return self.repo.delete_folder(folder_id)

    # -- scanning (FR-1, FR-13) -------------------------------------------

    def settle_partition(
        self, observations: list[FileObservation], now: datetime, settle_seconds: float
    ) -> tuple[list[FileObservation], list[FileObservation]]:
        """Split a scan into (ready, deferred).

        A file is ready when it has been quiet for ``settle_seconds`` *and* its
        (size, mtime) signature is unchanged since the previous scan.  The
        quiet test is what lets a single ``run --once`` in a fresh process do
        useful work; the signature test is what defers a file that is being
        written across two consecutive polls (FR-1, D15).  Both read time from
        the caller — never from the wall clock.
        """
        ready: list[FileObservation] = []
        deferred: list[FileObservation] = []
        for observation in observations:
            age = (now - datetime.fromtimestamp(observation.mtime, UTC)).total_seconds()
            previous = self._observations.get(observation.path)
            unchanged = previous is None or previous.same_content_signature(observation)
            (ready if age >= settle_seconds and unchanged else deferred).append(observation)
        return ready, deferred

    def scan_once(self, *, now: datetime | None = None, force: bool = False) -> ScanResult:
        """One deterministic scan pass over every enabled watched folder."""
        moment = now or self.clock.now()
        folders = [folder for folder in self.repo.list_folders() if folder.enabled]
        observations = self.watcher.scan(folders, moment)
        by_folder = {folder.path: folder for folder in folders}

        settle = max(
            [self.base_policy.general.settle_seconds]
            + [
                self.effective_policy(folder.policy_path).general.settle_seconds
                for folder in folders
            ],
            default=self.base_policy.general.settle_seconds,
        )
        ready, deferred = self.settle_partition(observations, moment, settle)
        self._observations = {obs.path: obs for obs in observations}

        processed: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        for observation in ready:
            folder = self._owning_folder(observation.path, by_folder)
            try:
                run = self.clean_file(
                    observation.path,
                    folder=folder,
                    trigger=TriggerKind.FORCED if force else TriggerKind.SCAN,
                    force=force,
                    now=moment,
                )
            except DatasweepError:
                failed.append(observation.path)
                continue
            if run.status is RunStatus.SKIPPED:
                skipped.append(run.id)
            elif run.status is RunStatus.FAILED:
                failed.append(run.id)
            else:
                processed.append(run.id)
        return ScanResult(
            processed=processed,
            skipped=skipped,
            deferred=[obs.path for obs in deferred],
            failed=failed,
        )

    @staticmethod
    def _owning_folder(path: str, by_folder: dict[str, WatchedFolder]) -> WatchedFolder | None:
        """Deepest watched folder containing ``path`` — nested watches resolve."""
        best: WatchedFolder | None = None
        for folder_path, folder in by_folder.items():
            contains = path == folder_path or path.startswith(folder_path.rstrip("/") + "/")
            if contains and (best is None or len(folder_path) > len(best.path)):
                best = folder
        return best

    # -- cleaning (US-5, FR-2, FR-8, FR-10, FR-12) ------------------------

    def clean_file(
        self,
        path: str,
        *,
        out: str | None = None,
        policy_path: str | None = None,
        force: bool = False,
        sheet: int | str | None = None,
        folder: WatchedFolder | None = None,
        trigger: TriggerKind = TriggerKind.MANUAL,
        now: datetime | None = None,
    ) -> Run:
        """Process one file end to end and record exactly one Run."""
        source = Path(path).expanduser()
        if not source.is_file():
            raise DatasweepError(f"no such file: {path}", path=str(source))
        absolute = str(source.resolve())
        started_at = now or self.clock.now()

        overrides: dict[str, Any] = {}
        if sheet is not None:
            overrides["general"] = {"xlsx_sheet": sheet}
        policy = self.effective_policy(
            policy_path or (folder.policy_path if folder else None), overrides
        )
        policy_hash = policy.policy_hash()

        content_sha256 = sha256_file(absolute)
        source_file = self.repo.upsert_source_file(
            id=str(uuid.uuid4()),
            path=absolute,
            folder_id=folder.id if folder else None,
            now=started_at,
        )

        if not force:
            previous = self.repo.find_completed_run(
                content_sha256=content_sha256,
                policy_hash=policy_hash,
                engine_version=self.engine_version,
            )
            if previous is not None:
                return self.repo.add_run(
                    Run(
                        id=str(uuid.uuid4()),
                        file_id=source_file.id,
                        content_sha256=content_sha256,
                        policy_hash=policy_hash,
                        policy_snapshot=policy.model_dump(mode="json"),
                        engine_version=self.engine_version,
                        started_at=started_at,
                        finished_at=started_at,
                        status=RunStatus.SKIPPED,
                        trigger=trigger,
                    )
                )

        provisional = Run(
            id=str(uuid.uuid4()),
            file_id=source_file.id,
            content_sha256=content_sha256,
            policy_hash=policy_hash,
            policy_snapshot=policy.model_dump(mode="json"),
            engine_version=self.engine_version,
            started_at=started_at,
            status=RunStatus.FAILED,
            trigger=TriggerKind.FORCED if force and trigger is TriggerKind.SCAN else trigger,
            error="run did not complete",
        )
        self.repo.add_run(provisional)

        try:
            run = self._process(
                provisional,
                absolute,
                policy=policy,
                policy_hash=policy_hash,
                content_sha256=content_sha256,
                out=out,
                folder=folder,
                now=now,
            )
        except DatasweepError as exc:
            failed = provisional.model_copy(
                update={
                    "finished_at": now or self.clock.now(),
                    "status": RunStatus.FAILED,
                    "error": f"{exc.code}: {exc.message}",
                }
            )
            self.repo.finalize_run(failed)
            raise
        except Exception as exc:  # pragma: no cover - defensive, never silent
            failed = provisional.model_copy(
                update={
                    "finished_at": now or self.clock.now(),
                    "status": RunStatus.FAILED,
                    "error": f"unexpected: {exc}",
                }
            )
            self.repo.finalize_run(failed)
            raise
        return run

    def _process(
        self,
        provisional: Run,
        absolute: str,
        *,
        policy: Policy,
        policy_hash: str,
        content_sha256: str,
        out: str | None,
        folder: WatchedFolder | None,
        now: datetime | None,
    ) -> Run:
        data = Path(absolute).read_bytes()
        reader = reader_for(absolute, self.readers)
        meta = reader.sniff(absolute, data)
        read_result = reader.read(data, meta, policy)
        table, reader_issues, meta = read_result.table, list(read_result.issues), read_result.meta

        result = clean_table(
            table,
            policy,
            content_sha256=content_sha256,
            engine_version=self.engine_version,
            policy_hash=policy_hash,
            reader_issues=reader_issues,
        )

        source_name = Path(absolute).name
        report_md = render_report(
            result,
            source_name=source_name,
            content_sha256=content_sha256,
            policy_hash=policy_hash,
            engine_version=self.engine_version,
            revision=1,
            file_format=meta.format.value,
            encoding=meta.encoding,
            dialect=meta.dialect(),
        )
        run_dir = self._artifact_dir(absolute, content_sha256, out=out, folder=folder)
        paths = self.artifacts.write(
            run_dir,
            cleaned=result.cleaned,
            audit=result.audit,
            findings=result.issues,
            report_md=report_md,
            fmt=meta.format,
            header=self._audit_header(source_name, content_sha256, policy_hash, 1, result.cleaned),
            revision=1,
        )

        # FR-10: the source is never opened for writing; prove it every run.
        after_hash = sha256_file(absolute)
        if after_hash != content_sha256:
            raise DatasweepError(
                f"source file changed while it was being processed: {absolute}",
                before=content_sha256,
                after=after_hash,
            )

        rejected = self.repo.rejected_item_ids(content_sha256)
        review_items = [
            item.model_copy(update={"run_id": provisional.id})
            for item in result.review_items
            if item.id not in rejected
        ]
        finished_at = now or self.clock.now()
        run = provisional.model_copy(
            update={
                "finished_at": finished_at,
                "status": RunStatus.REVIEW_PENDING if review_items else RunStatus.SUCCEEDED,
                "error": None,
                "format": meta.format,
                "encoding": meta.encoding,
                "dialect": meta.dialect(),
                "n_rows": result.original.n_rows,
                "n_cols": result.original.n_cols,
                "issue_counts": dict(result.issue_counts),
                "change_counts": dict(result.change_counts),
                "artifact_dir": paths.directory,
            }
        )
        self.repo.finalize_run(run)
        self.repo.add_column_profiles(run.id, result.profiles)
        self.repo.add_issue_summaries(self._issue_summaries(run.id, result.issues))
        if review_items:
            self.repo.add_review_items(review_items)
        self.repo.add_revision(
            Revision(
                id=str(uuid.uuid4()),
                run_id=run.id,
                revision_no=1,
                created_at=finished_at,
                accepted_item_ids=[],
                cleaned_path=paths.cleaned,
                audit_path=paths.audit,
            )
        )
        self.notifier.notify(self.run_summary(run, source_name))
        return run

    def profile_file(
        self,
        path: str,
        *,
        policy_path: str | None = None,
        sheet: int | str | None = None,
    ) -> ProfileResult:
        """US-5's dry run: full pipeline, nothing written, nothing persisted."""
        source = Path(path).expanduser()
        if not source.is_file():
            raise DatasweepError(f"no such file: {path}", path=str(source))
        overrides: dict[str, Any] = {"general": {"xlsx_sheet": sheet}} if sheet is not None else {}
        policy = self.effective_policy(policy_path, overrides)
        data = source.read_bytes()
        reader = reader_for(str(source), self.readers)
        meta = reader.sniff(str(source), data)
        read_result = reader.read(data, meta, policy)
        result = clean_table(
            read_result.table,
            policy,
            content_sha256=hashlib.sha256(data).hexdigest(),
            engine_version=self.engine_version,
            policy_hash=policy.policy_hash(),
            reader_issues=list(read_result.issues),
        )
        return ProfileResult(
            path=str(source.resolve()),
            format=read_result.meta.format,
            encoding=read_result.meta.encoding,
            dialect=read_result.meta.dialect(),
            n_rows=result.original.n_rows,
            n_cols=result.original.n_cols,
            columns=result.profiles,
            issues=result.issues,
            review_items=result.review_items,
            issue_counts=dict(result.issue_counts),
            change_counts=dict(result.change_counts),
        )

    # -- runs (FR-12) ------------------------------------------------------

    def get_run(self, run_id: str) -> Run:
        run = self.repo.get_run(run_id)
        if run is None:
            raise UnknownRunError(f"unknown run: {run_id}", run_id=run_id)
        return run

    def resolve_run(self, run_ref: str) -> Run:
        """Accept a full run id or an unambiguous prefix (CLI convenience)."""
        run = self.repo.get_run(run_ref)
        if run is not None:
            return run
        matches = [run for run in self.repo.list_runs() if run.id.startswith(run_ref)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise UnknownRunError(f"unknown run: {run_ref}", run_id=run_ref)
        raise DatasweepError(
            f"run reference {run_ref!r} is ambiguous ({len(matches)} matches)",
            run_id=run_ref,
        )

    def list_runs(self, *, path: str | None = None, status: RunStatus | None = None) -> list[Run]:
        resolved = str(Path(path).expanduser().resolve()) if path else None
        return self.repo.list_runs(path=resolved, status=status)

    def run_detail(self, run_id: str) -> RunDetail:
        run = self.get_run(run_id)
        return RunDetail(
            run=run,
            columns=self.repo.list_column_profiles(run.id),
            issues=self.repo.list_issue_summaries(run.id),
            revisions=self.repo.list_revisions(run.id),
            review_items=self.repo.list_review_items(run.id),
        )

    def run_summary(self, run: Run, source_name: str | None = None) -> RunSummary:
        if source_name is None:
            source = self.repo.get_source_file_by_id(run.file_id)
            source_name = Path(source.path).name if source is not None else run.file_id
        return RunSummary(
            run_id=run.id,
            source_name=source_name,
            status=run.status,
            n_rows=run.n_rows,
            n_cols=run.n_cols,
            issue_counts=run.issue_counts,
            change_counts=run.change_counts,
            artifact_dir=run.artifact_dir,
        )

    def artifact_path(self, run: Run, name: str) -> Path:
        if run.artifact_dir is None:
            raise UnknownRunError(
                f"run {run.id} produced no artifacts (status {run.status.value})", run_id=run.id
            )
        path = Path(run.artifact_dir) / name
        if not path.is_file():
            raise UnknownRunError(f"missing artifact {name} for run {run.id}", run_id=run.id)
        return path

    def artifact_paths(self, run: Run) -> dict[str, str]:
        revisions = self.repo.list_revisions(run.id)
        latest = revisions[-1] if revisions else None
        directory = run.artifact_dir
        if directory is None:
            return {}
        out = {
            "directory": directory,
            "cleaned": latest.cleaned_path if latest else "",
            "audit": latest.audit_path if latest else "",
            "findings": str(Path(directory) / "findings.jsonl"),
            "report": str(Path(directory) / "report.md"),
        }
        return {key: value for key, value in out.items() if value}

    # -- review workflow (FR-11) ------------------------------------------

    def review_items(self, run_id: str) -> list[ReviewItem]:
        self.get_run(run_id)
        return self.repo.list_review_items(run_id)

    def decide(
        self,
        run_id: str,
        *,
        accept: list[str] | None = None,
        reject: list[str] | None = None,
        now: datetime | None = None,
    ) -> Revision:
        """Record decisions and recompute a *complete* revision (FR-11)."""
        run = self.get_run(run_id)
        moment = now or self.clock.now()
        known = {item.id: item for item in self.repo.list_review_items(run_id)}
        requested = list(accept or []) + list(reject or [])
        unknown = [item_id for item_id in requested if item_id not in known]
        if unknown:
            raise UnknownItemError(
                f"unknown review item(s) for run {run_id}: {', '.join(sorted(unknown))}",
                run_id=run_id,
                item_ids=sorted(unknown),
            )
        for item_id in accept or []:
            self.repo.decide_review_item(run_id, item_id, ReviewStatus.ACCEPTED, moment)
        for item_id in reject or []:
            self.repo.decide_review_item(run_id, item_id, ReviewStatus.REJECTED, moment)

        accepted_ids = sorted(
            item.id
            for item in self.repo.list_review_items(run_id)
            if item.status is ReviewStatus.ACCEPTED
        )
        result, meta, source_path = self._recompute(run)
        cleaned, audit = apply_revision(result, accepted_ids)
        revision_no = len(self.repo.list_revisions(run_id)) + 1
        paths = self.artifacts.write(
            run.artifact_dir or "",
            cleaned=cleaned,
            audit=audit,
            findings=result.issues,
            report_md="",
            fmt=meta.format,
            header=self._audit_header(
                Path(source_path).name,
                run.content_sha256,
                run.policy_hash,
                revision_no,
                cleaned,
            ),
            revision=revision_no,
        )
        return self.repo.add_revision(
            Revision(
                id=str(uuid.uuid4()),
                run_id=run_id,
                revision_no=revision_no,
                created_at=moment,
                accepted_item_ids=accepted_ids,
                cleaned_path=paths.cleaned,
                audit_path=paths.audit,
            )
        )

    # -- reversibility (FR-9) ---------------------------------------------

    def revert_check(self, run_id: str, revision_no: int | None = None) -> RevertCheck:
        """``revert(cleaned.r<N>, audit.r<N>) == parsed original``, from disk."""
        run = self.get_run(run_id)
        revisions = self.repo.list_revisions(run_id)
        if not revisions:
            raise UnknownRunError(f"run {run_id} has no revisions", run_id=run_id)
        chosen = revisions[-1]
        if revision_no is not None:
            match = [rev for rev in revisions if rev.revision_no == revision_no]
            if not match:
                raise UnknownRunError(f"run {run_id} has no revision {revision_no}", run_id=run_id)
            chosen = match[0]

        result, meta, _ = self._recompute(run)
        cleaned = _read_table_artifact(chosen.cleaned_path, meta.format)
        audit = _read_audit_artifact(chosen.audit_path)
        rebuilt = revert(cleaned, audit)
        mismatches = _table_mismatches(result.original, rebuilt)
        return RevertCheck(
            run_id=run_id,
            revision=chosen.revision_no,
            match=not mismatches,
            mismatches=mismatches,
        )

    # -- internals ---------------------------------------------------------

    def _recompute(self, run: Run) -> tuple[Any, FileMeta, str]:
        """Re-derive a run's CleanResult from its source file.

        Deterministic by FR-16, so this is a faithful reconstruction rather
        than a second opinion: same bytes, same policy snapshot, same engine
        version ⇒ same plan.
        """
        source = self.repo.get_source_file_by_id(run.file_id)
        if source is None:  # pragma: no cover - FK guarantees this
            raise UnknownRunError(f"run {run.id} has no source file", run_id=run.id)
        path = Path(source.path)
        if not path.is_file():
            raise DatasweepError(f"source file is gone: {source.path}", path=source.path)
        current = sha256_file(path)
        if current != run.content_sha256:
            raise DatasweepError(
                f"source file changed since run {run.id}", path=source.path, run_id=run.id
            )
        policy = Policy.model_validate(run.policy_snapshot)
        data = path.read_bytes()
        reader = reader_for(str(path), self.readers)
        meta = reader.sniff(str(path), data)
        read_result = reader.read(data, meta, policy)
        result = clean_table(
            read_result.table,
            policy,
            content_sha256=run.content_sha256,
            engine_version=run.engine_version,
            policy_hash=run.policy_hash,
            reader_issues=list(read_result.issues),
        )
        return result, read_result.meta, str(path)

    def _artifact_dir(
        self,
        absolute: str,
        content_sha256: str,
        *,
        out: str | None,
        folder: WatchedFolder | None,
    ) -> str:
        if out is not None:
            base = Path(out).expanduser()
        elif folder is not None:
            base = Path(folder.output_dir)
        else:
            base = Path(absolute).parent / DEFAULT_OUTPUT_DIRNAME
        return str(base / artifact_dir_name(Path(absolute).stem, content_sha256))

    @staticmethod
    def _audit_header(
        source_name: str,
        content_sha256: str,
        policy_hash: str,
        revision: int,
        cleaned: RawTable,
    ) -> dict[str, Any]:
        """DATA_MODEL §3.1 — no run id, no timestamp, no absolute path."""
        return {
            "source_name": source_name,
            "content_sha256": content_sha256,
            "policy_hash": policy_hash,
            "engine_version": __version__,
            "revision": revision,
            "n_rows": cleaned.n_rows,
            "n_cols": cleaned.n_cols,
        }

    @staticmethod
    def _issue_summaries(run_id: str, issues: list[Issue]) -> list[IssueSummary]:
        """Roll instances up per (class, column, disposition) — DATA_MODEL §2.5."""
        disposition_of = {
            Tier.AUTO: Disposition.FIXED,
            Tier.REVIEW: Disposition.PROPOSED,
            Tier.REPORT: Disposition.REPORTED,
        }
        groups: dict[tuple[IssueClass, int | None, Disposition], list[Issue]] = defaultdict(list)
        for issue in issues:
            groups[(issue.klass, issue.col, disposition_of[issue.tier])].append(issue)

        summaries: list[IssueSummary] = []
        for (klass, col_index, disposition), members in sorted(
            groups.items(),
            key=lambda item: (
                item[0][0].value,
                -1 if item[0][1] is None else item[0][1],
                item[0][2].value,
            ),
        ):
            first = members[0]
            summaries.append(
                IssueSummary(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    klass=klass,
                    col_index=col_index,
                    cell_count=len(members),
                    disposition=disposition,
                    samples=[
                        {"row": issue.row, "before": issue.value}
                        for issue in members[:MAX_SUMMARY_SAMPLES]
                    ],
                    evidence={"rule": first.rule, **first.evidence},
                )
            )
        return summaries


__all__ = [
    "DEFAULT_OUTPUT_DIRNAME",
    "DatasweepService",
    "ProfileResult",
    "RevertCheck",
    "RunDetail",
    "ScanResult",
    "audit_filename",
    "cleaned_filename",
    "sha256_file",
]
