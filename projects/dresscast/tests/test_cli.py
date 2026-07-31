"""FR-18: the Typer CLI — exit codes, ``--json``, ``--units f``, and the
commands SCOPE.md §Architecture-CLI sketches, driven through CliRunner.

Each invocation opens a real SQLite database under ``tmp_path`` and reads
weather from a fixture directory, so the tests exercise the same wiring the
installed ``dresscast`` entry point uses — no wall-clock dependence except the
explicitly-tested "``--date`` defaults to today" edge, which is avoided by
always passing ``--date``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import diurnal, make_forecast, small_wardrobe
from dresscast.adapters.extractor import photo_sha256
from dresscast.cli.main import app
from dresscast.services import build_service
from typer.testing import CliRunner

runner = CliRunner()

DATE = "2026-04-14"
NEXT = "2026-04-15"
NOW = datetime(2026, 4, 14, 6, 30, tzinfo=UTC)

COMMANDS = (
    "add",
    "ls",
    "show",
    "edit",
    "suggest",
    "forecast",
    "brief",
    "outfit",
    "explain",
    "wear",
    "laundry",
    "history",
    "serve",
)


def _write_weather(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for date in (DATE, NEXT):
        forecast = make_forecast(date=date, temps=diurnal(5.0, 18.0), wind=10.0, humidity=55.0)
        payload = {
            "date": date,
            "location_name": "home",
            "lat": 40.71,
            "lon": -74.01,
            "timezone": "America/New_York",
            "hours": [h.model_dump() for h in forecast.hours],
        }
        (directory / f"{date}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A data directory with weather fixtures and an empty database."""
    _write_weather(tmp_path / "weather")
    return tmp_path


@pytest.fixture()
def stocked(home: Path) -> Path:
    """The same directory with the shared synthetic wardrobe already stored."""
    service = build_service(db=home / "dresscast.db", data_dir=home)
    try:
        for garment in service.repo.list_garments() or []:  # pragma: no cover - fresh db
            raise AssertionError(f"unexpected garment {garment.name}")
        for garment in small_wardrobe():
            service.repo.add_garment(garment)
    finally:
        service.close()
    return home


def invoke(home: Path, *args: str):
    return runner.invoke(
        app, ["--db", str(home / "dresscast.db"), "--data-dir", str(home), *args]
    )


# -- help and exit codes ----------------------------------------------------


def test_fr18_help_lists_every_scoped_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.stdout, f"{command} missing from --help"


def test_fr18_no_arguments_shows_help_not_a_traceback() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_fr18_bad_units_exit_nonzero(home: Path) -> None:
    result = invoke(home, "--units", "kelvin", "ls")
    assert result.exit_code != 0


# -- wardrobe (FR-1) --------------------------------------------------------


def test_fr1_us1_add_defaults_from_the_preset_then_ls_shows_it(home: Path) -> None:
    result = invoke(
        home,
        "add",
        "--name",
        "navy-wool-coat",
        "--category",
        "wool_coat",
        "--colors",
        "navy",
        "--occasions",
        "work,casual",
    )
    assert result.exit_code == 0, result.stderr
    assert "navy-wool-coat" in result.stdout

    shown = invoke(home, "--json", "show", "navy-wool-coat")
    assert shown.exit_code == 0
    record = json.loads(shown.stdout)
    assert record["clo"] == 0.6  # D1 preset, not user-supplied
    assert record["layer_role"] == "outer"
    assert record["overridden_fields"] == []

    listing = invoke(home, "ls")
    assert listing.exit_code == 0
    assert "navy-wool-coat" in listing.stdout


def test_fr1_add_unknown_category_exits_nonzero(home: Path) -> None:
    result = invoke(
        home, "add", "--name", "x", "--category", "cape", "--colors", "black"
    )
    assert result.exit_code == 1
    assert "invalid_params" in result.stderr


def test_fr1_edit_records_the_override(home: Path) -> None:
    invoke(
        home,
        "add",
        "--name",
        "gray-fleece",
        "--category",
        "fleece",
        "--colors",
        "gray",
        "--occasions",
        "casual",
    )
    result = invoke(home, "--json", "edit", "gray-fleece", "--clo", "0.35")
    assert result.exit_code == 0
    record = json.loads(result.stdout)
    assert record["clo"] == 0.35
    assert "clo" in record["overridden_fields"]


def test_fr18_show_unknown_garment_exits_nonzero(home: Path) -> None:
    result = invoke(home, "show", "not-a-garment")
    assert result.exit_code == 1
    assert "unknown_garment" in result.stderr


# -- suggestions (FR-2, US-2) -----------------------------------------------


