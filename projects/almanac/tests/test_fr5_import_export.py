"""FR-5: import (JSON / CSV / starter pack) and export."""

from __future__ import annotations

import datetime as dt
import json

from almanac.models import ImportCandidate

CSV_HEADER = "text,author,source,url,tags,note\n"


def test_fr5_csv_import_maps_the_documented_columns(service, at_time):
    payload = (
        CSV_HEADER
        + "The first saying.,Seneca,Letters,https://example.org,stoicism;letters,keep this one\n"
    )
    report = service.import_csv(payload, now=at_time(0))
    assert report.created == 1
    assert report.errors == []
    entry = service.get_entry(report.entry_ids[0])
    assert entry.author == "Seneca"
    assert entry.source == "Letters"
    assert entry.url == "https://example.org"
    assert entry.note == "keep this one"
    assert service.repo.entry_tag_names(entry.id) == ["letters", "stoicism"]


def test_fr5_csv_import_reports_row_numbers_for_errors(service, at_time):
    payload = CSV_HEADER + "Good row.,,,,,\n,,,,,\nAnother good row.,,,,,\n"
    report = service.import_csv(payload, now=at_time(0))
    assert report.created == 2
    assert [issue.row for issue in report.errors] == [2]
    assert "text" in report.errors[0].message


def test_fr5_csv_without_a_text_column_is_rejected(service, at_time):
    from almanac.service import ValidationFailed

    try:
        service.import_csv("author,note\nSeneca,hi\n", now=at_time(0))
    except ValidationFailed as exc:
        assert "text" in str(exc)
    else:  # pragma: no cover - the import must not succeed
        raise AssertionError("a CSV without a text column must be rejected")


def test_fr5_import_skips_duplicates_within_and_across_files(service, at_time):
    payload = CSV_HEADER + "Same saying.,,,,,\nSAME SAYING!,,,,,\n"
    first = service.import_csv(payload, now=at_time(0))
    assert first.created == 1
    assert first.duplicates == 1
    again = service.import_csv(payload, now=at_time(1))
    assert again.created == 0
    assert again.duplicates == 2


def test_fr5_import_prints_the_capacity_derived_drain_horizon(service, at_time):
    rows = [ImportCandidate(text=f"Imported saying number {i}.") for i in range(20)]
    report = service.import_candidates(rows, now=at_time(0))
    assert report.created == 20
    # S + ceil(B / (k - n_pinned / P_rescue)) with no pinned entries yet.
    assert report.drain_horizon_days == 90 + 20
    assert "drain within about 110 days" in report.drain_note


def test_fr5_drain_horizon_widens_as_the_pinned_set_grows(service, at_time):
    created = service.capture("A pinned anchor.", now=at_time(0))
    service.pin(created.entry.id, now=at_time(0))
    report = service.import_candidates(
        [ImportCandidate(text=f"Batch entry {i}.") for i in range(10)], now=at_time(0)
    )
    assert report.drain_horizon_days == 90 + 11  # ceil(10 / (1 - 1/32)) == 11


def test_fr5_starter_pack_loads_and_is_idempotent(service, starter_quotes, at_time):
    report = service.import_starter(starter_quotes, now=at_time(0))
    assert report.created == len(starter_quotes)
    assert report.errors == []
    themes = {
        link.theme_id
        for entry_id in report.entry_ids
        for link in service.repo.entry_themes(entry_id)
    }
    assert len(themes) == 16, "the starter pack should cover every theme"

    repeat = service.import_starter(starter_quotes, now=at_time(1))
    assert repeat.created == 0
    assert repeat.duplicates == len(starter_quotes)


def test_fr5_export_carries_the_whole_library(service, at_time):
    created = service.capture(
        "Exported saying.",
        author="Anon",
        tags=["alpha"],
        themes=["courage"],
        now=at_time(0),
        captured_on=dt.date(2026, 1, 1),
    )
    service.pin(created.entry.id, now=at_time(0))
    collection = service.create_collection("Set", "desc", now=at_time(0))
    service.add_to_collection(collection.id, created.entry.id)
    card = service.materialize_day(dt.date(2026, 1, 1), now=at_time(0))[0]
    service.reflect(card.surfacing.id, "applied", "did it", now=at_time(0))

    document = service.export_library(now=at_time(0))
    assert document.format == "almanac-export"
    assert len(document.entries) == 1
    assert document.entries[0].tags == ["alpha"]
    assert [link.theme_id for link in document.entries[0].themes] == ["courage"]
    assert len(document.surfacings) == 1
    assert len(document.reflections) == 1
    assert document.collection_entries[collection.id] == [created.entry.id]
    assert document.config["seed"] == "7"


def test_fr5_export_round_trips_through_import(service, repo, datasets, clock, at_time):
    from almanac.adapters.attribution_local import LocalAttributionChecker
    from almanac.adapters.ids import SequenceIdFactory
    from almanac.adapters.personalizer_null import TemplatePersonalizer
    from almanac.service import AlmanacService
    from almanac.store.memory_repo import MemoryRepository

    for index in range(3):
        service.capture(
            f"Round trip saying {index}.",
            author=f"Author {index}",
            tags=[f"tag{index}"],
            themes=["humility"],
            now=at_time(0),
        )
    document = service.export_library(now=at_time(0))

    fresh = AlmanacService(
        repo=MemoryRepository(),
        datasets=datasets,
        clock=clock,
        ids=SequenceIdFactory(prefix="B"),
        personalizer=TemplatePersonalizer(),
        attribution=LocalAttributionChecker(datasets.misattributions),
        seed=7,
    )
    fresh.initialize()
    report = fresh.import_json(document.model_dump_json(), now=at_time(1))
    assert report.created == 3
    assert report.errors == []
    restored = fresh.list_entries()
    assert [e.text for e in restored] == [e.entry.text for e in document.entries]
    assert [e.author for e in restored] == [e.entry.author for e in document.entries]
    assert fresh.repo.entry_tag_names(restored[0].id) == ["tag0"]
    assert [link.theme_id for link in fresh.repo.entry_themes(restored[0].id)] == ["humility"]
    # Ids, hashes and history belong to the new library, not the old one.
    assert restored[0].id != document.entries[0].entry.id


def test_fr5_json_import_accepts_a_bare_list_and_reports_bad_rows(service, at_time):
    payload = json.dumps(
        [
            {"text": "A fine idea.", "kind": "idea"},
            {"text": "", "kind": "quote"},
            {"text": "Another fine idea.", "themes": ["not-a-theme"]},
        ]
    )
    report = service.import_json(payload, now=at_time(0))
    assert report.created == 1
    assert [issue.row for issue in report.errors] == [2, 3]


def test_fr5_json_import_rejects_malformed_payloads(service, at_time):
    from almanac.service import ValidationFailed

    for payload in ("not json at all", "42"):
        try:
            service.import_json(payload, now=at_time(0))
        except ValidationFailed:
            continue
        raise AssertionError(f"{payload!r} should not import")  # pragma: no cover
