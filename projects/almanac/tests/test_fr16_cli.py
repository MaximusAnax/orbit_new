"""FR-16: the Typer CLI — commands, exit codes, ``--json`` output.

These run against a real temporary SQLite database, so they also cover the
default store path end to end.
"""

from __future__ import annotations

import datetime as dt
import json

MARCUS = "You have power over your mind - not outside events."
TODAY = dt.date.today().isoformat()


def init(cli):
    result = cli("init", "--seed", "7")
    assert result.exit_code == 0, result.output
    return result


def test_fr16_init_reports_the_loaded_datasets(cli):
    output = init(cli).output
    assert "16 themes" in output
    assert "106 prompt templates" in output
    assert "seed 7" in output


def test_fr16_add_prints_suggestions_and_flags(cli):
    init(cli)
    result = cli(
        "add",
        "Insanity is doing the same thing over and over again and expecting different results.",
        "--author",
        "Albert Einstein",
        "--tag",
        "Mental Models",
        "--yes",
    )
    assert result.exit_code == 0
    assert "attribution: misattributed" in result.output
    assert "quoteinvestigator.com" in result.output
    assert "tags: mental-models" in result.output


def test_fr16_add_confirms_a_suggested_theme_interactively(cli):
    init(cli)
    result = cli("add", "Remember that you must die; the hour is already spent.", input="y\n")
    assert result.exit_code == 0
    assert "suggested themes:" in result.output
    assert "themes set: mortality_and_time" in result.output


def test_fr16_add_never_assigns_a_theme_when_the_prompt_is_declined(cli):
    init(cli)
    result = cli("add", "Remember that you must die; the hour is already spent.", input="n\n")
    assert result.exit_code == 0
    assert "themes set:" not in result.output


def test_fr16_duplicate_capture_needs_confirmation_and_exits_nonzero(cli):
    init(cli)
    cli("add", MARCUS, "--yes")
    declined = cli("add", MARCUS.upper(), input="n\n")
    assert declined.exit_code == 1
    assert "duplicate not added" in declined.output

    accepted = cli("add", MARCUS.upper(), input="y\ny\n")
    assert accepted.exit_code == 0
    assert "WARNING: same normalized text" in accepted.output


def test_fr16_today_renders_a_card_and_json(cli):
    init(cli)
    cli("import", "--starter")
    plain = cli("today")
    assert plain.exit_code == 0
    assert "PROMPT (" in plain.output
    assert "reflect with: almanac reflect --last" in plain.output

    as_json = cli("today", "--json")
    payload = json.loads(as_json.output)
    assert payload[0]["surfacing"]["kind"] == "daily"
    assert payload[0]["surfacing"]["seed"] == 7


def test_fr16_today_is_idempotent(cli):
    init(cli)
    cli("import", "--starter")
    first = cli("today", "--json").output
    second = cli("today", "--json").output
    assert first == second


def test_fr16_reflect_last_then_reflecting_again_exits_nonzero(cli):
    init(cli)
    cli("import", "--starter")
    cli("today")
    ok = cli("reflect", "--last", "--grade", "applied", "--text", "wrote the memo first")
    assert ok.exit_code == 0
    assert "logged applied" in ok.output

    again = cli("reflect", "--last", "--grade", "flat")
    assert again.exit_code == 1
    assert "already carries a reflection" in again.output


def test_fr16_reflect_without_target_exits_nonzero(cli):
    init(cli)
    result = cli("reflect", "--grade", "flat")
    assert result.exit_code == 1
    assert "SURFACING_ID" in result.output


def test_fr16_import_csv_reports_rows_and_drain_horizon(cli, tmp_path):
    init(cli)
    path = tmp_path / "rows.csv"
    path.write_text(
        "text,author,source,url,tags,note\n"
        "The first saying.,Anon,Book,,alpha;beta,note one\n"
        ",Nobody,,,,\n"
        "The second saying.,Anon,,,,\n",
        encoding="utf-8",
    )
    result = cli("import", str(path))
    assert result.exit_code == 0
    assert "created 2" in result.output
    assert "row 2:" in result.output
    assert "drain within about" in result.output


def test_fr16_import_missing_file_exits_nonzero(cli, tmp_path):
    init(cli)
    result = cli("import", str(tmp_path / "nope.csv"))
    assert result.exit_code == 1
    assert "no such file" in result.output


def test_fr16_export_writes_a_json_document(cli, tmp_path):
    init(cli)
    cli("add", MARCUS, "--yes")
    out = tmp_path / "library.json"
    assert cli("export", "--out", str(out)).exit_code == 0
    document = json.loads(out.read_text())
    assert document["format"] == "almanac-export"
    assert len(document["entries"]) == 1


def test_fr16_list_search_and_show(cli):
    init(cli)
    cli("add", MARCUS, "--author", "Marcus Aurelius", "--tag", "stoicism", "--yes")
    listing = cli("list")
    assert listing.exit_code == 0
    assert "1 entry" in listing.output

    entry_id = json.loads(cli("list", "--json").output)[0]["id"]
    assert cli("search", "outside events").exit_code == 0
    assert entry_id in cli("search", "outside events").output

    shown = cli("show", entry_id)
    assert "never surfaced" in shown.output
    assert "Marcus Aurelius" in shown.output