def _add_with_photo(home: Path, photo: Path) -> None:
    photo.write_bytes(b"not-really-a-jpeg")
    result = invoke(
        home,
        "add",
        "--name",
        "mystery-fleece",
        "--category",
        "fleece",
        "--colors",
        "olive",
        "--occasions",
        "casual",
        "--photo",
        str(photo),
    )
    assert result.exit_code == 0, result.stderr


def test_fr2_us2_suggest_without_extractor_says_so_and_exits_nonzero(
    home: Path, tmp_path: Path
) -> None:
    _add_with_photo(home, tmp_path / "photo.jpg")
    result = invoke(home, "suggest", "mystery-fleece")
    assert result.exit_code == 1
    assert "no_extractor_configured" in result.stderr

    # And the garment is untouched (FR-2: suggestions never mutate implicitly).
    shown = json.loads(invoke(home, "--json", "show", "mystery-fleece").stdout)
    assert shown["category"] == "fleece"


def test_fr2_us2_suggest_accept_applies_the_category_cascade(
    home: Path, tmp_path: Path
) -> None:
    photo = tmp_path / "photo.jpg"
    _add_with_photo(home, photo)
    mapping = {
        photo_sha256(photo): {
            "category": {"value": "wool_coat", "confidence": 0.91},
        }
    }
    (home / "suggestions.json").write_text(json.dumps(mapping), encoding="utf-8")

    pending = invoke(home, "suggest", "mystery-fleece")
    assert pending.exit_code == 0
    assert "wool_coat" in pending.stdout  # proposed, not yet applied
    shown = json.loads(invoke(home, "--json", "show", "mystery-fleece").stdout)
    assert shown["category"] == "fleece"

    accepted = invoke(home, "--json", "suggest", "mystery-fleece", "--accept")
    assert accepted.exit_code == 0
    payload = json.loads(accepted.stdout)
    garment = payload["garment"]
    assert garment["category"] == "wool_coat"
    assert garment["clo"] == 0.6  # cascade re-derived from the new preset
    assert garment["layer_role"] == "outer"
    vias = {e["field"]: e["via"] for e in payload["suggestion"]["accepted_fields"]}
    assert vias["category"] == "explicit"
    assert vias["clo"] == "cascade"


# -- weather and brief (FR-4, FR-16) ----------------------------------------


def test_fr4_forecast_snapshots_and_prints_the_day(home: Path) -> None:
    result = invoke(home, "forecast", "--date", DATE)
    assert result.exit_code == 0
    assert "24 hours" in result.stdout

    as_json = invoke(home, "--json", "forecast", "--date", DATE)
    snapshot = json.loads(as_json.stdout)
    assert snapshot["date"] == DATE
    assert len(snapshot["hours"]) == 24


def test_fr16_us9_brief_works_on_an_empty_database(home: Path) -> None:
    result = invoke(home, "brief", "--date", DATE)
    assert result.exit_code == 0
    assert "clo" in result.stdout

    as_json = invoke(home, "--json", "brief", "--date", DATE)
    brief = json.loads(as_json.stdout)
    assert brief["date"] == DATE
    assert len(brief["hours"]) == 15  # default wear window 07:00-22:00


def test_fr18_units_f_is_a_presentation_conversion_only(home: Path) -> None:
    celsius = invoke(home, "brief", "--date", DATE)
    fahrenheit = invoke(home, "--units", "f", "brief", "--date", DATE)
    assert "°F" not in celsius.stdout

    # Every hour row the CLI itself formats converts; the engine's stored
    # reasoning strings are displayed verbatim (FR-15 byte-stability).
    def hour_rows(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.lstrip()[:5].endswith(":00")]

    assert hour_rows(celsius.stdout) and all("°C" in r for r in hour_rows(celsius.stdout))
    f_rows = hour_rows(fahrenheit.stdout)
    assert f_rows and all("°F" in r and "°C" not in r for r in f_rows)

    # The stored numbers stay SI regardless of the display flag (D15).
    as_json = invoke(home, "--units", "f", "--json", "brief", "--date", DATE)
    brief = json.loads(as_json.stdout)
    assert all(-60.0 <= h["bare_feels_c"] <= 60.0 for h in brief["hours"])


def test_fr4_missing_fixture_date_exits_nonzero(home: Path) -> None:
    result = invoke(home, "forecast", "--date", "2031-01-01")
    assert result.exit_code == 1
    assert "forecast_unavailable" in result.stderr


# -- outfits (FR-8, FR-14, FR-15) -------------------------------------------


