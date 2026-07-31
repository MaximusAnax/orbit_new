"""Orchestration between adapters, store and engine (SCOPE Architecture).

This is the only layer that touches both I/O and the pure engine.  The API
(:mod:`flowlist.api`) and the CLI (:mod:`flowlist.cli`) call *these* functions
and do nothing but parse and serialise, so a business rule can never end up
living in a request handler.

Every function that writes takes ``now`` explicitly: the engine never reads a
clock (CONVENTIONS 3) and neither does this module — callers at the process
boundary supply the timestamp.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from flowlist import ENGINE_VERSION
from flowlist.adapters.base import LocalAudioAnalyzer, MetadataProvider
from flowlist.adapters.readers import (
    CsvPlaylistReader,
    DirectoryPlaylistReader,
    JsonPlaylistReader,
)
from flowlist.adapters.writers import writer_for
from flowlist.engine.identity import track_id as make_track_id
from flowlist.engine.models import (
    CLIFF_THRESHOLD,
    DEFAULT_PRECEDENCE,
    SEAMLESS_THRESHOLD,
    Algorithm,
    ArcProfile,
    AudioFeatures,
    CoverageReport,
    ExportFormat,
    ExportRow,
    FeatureSnapshot,
    FeatureSource,
    FlowReport,
    ImportedPlaylist,
    ImportedTrack,
    ImportIssue,
    Playlist,
    PlaylistEntry,
    PlaylistExport,
    PlaylistSource,
    ReorderParams,
    ReorderRun,
    ResolvedFeatures,
    RunEntry,
    Track,
    TransitionWeights,
    round_score,
)
from flowlist.engine.optimizer import reorder as optimize
from flowlist.engine.resolution import coverage_report, fully_resolved, resolve_features
from flowlist.engine.scoring import build_matrix, score_order
from flowlist.errors import (
    InvalidAnchorError,
    NameConflictError,
    PlaylistImportError,
    UnknownPlaylistError,
    UnknownRunError,
    UnknownTrackError,
)
from flowlist.store.base import Repository

#: Injectable id source so tests and fixtures can be deterministic.
IdFactory = Callable[[], str]


def uuid_factory() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Result bundles
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImportResult:
    """What ``flowlist import`` / ``POST /playlists/import`` produced (FR-1/2)."""

    playlist: Playlist
    entries: list[PlaylistEntry]
    tracks: list[Track]
    issues: list[ImportIssue] = field(default_factory=list)
    replaced: bool = False

    @property
    def warnings(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if not issue.skipped]

    @property
    def skipped(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.skipped]


@dataclass(frozen=True)
class PlaylistView:
    """A playlist with everything a caller needs to render it (US-1)."""

    playlist: Playlist
    entries: list[PlaylistEntry]
    tracks: dict[str, Track]
    features: dict[str, ResolvedFeatures]
    coverage: CoverageReport

    def snapshots(self) -> list[FeatureSnapshot]:
        """Resolved features in entry order — the optimizer's node features."""
        return [self.features[entry.track_id].snapshot() for entry in self.entries]

    def entry_ids(self) -> list[str]:
        return [entry.id for entry in self.entries]


@dataclass(frozen=True)
class ReorderOutcome:
    """A persisted reorder plus the two reports that justify it (FR-11)."""

    run: ReorderRun
    run_entries: list[RunEntry]
    before: FlowReport
    after: FlowReport
    applied: bool = False


@dataclass(frozen=True)
class RunView:
    """A stored run rehydrated for display or export (US-4, FR-12)."""

    run: ReorderRun
    run_entries: list[RunEntry]
    playlist: Playlist
    tracks: dict[str, Track]
    entries: dict[str, PlaylistEntry]

    @property
    def applied(self) -> bool:
        return self.playlist.applied_run_id == self.run.id


# --------------------------------------------------------------------------- #
# Import (FR-1, FR-2)
# --------------------------------------------------------------------------- #

_READERS = {
    PlaylistSource.CSV: CsvPlaylistReader,
    PlaylistSource.JSON: JsonPlaylistReader,
    PlaylistSource.DIRECTORY: DirectoryPlaylistReader,
}

_SUFFIX_FORMATS = {".csv": PlaylistSource.CSV, ".json": PlaylistSource.JSON}


