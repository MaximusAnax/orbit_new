"""Adapter contracts: readers (FR-1/2), providers (FR-3), writers (FR-12)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from flowlist.adapters.analyzer import (
    KK_MAJOR,
    KK_MINOR,
    FixtureLocalAnalyzer,
    LibrosaLocalAnalyzer,
    estimate_key,
)
from flowlist.adapters.base import (
    LocalAudioAnalyzer,
    MetadataProvider,
    PlaylistReader,
    PlaylistWriter,
)
from flowlist.adapters.metadata import (
    ChainedMetadataProvider,
    FixtureMetadataProvider,
    SpotifyMetadataProvider,
)
from flowlist.adapters.readers import (
    AUDIO_EXTENSIONS,
    CsvPlaylistReader,
    DirectoryPlaylistReader,
    JsonPlaylistReader,
    read_tags,
    sha1_of_file,
)
from flowlist.adapters.writers import EXPORT_COLUMNS, writer_for
from flowlist.engine.models import (
    ExportFormat,
    ExportRow,
    FeatureSnapshot,
    FeatureSource,
    PlaylistExport,
    PlaylistSource,
)
from flowlist.engine.scoring import transition_score
from flowlist.errors import AdapterUnavailableError, PlaylistImportError
from flowlist_testkit import make_track

EXPORTIFY_HEADER = (
    "Track URI,Track Name,Artist Name(s),Album Name,Duration (ms),"
    "Tempo,Key,Mode,Energy,Danceability,Loudness\n"
)


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #


def test_offline_implementations_satisfy_their_protocols() -> None:
    assert isinstance(FixtureMetadataProvider(), MetadataProvider)
    assert isinstance(FixtureLocalAnalyzer(), LocalAudioAnalyzer)
    assert isinstance(LibrosaLocalAnalyzer(), LocalAudioAnalyzer)
    assert isinstance(CsvPlaylistReader(), PlaylistReader)
    assert isinstance(JsonPlaylistReader(), PlaylistReader)
    assert isinstance(DirectoryPlaylistReader(), PlaylistReader)
    for fmt in ExportFormat:
        assert isinstance(writer_for(fmt), PlaylistWriter)


# --------------------------------------------------------------------------- #
# FR-1 -- CSV import
# --------------------------------------------------------------------------- #


def test_fr1_csv_exportify_layout() -> None:
    text = EXPORTIFY_HEADER + (
        "spotify:track:3n3Ppam7vgaVa1iaRUc9Lp,One More Hour,Synthetic Sun,Solar,214000,"
        "124.0,9,0,0.71,0.8,-7.2\n"
    )
    playlist = CsvPlaylistReader().read_text(text, name="party")
    assert playlist.name == "party"
    assert playlist.source is PlaylistSource.CSV
    assert len(playlist.tracks) == 1
    track = playlist.tracks[0]
    assert track.title == "One More Hour"
    assert track.artist == "Synthetic Sun"
    assert track.album == "Solar"
    assert track.duration_ms == 214000
    assert track.spotify_id == "3n3Ppam7vgaVa1iaRUc9Lp"
    assert track.features == FeatureSnapshot(
        bpm=124.0, key_pc=9, mode=0, energy=0.71, danceability=0.8, loudness_db=-7.2
    )
    assert playlist.issues == []


def test_fr1_key_minus_one_sentinel_nulls_key_and_mode_with_a_warning() -> None:
    """FR-1/D10: 'no key detected' must not lose the whole track."""
    text = EXPORTIFY_HEADER + ",Night Drive,Vera Lux,,231000,122.0,-1,1,0.64,0.71,-8.1\n"
    playlist = CsvPlaylistReader().read_text(text, name="p")
    track = playlist.tracks[0]
    assert track.title == "Night Drive"  # still imported
    assert track.features is not None
    assert track.features.key_pc is None
    assert track.features.mode is None  # nulled alongside (2.2)
    assert track.features.bpm == 122.0  # remaining fields intact
    warnings = [i for i in playlist.issues if i.code == "key_sentinel"]
    assert len(warnings) == 1
    assert warnings[0].line == 2
    assert playlist.skipped == []


@pytest.mark.parametrize("tempo", ["0", "0.0", "12", "999"])
def test_fr1_tempo_sentinel_and_out_of_range_null_the_bpm(tempo: str) -> None:
    text = EXPORTIFY_HEADER + f",Night Drive,Vera Lux,,231000,{tempo},4,0,0.64,0.71,-8.1\n"
    playlist = CsvPlaylistReader().read_text(text, name="p")
    track = playlist.tracks[0]
    assert track.features is not None
    assert track.features.bpm is None
    assert track.features.key_pc == 4  # everything else survives
    assert any(i.code in ("bpm_sentinel", "bpm_out_of_range") for i in playlist.issues)
    assert playlist.skipped == []


def test_fr1_malformed_rows_are_skipped_with_line_numbers() -> None:
    text = EXPORTIFY_HEADER + (
        ",,Vera Lux,,231000,122.0,4,0,0.64,0.71,-8.1\n"  # no track name -> skipped
        ",Night Drive,Vera Lux,,231000,122.0,4,0,0.64,0.71,-8.1\n"
    )
    playlist = CsvPlaylistReader().read_text(text, name="p")
    assert len(playlist.tracks) == 1
    assert len(playlist.skipped) == 1
    assert playlist.skipped[0].line == 2
    assert playlist.skipped[0].code == "malformed_row"


def test_fr1_missing_features_are_not_malformed() -> None:
    """'malformed -> skip' is reserved for structurally unparsable rows."""
    text = "Track Name,Artist Name(s)\nNight Drive,Vera Lux\n"
    playlist = CsvPlaylistReader().read_text(text, name="p")
    assert len(playlist.tracks) == 1
    assert playlist.tracks[0].features is None
    assert playlist.issues == []


def test_fr1_csv_column_lookup_is_case_and_alias_tolerant() -> None:
    text = "track name,artist,tempo\nNight Drive,Vera Lux,124\n"
    playlist = CsvPlaylistReader().read_text(text, name="p")
    assert playlist.tracks[0].artist == "Vera Lux"
    assert playlist.tracks[0].features is not None
    assert playlist.tracks[0].features.bpm == 124.0


def test_fr1_csv_blank_lines_ignored() -> None:
    text = EXPORTIFY_HEADER + "\n,Night Drive,Vera Lux,,,,,,,,\n\n"
    playlist = CsvPlaylistReader().read_text(text, name="p")
    assert len(playlist.tracks) == 1
    assert playlist.issues == []


def test_fr1_csv_rejects_unusable_files() -> None:
    with pytest.raises(PlaylistImportError):
        CsvPlaylistReader().read_text("", name="p")
    with pytest.raises(PlaylistImportError):
        CsvPlaylistReader().read_text("a,b,c\n1,2,3\n", name="p")


def test_fr1_csv_reads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "party.csv"
    path.write_text(EXPORTIFY_HEADER + ",Night Drive,Vera Lux,,,124,4,0,,,\n", encoding="utf-8")
    playlist = CsvPlaylistReader().read(str(path), name="party")
    assert playlist.source_ref == str(path)
    assert len(playlist.tracks) == 1
    with pytest.raises(PlaylistImportError):
        CsvPlaylistReader().read(str(tmp_path / "missing.csv"), name="x")


# --------------------------------------------------------------------------- #
# FR-1 -- JSON import
# --------------------------------------------------------------------------- #


def test_fr1_json_schema_from_the_data_model() -> None:
    payload = {
        "name": "party",
        "tracks": [
            {
                "title": "One More Hour",
                "artist": "Synthetic Sun",
                "spotify_id": "3n3Ppam7vgaVa1iaRUc9Lp",
                "duration_ms": 214000,
                "features": {
                    "bpm": 124.0,
                    "key_pc": 9,
                    "mode": 0,
                    "energy": 0.71,
                    "danceability": 0.8,
                    "loudness_db": -7.2,
                },
            },
            {"title": "Night Drive", "artist": "Vera Lux", "file_path": "/music/night_drive.flac"},
        ],
    }
    playlist = JsonPlaylistReader().read_text(json.dumps(payload))
    assert playlist.name == "party"
    assert playlist.source is PlaylistSource.JSON
    assert len(playlist.tracks) == 2
    assert playlist.tracks[0].features is not None
    assert playlist.tracks[0].features.bpm == 124.0
    assert playlist.tracks[1].features is None
    assert playlist.tracks[1].file_path == "/music/night_drive.flac"


def test_fr1_json_applies_the_same_sentinel_rules() -> None:
    payload = {
        "name": "p",
        "tracks": [{"title": "T", "artist": "A", "features": {"bpm": 0, "key_pc": -1, "mode": 1}}],
    }
    playlist = JsonPlaylistReader().read_text(json.dumps(payload))
    assert playlist.tracks[0].features is None  # nothing usable survived
    assert {i.code for i in playlist.issues} == {"bpm_sentinel", "key_sentinel"}


def test_fr1_json_errors() -> None:
    with pytest.raises(PlaylistImportError):
        JsonPlaylistReader().read_text("{ not json")
    with pytest.raises(PlaylistImportError):
        JsonPlaylistReader().read_text('{"tracks": []}')  # no name anywhere
    with pytest.raises(PlaylistImportError):
        JsonPlaylistReader().read_text('{"name": "p"}')
    playlist = JsonPlaylistReader().read_text('{"tracks": [{"artist": "A"}]}', name="p")
    assert playlist.tracks == []
    assert playlist.skipped[0].code == "malformed_row"


# --------------------------------------------------------------------------- #
# FR-2 -- directory import
# --------------------------------------------------------------------------- #


def test_fr2_directory_scan(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "Vera Lux - Night Drive.flac").write_bytes(b"flacdata")
    (tmp_path / "sub" / "Synthetic Sun - One More Hour.mp3").write_bytes(b"mp3data")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    playlist = DirectoryPlaylistReader().read(str(tmp_path), name="gym")
    assert playlist.name == "gym"
    assert playlist.source is PlaylistSource.DIRECTORY
    assert len(playlist.tracks) == 2
    # Lexicographic path order.
    assert [t.title for t in playlist.tracks] == ["Night Drive", "One More Hour"]
    assert [t.artist for t in playlist.tracks] == ["Vera Lux", "Synthetic Sun"]
    assert all(t.file_sha1 and t.file_path for t in playlist.tracks)
    # No audio decoding at import time: no features are produced.
    assert all(t.features is None for t in playlist.tracks)


def test_fr2_filename_fallback_when_tags_are_absent(tmp_path: Path) -> None:
    (tmp_path / "night_drive.wav").write_bytes(b"wavdata")
    playlist = DirectoryPlaylistReader().read(str(tmp_path))
    assert playlist.tracks[0].title == "night_drive"
    assert playlist.tracks[0].artist == ""
    assert any(i.code == "filename_fallback" for i in playlist.issues)


def test_fr2_default_name_is_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "gym"
    folder.mkdir()
    (folder / "a - b.mp3").write_bytes(b"x")
    assert DirectoryPlaylistReader().read(str(folder)).name == "gym"


def test_fr2_extensions() -> None:
    assert AUDIO_EXTENSIONS == (".mp3", ".m4a", ".flac", ".ogg", ".wav")


def test_fr2_rejects_non_directories(tmp_path: Path) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"x")
    with pytest.raises(PlaylistImportError):
        DirectoryPlaylistReader().read(str(path))


def test_fr2_file_sha1_is_the_content_hash(tmp_path: Path) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"hello")
    # sha1("hello")
    assert sha1_of_file(path) == "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"


def test_fr2_tag_reading_degrades_without_mutagen(tmp_path: Path) -> None:
    """mutagen is optional here: absence falls back to the filename (FR-2)."""
    path = tmp_path / "x.mp3"
    path.write_bytes(b"not really an mp3")
    assert read_tags(path) == (None, None, None, None)


# --------------------------------------------------------------------------- #
# FR-3 -- metadata providers
# --------------------------------------------------------------------------- #


def test_fr3_fixture_provider_serves_a_catalog() -> None:
    provider = FixtureMetadataProvider(
        {"meta:a": {"bpm": 124.0, "key_pc": 9, "mode": 0, "energy": 0.7, "loudness_db": -7.0}}
    )
    tracks = [make_track("meta:a"), make_track("meta:b")]
    found = provider.get_features(tracks)
    assert set(found) == {"meta:a"}  # unknown tracks are simply absent (D10)
    assert found["meta:a"].source is FeatureSource.FIXTURE
    assert found["meta:a"].bpm == 124.0
    assert provider.available()


def test_fr3_fixture_provider_is_deterministic_and_clock_free() -> None:
    provider = FixtureMetadataProvider({"meta:a": {"bpm": 124.0}})
    first = provider.get_features([make_track("meta:a")])
    second = provider.get_features([make_track("meta:a")])
    assert first == second
    assert first["meta:a"].analyzed_at == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        {"meta:a": {"bpm": 124.0}},
        {"meta:a": {"features": {"bpm": 124.0}}},
        [{"id": "meta:a", "features": {"bpm": 124.0}}],
        {"tracks": [{"id": "meta:a", "bpm": 124.0}]},
    ],
)
def test_fr3_fixture_catalog_shapes(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    provider = FixtureMetadataProvider.from_path(path)
    assert provider.get_features([make_track("meta:a")])["meta:a"].bpm == 124.0


def test_fr3_fixture_provider_can_pose_as_another_source() -> None:
    provider = FixtureMetadataProvider({"meta:a": {"bpm": 124.0}}, source=FeatureSource.STREAMING)
    assert provider.name == "streaming"
    assert provider.get_features([make_track("meta:a")])["meta:a"].source is (
        FeatureSource.STREAMING
    )


def test_fr3_chained_provider_stops_at_the_first_hit() -> None:
    first = FixtureMetadataProvider({"meta:a": {"bpm": 100.0}}, source=FeatureSource.MANUAL)
    second = FixtureMetadataProvider({"meta:a": {"bpm": 200.0}, "meta:b": {"bpm": 90.0}})
    chained = ChainedMetadataProvider([first, second])
    found = chained.get_features([make_track("meta:a"), make_track("meta:b")])
    assert found["meta:a"].bpm == 100.0
    assert found["meta:b"].bpm == 90.0


def test_live_spotify_provider_is_credential_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    provider = SpotifyMetadataProvider()
    assert provider.available() is False
    with pytest.raises(AdapterUnavailableError):
        provider.get_features([make_track("spotify:x", spotify_id="x")])

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    assert SpotifyMetadataProvider().available() is True


def test_live_spotify_translation_applies_the_sentinel_rules() -> None:
    """No network needed: the vocabulary mapping is pure (D1, FR-1)."""
    translated = SpotifyMetadataProvider._translate(
        {"tempo": 124.0, "key": 9, "mode": 0, "energy": 0.7, "loudness": -7.2, "valence": 0.4}
    )
    assert translated["bpm"] == 124.0
    assert (translated["key_pc"], translated["mode"]) == (9, 0)

    sentinel = SpotifyMetadataProvider._translate({"tempo": 0.0, "key": -1, "mode": 1})
    assert sentinel["bpm"] is None
    assert sentinel["key_pc"] is None and sentinel["mode"] is None

    clipped = SpotifyMetadataProvider._translate(
        {"tempo": 300.0, "key": 0, "mode": 1, "loudness": 4.0}
    )
    assert clipped["bpm"] is None
    assert clipped["loudness_db"] is None


def test_live_spotify_never_touches_the_network_without_use() -> None:
    """Constructing the live adapter must be free of side effects."""
    provider = SpotifyMetadataProvider(client_id="a", client_secret="b")
    assert provider.available()
    assert provider.batch_size == 100


# --------------------------------------------------------------------------- #
# US-6 -- local analysis
# --------------------------------------------------------------------------- #


def test_us6_fixture_analyzer_keys_on_basename(tmp_path: Path) -> None:
    analyzer = FixtureLocalAnalyzer(
        {"night_drive.flac": {"track_id": "file:abc", "bpm": 123.9, "confidence": 0.83}}
    )
    result = analyzer.analyze("/music/night_drive.flac")
    assert result is not None
    assert result.source is FeatureSource.LOCAL_ANALYSIS
    assert result.bpm == 123.9
    assert result.confidence == 0.83
    assert analyzer.analyze("/music/other.flac") is None


def test_us6_krumhansl_schmuckler_recovers_a_planted_key() -> None:
    """A chroma histogram shaped like the C-major profile must resolve to C major."""
    chroma = list(KK_MAJOR)
    assert estimate_key(chroma) == (0, 1, pytest.approx(1.0))
    # Rotate the profile up a fifth: G major.
    rotated = [KK_MAJOR[(i - 7) % 12] for i in range(12)]
    pc, mode, correlation = estimate_key(rotated)
    assert (pc, mode) == (7, 1)
    assert correlation == pytest.approx(1.0)
    # A minor profile resolves to the minor mode.
    minor = [KK_MINOR[(i - 9) % 12] for i in range(12)]
    assert estimate_key(minor)[:2] == (9, 0)


def test_us6_key_estimation_validates_its_input() -> None:
    with pytest.raises(ValueError):
        estimate_key([1.0] * 11)


def test_us6_flat_chroma_does_not_crash() -> None:
    pc, mode, correlation = estimate_key([1.0] * 12)
    assert 0 <= pc <= 11
    assert mode in (0, 1)
    assert correlation == 0.0


def test_us6_librosa_analyzer_explains_the_missing_extra() -> None:
    analyzer = LibrosaLocalAnalyzer()
    if analyzer.available():  # pragma: no cover - only with the audio extra
        pytest.skip("librosa installed; the missing-extra path cannot be exercised")
    with pytest.raises(AdapterUnavailableError) as excinfo:
        analyzer.analyze("/music/night_drive.flac")
    assert "audio" in str(excinfo.value)
    assert excinfo.value.code == "adapter_unavailable"


# --------------------------------------------------------------------------- #
# FR-12 -- writers
# --------------------------------------------------------------------------- #


def export_fixture() -> PlaylistExport:
    a = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.71, loudness_db=-7.2)
    b = FeatureSnapshot(bpm=126.5, key_pc=4, mode=0, energy=0.76, loudness_db=-6.4)
    return PlaylistExport(
        playlist_name="party",
        rows=[
            ExportRow(
                position=0,
                entry_id="e0",
                track=make_track(
                    "spotify:abc",
                    title="One More Hour",
                    artist="Synthetic Sun",
                    album="Solar",
                    duration_ms=214000,
                    spotify_id="abc",
                ),
                features=a,
            ),
            ExportRow(
                position=1,
                entry_id="e1",
                track=make_track(
                    "file:def",
                    title="Night Drive",
                    artist="Vera Lux",
                    duration_ms=231000,
                    file_path="/music/night_drive.flac",
                ),
                features=b,
                transition=transition_score(a, b),
            ),
        ],
    )


def test_fr12_m3u_export() -> None:
    text = writer_for("m3u").render(export_fixture())
    lines = text.splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXTINF:214,Synthetic Sun - One More Hour"
    assert lines[2] == "# no local file: Synthetic Sun - One More Hour"
    assert lines[3] == "#EXTINF:231,Vera Lux - Night Drive"
    assert lines[4] == "/music/night_drive.flac"


def test_fr12_m3u_handles_unknown_durations() -> None:
    export = export_fixture()
    rows = [export.rows[0].model_copy(update={"track": make_track("meta:x")})]
    text = writer_for(ExportFormat.M3U).render(export.model_copy(update={"rows": rows}))
    assert "#EXTINF:-1," in text


def test_fr12_csv_export_columns() -> None:
    text = writer_for("csv").render(export_fixture())
    rows = text.splitlines()
    assert rows[0] == ",".join(EXPORT_COLUMNS)
    assert rows[1].startswith("spotify:track:abc,One More Hour,Synthetic Sun,Solar,214000")
    assert rows[1].endswith(",0,,")  # position 0 has no incoming transition
    assert "adjacent fifth/fourth" in rows[2]


def test_fr12_json_export_is_the_run_payload() -> None:
    payload = json.loads(writer_for("json").render(export_fixture()))
    assert payload["playlist_name"] == "party"
    assert payload["run"] is None
    assert [e["position"] for e in payload["entries"]] == [0, 1]
    assert payload["entries"][0]["transition"] is None
    assert payload["entries"][1]["transition"]["key_relation"] == "adjacent_fifth"
    assert payload["entries"][1]["track"]["file_path"] == "/music/night_drive.flac"


def test_fr12_writers_write_files(tmp_path: Path) -> None:
    export = export_fixture()
    for fmt in ExportFormat:
        writer = writer_for(fmt)
        path = str(tmp_path / f"out.{fmt.value}")
        assert writer.write(export, path) == path
        assert Path(path).read_text(encoding="utf-8") == writer.render(export)


def test_fr12_unknown_format_rejected() -> None:
    with pytest.raises(ValueError):
        writer_for("xspf")