def test_fr16_show_unknown_entry_exits_nonzero(cli):
    init(cli)
    result = cli("show", "nope")
    assert result.exit_code == 1
    assert "unknown entry" in result.output


def test_fr16_edit_rejects_a_semantic_change_after_surfacing(cli):
    init(cli)
    cli("add", MARCUS, "--yes")
    cli("today")
    entry_id = json.loads(cli("list", "--json").output)[0]["id"]

    ok = cli("edit", entry_id, "--text", MARCUS.replace("-", "—"))
    assert ok.exit_code == 0

    bad = cli("edit", entry_id, "--text", "A completely different sentence.")
    assert bad.exit_code == 1
    assert "archive this entry and re-add it" in bad.output


def test_fr16_pin_unpin_archive_restore(cli):
    init(cli)
    cli("add", MARCUS, "--yes")
    entry_id = json.loads(cli("list", "--json").output)[0]["id"]
    assert "pinned" in cli("pin", entry_id).output
    assert json.loads(cli("list", "--json", "--pinned").output)[0]["id"] == entry_id
    cli("unpin", entry_id)
    assert cli("list", "--json", "--pinned").output.strip() == "[\n\n]"
    cli("archive", entry_id)
    assert "(no entries match)" in cli("list").output
    assert entry_id in cli("list", "--status", "archived").output
    cli("restore", entry_id)
    assert entry_id in cli("list").output


def test_fr16_list_rejects_an_unknown_status(cli):
    init(cli)
    result = cli("list", "--status", "sideways")
    assert result.exit_code == 1
    assert "unknown status" in result.output


def test_fr16_tags_and_themes(cli):
    init(cli)
    cli("add", MARCUS, "--tag", "Stoicism", "--yes")
    assert "stoicism" in cli("tags").output
    themes = cli("themes").output
    assert "mortality_and_time" in themes
    assert len(json.loads(cli("themes", "--json").output)) == 16


def test_fr16_collections_lifecycle(cli):
    init(cli)
    cli("add", MARCUS, "--yes")
    entry_id = json.loads(cli("list", "--json").output)[0]["id"]
    created = cli("collection", "create", "Mornings", "--desc", "before the inbox")
    assert created.exit_code == 0
    collection_id = created.output.split()[2]

    assert "position 0" in cli("collection", "add", collection_id, entry_id).output
    assert entry_id in cli("collection", "show", collection_id).output
    assert "(1 entries)" in cli("collection", "list").output
    drawn = cli("draw", "--collection", collection_id)
    assert drawn.exit_code == 0
    assert "extra:extra" in drawn.output

    cli("collection", "rm", collection_id, entry_id)
    assert entry_id not in cli("collection", "show", collection_id).output
    assert cli("collection", "delete", collection_id).exit_code == 0
    assert cli("collection", "show", collection_id).exit_code == 1


def test_fr16_draw_without_candidates_exits_nonzero(cli):
    init(cli)
    result = cli("draw")
    assert result.exit_code == 1
    assert "no eligible entry" in result.output


def test_fr16_stats_prints_the_capacity_block(cli):
    init(cli)
    cli("import", "--starter")
    cli("today")
    report = cli("stats")
    assert report.exit_code == 0
    assert "capacity (SCOPE.md capacity identity)" in report.output
    assert "review capacity C=" in report.output
    assert json.loads(cli("stats", "--json").output)["rho"] == 0.35


def test_fr16_check_attribution_by_text_and_by_entry(cli):
    init(cli)
    einstein = (
        "Insanity is doing the same thing over and over again and expecting different results."
    )
    cli("add", einstein, "--author", "Albert Einstein", "--yes")
    entry_id = json.loads(cli("list", "--json").output)[0]["id"]

    by_text = cli("check-attribution", "--text", einstein, "--author", "Albert Einstein")
    assert "misattributed" in by_text.output
    assert "misattributed" in cli("check-attribution", "--entry", entry_id).output
    assert (
        "absence of a flag is not verification"
        in cli("check-attribution", "--text", "nothing matches this").output
    )
    assert cli("check-attribution").exit_code == 1


def test_fr16_config_get_and_set(cli):
    init(cli)
    assert cli("config", "get", "seed").output.strip() == "7"
    assert cli("config", "set", "seed", "42").exit_code == 0
    assert cli("config", "get", "seed").output.strip() == "42"
    assert cli("config", "set", "batch_k", "9").exit_code == 1
    assert cli("config", "set", "rho", "0.5").exit_code == 1
    assert cli("config", "get", "nope").exit_code == 1
    assert "schema_version" in cli("config", "get").output


def test_fr16_version_flag(cli):
    result = cli("--version")
    assert result.exit_code == 0
    assert result.output.startswith("almanac ")


def test_fr16_init_rebuild_recomputes_scheduler_state(cli):
    init(cli)
    cli("import", "--starter")
    cli("today")
    result = cli("init", "--seed", "7", "--rebuild")
    assert result.exit_code == 0
    assert "rebuilt scheduler state for" in result.output