def detect_format(source: str) -> PlaylistSource:
    """Guess the import format from a path (FR-1's optional ``--format``)."""
    path = Path(source)
    if path.is_dir():
        return PlaylistSource.DIRECTORY
    suffix = path.suffix.lower()
    if suffix in _SUFFIX_FORMATS:
        return _SUFFIX_FORMATS[suffix]
    raise PlaylistImportError(
        f"cannot tell the playlist format of {source!r}; pass --format csv|json|dir",
        source=source,
    )


def read_playlist(
    source: str,
    *,
    name: str | None = None,
    fmt: PlaylistSource | None = None,
) -> ImportedPlaylist:
    """Parse a playlist source with the matching reader (FR-1/FR-2)."""
    resolved = fmt or detect_format(source)
    reader = _READERS[resolved]()
    return reader.read(source, name=name)


def read_playlist_content(
    content: str,
    *,
    name: str | None,
    fmt: PlaylistSource,
    source_ref: str | None = None,
) -> ImportedPlaylist:
    """Parse in-memory playlist text — the API's ``content`` body field."""
    if fmt is PlaylistSource.CSV:
        if not name:
            raise PlaylistImportError("CSV import requires a playlist name")
        return CsvPlaylistReader().read_text(content, name=name, source_ref=source_ref)
    if fmt is PlaylistSource.JSON:
        return JsonPlaylistReader().read_text(content, name=name, source_ref=source_ref)
    raise PlaylistImportError("directory import needs a path, not inline content", format=str(fmt))


def _to_track(row: ImportedTrack, *, now: datetime) -> Track:
    """Assign D9 identity to one parsed row."""
    return Track(
        id=make_track_id(
            spotify_id=row.spotify_id,
            file_sha1=row.file_sha1,
            artist=row.artist,
            title=row.title,
        ),
        title=row.title,
        artist=row.artist,
        album=row.album,
        duration_ms=row.duration_ms,
        spotify_id=row.spotify_id,
        file_path=row.file_path,
        created_at=now,
    )


def import_playlist(
    repo: Repository,
    imported: ImportedPlaylist,
    *,
    now: datetime,
    replace: bool = False,
    force: bool = False,
    id_factory: IdFactory = uuid_factory,
) -> ImportResult:
    """Persist a parsed playlist (FR-1).

    Tracks are upserted by D9 identity so re-importing the same file never
    duplicates the catalog; features carried by the file are stored as
    ``source=import``.  Importing onto an existing name is refused unless
    ``replace``; if that playlist already has runs, ``force`` is required too
    and reuses the one sanctioned deletion path (DATA_MODEL 2.3).
    """
    tracks: list[Track] = []
    for row in imported.tracks:
        track = repo.upsert_track(_to_track(row, now=now))
        tracks.append(track)
        if row.features is not None:
            repo.upsert_features(
                AudioFeatures(
                    track_id=track.id,
                    source=FeatureSource.IMPORT,
                    analyzed_at=now,
                    **row.features.model_dump(),
                )
            )

    track_ids = [track.id for track in tracks]
    entry_ids = [id_factory() for _ in track_ids]

    existing = repo.get_playlist_by_name(imported.name)
    if existing is not None:
        if not replace:
            raise NameConflictError(
                f"a playlist named {imported.name!r} already exists; "
                "pass --replace to overwrite its entries",
                name=imported.name,
                playlist_id=existing.id,
            )
        entries = repo.replace_entries(
            existing.id, track_ids=track_ids, entry_ids=entry_ids, force=force
        )
        playlist = repo.get_playlist(existing.id)
        assert playlist is not None
        return ImportResult(
            playlist=playlist,
            entries=entries,
            tracks=tracks,
            issues=list(imported.issues),
            replaced=True,
        )

    playlist = repo.create_playlist(
        playlist_id=id_factory(),
        name=imported.name,
        source=imported.source,
        source_ref=imported.source_ref,
        created_at=now,
        track_ids=track_ids,
        entry_ids=entry_ids,
    )
    return ImportResult(
        playlist=playlist,
        entries=repo.get_entries(playlist.id),
        tracks=tracks,
        issues=list(imported.issues),
    )


# --------------------------------------------------------------------------- #
# Lookup helpers
# --------------------------------------------------------------------------- #