def test_fr8_us3_outfit_prints_ranked_outfits_with_reasoning(stocked: Path) -> None:
    result = invoke(
        stocked, "outfit", "--date", DATE, "--occasion", "work", "--k", "3", "--plan"
    )
    assert result.exit_code == 0, result.stderr
    for rank in ("#1", "#2", "#3"):
        assert rank in result.stdout

    as_json = invoke(
        stocked, "--json", "outfit", "--date", DATE, "--occasion", "work", "--k", "3"
    )
    rec = json.loads(as_json.stdout)
    assert len(rec["outfits"]) == 3
    top = rec["outfits"][0]
    assert top["rank"] == 1
    assert top["hour_plan"] and top["reasoning"]
    classes = {line["line_class"] for line in top["reasoning"]}
    assert {"day_thermal", "palette", "variety"} <= classes  # FR-15 always-on lines


def test_fr8_outfit_reruns_pick_identical_outfits(stocked: Path) -> None:
    first = json.loads(
        invoke(stocked, "--json", "outfit", "--date", DATE, "--occasion", "work").stdout
    )
    second = json.loads(
        invoke(stocked, "--json", "outfit", "--date", DATE, "--occasion", "work").stdout
    )

    def cores(rec: dict) -> list[tuple[str, ...]]:
        return [
            tuple(sorted(i["garment_id"] for i in o["items"]))
            for o in rec["outfits"]
        ]

    assert cores(first) == cores(second)
    assert [o["score_total"] for o in first["outfits"]] == [
        o["score_total"] for o in second["outfits"]
    ]


def test_fr14_outfit_on_an_empty_wardrobe_fails_with_the_brief_payload(
    home: Path,
) -> None:
    result = invoke(home, "outfit", "--date", DATE, "--occasion", "work")
    assert result.exit_code == 1
    assert "infeasible_wardrobe" in result.stderr
    assert "brief" in result.stderr  # FR-14: the FR-16 day brief rides along


def test_fr15_explain_latest_prints_stored_reasoning(stocked: Path) -> None:
    invoke(stocked, "outfit", "--date", DATE, "--occasion", "work")
    result = invoke(stocked, "explain", "latest", "--rank", "1")
    assert result.exit_code == 0
    assert "day_thermal" in result.stdout or "Feels" in result.stdout

    missing = invoke(stocked, "explain", "latest", "--rank", "9")
    assert missing.exit_code == 1


# -- wear and laundry (FR-12, FR-3, FR-11) ----------------------------------


def test_fr12_us7_wear_then_laundry_round_trip(stocked: Path) -> None:
    worn = invoke(
        stocked, "--json", "wear", "--items", "white-oxford-shirt", "--date", "2026-04-12"
    )
    assert worn.exit_code == 0, worn.stderr
    again = invoke(
        stocked, "--json", "wear", "--items", "white-oxford-shirt", "--date", "2026-04-13"
    )
    assert again.exit_code == 0

    dirty = invoke(stocked, "--json", "ls", "--status", "dirty")
    names = [g["name"] for g in json.loads(dirty.stdout)]
    assert names == ["white-oxford-shirt"]  # threshold 2 reached (US-7)

    washed = invoke(stocked, "laundry", "--all")
    assert washed.exit_code == 0
    assert "white-oxford-shirt" in washed.stdout
    assert json.loads(invoke(stocked, "--json", "ls", "--status", "dirty").stdout) == []


def test_fr3_laundry_with_nothing_dirty_exits_nonzero(stocked: Path) -> None:
    result = invoke(stocked, "laundry", "--all")
    assert result.exit_code == 1
    assert "invalid_params" in result.stderr


def test_fr12_wear_undo_on_the_same_day(stocked: Path) -> None:
    today = datetime.now(UTC).date().isoformat()
    worn = invoke(stocked, "--json", "wear", "--items", "dark-jeans", "--date", today)
    assert worn.exit_code == 0
    log_id = json.loads(worn.stdout)["id"]

    undone = invoke(stocked, "wear", "--undo", log_id, "--date", today)
    assert undone.exit_code == 0, undone.stderr
    assert json.loads(invoke(stocked, "--json", "history", "--until", today).stdout) == []


def test_fr11_history_lists_recent_wear(stocked: Path) -> None:
    invoke(stocked, "wear", "--items", "white-tee,dark-jeans", "--date", DATE)
    result = invoke(stocked, "history", "--days", "14", "--until", NEXT)
    assert result.exit_code == 0
    assert "white-tee" in result.stdout

    as_json = invoke(stocked, "--json", "history", "--days", "14", "--until", NEXT)
    logs = json.loads(as_json.stdout)
    assert len(logs) == 1 and logs[0]["date"] == DATE