def require_playlist(repo: Repository, ref: str) -> Playlist:
    """Resolve a playlist by id or (case-insensitive) name — CLI addressing."""
    playlist = repo.get_playlist(ref) or repo.get_playlist_by_name(ref)
    if playlist is None:
        raise UnknownPlaylistError(f"no playlist {ref!r}", playlist=ref)
    return playlist


def require_run(repo: Repository, run_id: str) -> ReorderRun:
    run = repo.get_run(run_id)
    if run is None:
        raise UnknownRunError(f"no run {run_id!r}", run_id=run_id)
    return run


def require_track(repo: Repository, track_id: str) -> Track:
    track = repo.get_track(track_id)
    if track is None:
        raise UnknownTrackError(f"no track {track_id!r}", track_id=track_id)
    return track


# --------------------------------------------------------------------------- #
# Feature resolution and analysis (FR-3, FR-4)
# --------------------------------------------------------------------------- #


def load_playlist(
    repo: Repository,
    ref: str,
    *,
    precedence: Sequence[FeatureSource] = DEFAULT_PRECEDENCE,
) -> PlaylistView:
    """Playlist + entries + tracks + resolved features + coverage."""
    playlist = require_playlist(repo, ref)
    entries = repo.get_entries(playlist.id)
    track_ids = [entry.track_id for entry in entries]
    tracks = {track.id: track for track in repo.list_tracks(track_ids)}
    rows = repo.get_features_for(sorted(set(track_ids)))
    features = {
        track_id: resolve_features(rows.get(track_id, []), precedence, track_id=track_id)
        for track_id in sorted(set(track_ids))
    }
    return PlaylistView(
        playlist=playlist,
        entries=entries,
        tracks=tracks,
        features=features,
        coverage=coverage_report(features),
    )


def analyze_playlist(
    repo: Repository,
    ref: str,
    *,
    now: datetime,
    providers: Sequence[MetadataProvider] = (),
    analyzer: LocalAudioAnalyzer | None = None,
    precedence: Sequence[FeatureSource] = DEFAULT_PRECEDENCE,
) -> CoverageReport:
    """Fill feature gaps from providers, then report coverage (FR-3).

    Idempotent by construction: a track whose coverage fields are already
    resolved is never sent to a provider, so a second ``analyze`` performs no
    new provider calls.
    """
    view = load_playlist(repo, ref, precedence=precedence)
    pending = [
        view.tracks[track_id]
        for track_id, resolved in sorted(view.features.items())
        if not fully_resolved(resolved) and track_id in view.tracks
    ]

    if pending and analyzer is not None and analyzer.available():
        for track in pending:
            if not track.file_path:
                continue
            measured = analyzer.analyze(track.file_path)
            if measured is not None:
                repo.upsert_features(
                    measured.model_copy(update={"track_id": track.id, "analyzed_at": now})
                )

    for provider in providers:
        if not pending or not provider.available():
            continue
        found = provider.get_features(pending)
        for track_id, features in found.items():
            repo.upsert_features(features.model_copy(update={"track_id": track_id}))
        pending = [track for track in pending if track.id not in found]

    return load_playlist(repo, ref, precedence=precedence).coverage


def set_manual_features(
    repo: Repository,
    track_id: str,
    *,
    now: datetime,
    bpm: float | None = None,
    key_pc: int | None = None,
    mode: int | None = None,
    energy: float | None = None,
    danceability: float | None = None,
    loudness_db: float | None = None,
    valence: float | None = None,
) -> AudioFeatures:
    """Store or merge the ``manual`` feature row (FR-4).

    The store merges field-wise, so any subset may be supplied and the rest of
    an earlier override survives.
    """
    require_track(repo, track_id)
    return repo.upsert_features(
        AudioFeatures(
            track_id=track_id,
            source=FeatureSource.MANUAL,
            bpm=bpm,
            key_pc=key_pc,
            mode=mode,
            energy=energy,
            danceability=danceability,
            loudness_db=loudness_db,
            valence=valence,
            analyzed_at=now,
        )
    )


# --------------------------------------------------------------------------- #
# Scoring and reordering (FR-7, FR-8, FR-9, FR-10, FR-11)
# --------------------------------------------------------------------------- #


def score_playlist(
    repo: Repository,
    ref: str,
    *,
    weights: TransitionWeights | None = None,
    profile: ArcProfile = ArcProfile.NEUTRAL,
    precedence: Sequence[FeatureSource] = DEFAULT_PRECEDENCE,
) -> tuple[PlaylistView, FlowReport]:
    """Score the playlist's *stored* order (FR-7)."""
    view = load_playlist(repo, ref, precedence=precedence)
    report = score_order(
        list(range(len(view.entries))),
        view.snapshots(),
        weights,
        profile,
        ids=view.entry_ids(),
    )
    return view, report


def _anchor_index(view: PlaylistView, entry_ref: str | None, label: str) -> int | None:
    """Map an entry id (or 0-based position) onto a matrix index (FR-9)."""
    if entry_ref is None:
        return None
    for index, entry in enumerate(view.entries):
        if entry.id == entry_ref:
            return index
    if entry_ref.isdigit():
        position = int(entry_ref)
        if 0 <= position < len(view.entries):
            return position
    for index, entry in enumerate(view.entries):
        if entry.track_id == entry_ref:
            return index
    raise InvalidAnchorError(
        f"{label} anchor {entry_ref!r} is not an entry of this playlist",
        anchor=entry_ref,
    )


def reorder_playlist(
    repo: Repository,
    ref: str,
    params: ReorderParams,
    *,
    now: datetime,
    precedence: Sequence[FeatureSource] = DEFAULT_PRECEDENCE,
    id_factory: IdFactory = uuid_factory,
    apply: bool = False,
) -> ReorderOutcome:
    """Reorder, score before/after, and persist an append-only run (FR-8/11).

    ``params.weights`` arrive already normalized (``ReorderParams`` normalizes
    on construction), so the run stores exactly the weights the engine used.
    """
    view = load_playlist(repo, ref, precedence=precedence)
    features = view.snapshots()
    entry_ids = view.entry_ids()

    start = _anchor_index(view, params.start_entry, "start")
    end = _anchor_index(view, params.end_entry, "end")

    matrix = build_matrix(features, params.weights, params.profile)
    result = optimize(
        matrix,
        seed=params.seed,
        start=start,
        end=end,
        max_passes=params.max_passes,
    )

    anchored = tuple(index for index in (start, end) if index is not None)
    before = score_order(
        list(range(len(features))),
        features,
        params.weights,
        params.profile,
        ids=entry_ids,
        anchored=anchored,
    )
    after = score_order(
        result.order,
        features,
        params.weights,
        params.profile,
        ids=entry_ids,
        anchored=anchored,
    )

    run_id = id_factory()
    run = ReorderRun(
        id=run_id,
        playlist_id=view.playlist.id,
        created_at=now,
        engine_version=ENGINE_VERSION,
        algorithm=Algorithm(result.algorithm),
        seed=params.seed,
        params=params,
        coverage=view.coverage,
        score_mean_before=before.mean,
        score_mean_after=after.mean,
        score_min_before=before.min_score,
        score_min_after=after.min_score,
        score_total_before=before.total,
        score_total_after=after.total,
        seamless_before=before.seamless,
        seamless_after=after.seamless,
        cliff_before=before.cliffs,
        cliff_after=after.cliffs,
    )
    run_entries = [
        RunEntry(
            run_id=run_id,
            position=position,
            entry_id=entry_ids[index],
            transition=None if position == 0 else after.transitions[position - 1],
        )
        for position, index in enumerate(result.order)
    ]
    repo.add_run(run, run_entries)

    applied = False
    if apply:
        repo.apply_run(run_id)
        applied = True

    return ReorderOutcome(
        run=run, run_entries=run_entries, before=before, after=after, applied=applied
    )


# --------------------------------------------------------------------------- #
# Runs, apply and export (FR-12)
# --------------------------------------------------------------------------- #


def load_run(repo: Repository, run_id: str) -> RunView:
    run = require_run(repo, run_id)
    run_entries = repo.get_run_entries(run_id)
    playlist = repo.get_playlist(run.playlist_id)
    if playlist is None:  # pragma: no cover - FK guarantees the parent row
        raise UnknownPlaylistError(f"no playlist {run.playlist_id!r}", playlist_id=run.playlist_id)
    entries = {entry.id: entry for entry in repo.get_entries(run.playlist_id)}
    track_ids = [entries[e.entry_id].track_id for e in run_entries if e.entry_id in entries]
    tracks = {track.id: track for track in repo.list_tracks(sorted(set(track_ids)))}
    return RunView(
        run=run, run_entries=run_entries, playlist=playlist, tracks=tracks, entries=entries
    )


def apply_run(repo: Repository, run_id: str) -> Playlist:
    """Rewrite the playlist's entry positions to the run's order (FR-12)."""
    require_run(repo, run_id)
    return repo.apply_run(run_id)


def build_export(view: RunView) -> PlaylistExport:
    """Assemble the writer payload from the run's *stored* breakdowns.

    Features come from the transitions' snapshots (DATA_MODEL 2.6), so an
    export reproduces the numbers the run was scored with even if the catalog
    has been re-analysed since.
    """
    rows: list[ExportRow] = []
    for position, run_entry in enumerate(view.run_entries):
        entry = view.entries.get(run_entry.entry_id)
        if entry is None:  # pragma: no cover - run entries are FK-checked
            continue
        track = view.tracks.get(entry.track_id)
        if track is None:  # pragma: no cover - tracks are never deleted
            continue
        if run_entry.transition is not None:
            features: FeatureSnapshot | None = run_entry.transition.features_to
        elif len(view.run_entries) > 1 and view.run_entries[1].transition is not None:
            features = view.run_entries[1].transition.features_from
        else:
            features = None
        rows.append(
            ExportRow(
                position=position,
                entry_id=run_entry.entry_id,
                track=track,
                features=features,
                transition=run_entry.transition,
            )
        )
    return PlaylistExport(playlist_name=view.playlist.name, run=view.run, rows=rows)


def render_export(repo: Repository, run_id: str, fmt: ExportFormat | str) -> str:
    """Render a run's ordering in one of the FR-12 formats."""
    return writer_for(fmt).render(build_export(load_run(repo, run_id)))


def write_export(repo: Repository, run_id: str, fmt: ExportFormat | str, path: str) -> str:
    """Render and write; returns the path written."""
    return writer_for(fmt).write(build_export(load_run(repo, run_id)), path)


def delete_playlist(repo: Repository, ref: str, *, force: bool = False) -> None:
    playlist = require_playlist(repo, ref)
    repo.delete_playlist(playlist.id, force=force)


def rebuild_after_report(view: RunView) -> FlowReport:
    """Recompute a run's ``after`` aggregates from its stored breakdowns.

    Used by M7's consistency check and by ``flowlist compare``: the aggregates
    on a run must be derivable from the per-transition JSON it stored.
    """
    transitions = [entry.transition for entry in view.run_entries if entry.transition is not None]
    scores = [t.score for t in transitions]
    total = round_score(sum(scores))
    return FlowReport(
        order=[entry.entry_id for entry in view.run_entries],
        transitions=transitions,
        total=total,
        mean=round_score(total / len(scores)) if scores else 0.0,
        min_score=min(scores) if scores else 0.0,
        seamless=sum(1 for s in scores if s >= SEAMLESS_THRESHOLD),
        cliffs=sum(1 for s in scores if s < CLIFF_THRESHOLD),
    )


def default_providers(catalog: str | Path | None) -> list[MetadataProvider]:
    """Offline provider chain for ``analyze`` (CONVENTIONS 3: offline default)."""
    from flowlist.adapters.metadata import FixtureMetadataProvider

    if catalog is None:
        return []
    return [FixtureMetadataProvider.from_path(catalog, source=FeatureSource.STREAMING)]


def issues_as_dicts(issues: Iterable[ImportIssue]) -> list[dict[str, object]]:
    return [issue.model_dump() for issue in issues]


__all__ = [
    "ImportResult",
    "PlaylistView",
    "ReorderOutcome",
    "RunView",
    "analyze_playlist",
    "apply_run",
    "build_export",
    "default_providers",
    "delete_playlist",
    "detect_format",
    "import_playlist",
    "issues_as_dicts",
    "load_playlist",
    "load_run",
    "read_playlist",
    "read_playlist_content",
    "rebuild_after_report",
    "render_export",
    "reorder_playlist",
    "require_playlist",
    "require_run",
    "require_track",
    "score_playlist",
    "set_manual_features",
    "uuid_factory",
    "write_export",
]
