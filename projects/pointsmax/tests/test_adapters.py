"""Adapter contracts: offline implementations work, live ones stay gated."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import sealed, world_files
from pointsmax.adapters import (
    AwardSource,
    CommittedAwardSource,
    CommittedFareReference,
    CommittedWorldProvider,
    FareReference,
    WorldProvider,
    build_world,
)
from pointsmax.adapters.world_provider import DEFAULT_WORLD_DIR, WORLD_FILE_MODELS
from pointsmax.engine.world import WorldValidationError
from pointsmax.models import Cabin, OfferKind

TODAY = date(2026, 7, 31)


# -- WorldProvider ---------------------------------------------------------


def test_committed_world_provider_satisfies_the_protocol():
    provider = CommittedWorldProvider()
    assert isinstance(provider, WorldProvider)


def test_committed_world_provider_reads_and_validates_the_shipped_dataset():
    provider = CommittedWorldProvider()
    world = provider.load()
    assert world.version.content_hash == provider.content_hash()
    assert world.version.version == "1.0.0"
    assert len(world.programs) == len({p.id for p in world.programs})


def test_committed_world_provider_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        CommittedWorldProvider(tmp_path).load()


def test_committed_world_provider_rejects_a_tampered_dataset(tmp_path):
    for name in [*WORLD_FILE_MODELS, "version.json"]:
        (tmp_path / name).write_text(
            (DEFAULT_WORLD_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    valuations = json.loads((tmp_path / "valuations.json").read_text(encoding="utf-8"))
    valuations[0]["cpp_milli"] += 1
    (tmp_path / "valuations.json").write_text(json.dumps(valuations), encoding="utf-8")

    with pytest.raises(WorldValidationError) as exc:
        CommittedWorldProvider(tmp_path).load()
    assert any(issue.code == "content_hash_mismatch" for issue in exc.value.issues)


def test_build_world_reports_missing_files():
    files = world_files()
    del files["cards.json"]
    with pytest.raises(ValueError, match=r"cards\.json"):
        build_world(sealed(files))


def test_build_world_rejects_a_non_array_file():
    files = world_files()
    files["cards.json"] = {"not": "an array"}
    with pytest.raises(ValueError, match="JSON array"):
        build_world(sealed(files), validate=False)


def test_build_world_can_skip_validation_for_fixture_authoring():
    files = world_files()
    files["valuations.json"] = []
    world = build_world(sealed(files), validate=False)
    assert world.valuations == []


# -- AwardSource -----------------------------------------------------------


def test_committed_award_source_returns_sorted_offers(world):
    source = CommittedAwardSource(world)
    assert isinstance(source, AwardSource)
    ids = [offer.id for offer in source.offers()]
    assert ids == sorted(ids)
    assert {o.id for o in source.for_program("air_x")} == {"x_out", "x_back", "x_rt", "x_soon"}


# -- FareReference ---------------------------------------------------------


def test_committed_fare_reference_matches_the_world_lookup(world):
    fares = CommittedFareReference(world)
    assert isinstance(fares, FareReference)
    assert (
        fares.fare(
            OfferKind.FLIGHT,
            month="2026-10",
            origin_city="AAA",
            dest_city="BBB",
            cabin=Cabin.BUSINESS,
            round_trip=False,
        )
        == 200000
    )
    assert fares.fare(OfferKind.STAY, month="2026-10", city="AAA") == 30000
    assert fares.fare(OfferKind.STAY, month="2026-12", city="AAA") is None


def test_fare_lookup_distinguishes_direction_and_trip_type(world):
    fares = CommittedFareReference(world)
    round_trip = fares.fare(
        OfferKind.FLIGHT,
        month="2026-10",
        origin_city="AAA",
        dest_city="BBB",
        cabin=Cabin.BUSINESS,
        round_trip=True,
    )
    one_way = fares.fare(
        OfferKind.FLIGHT,
        month="2026-10",
        origin_city="AAA",
        dest_city="BBB",
        cabin=Cabin.BUSINESS,
        round_trip=False,
    )
    assert round_trip == 380000
    assert round_trip != one_way * 2


# -- live adapters ---------------------------------------------------------


def test_live_world_provider_requires_its_feed_url(monkeypatch):
    from pointsmax.adapters._http import LiveAdapterUnavailable
    from pointsmax.adapters.world_provider_live import LiveWorldProvider

    monkeypatch.delenv("POINTSMAX_WORLD_FEED_URL", raising=False)
    with pytest.raises(LiveAdapterUnavailable, match="POINTSMAX_WORLD_FEED_URL"):
        LiveWorldProvider()
    provider = LiveWorldProvider("https://example.invalid/world/")
    assert provider.base_url == "https://example.invalid/world"


def test_live_award_source_requires_its_feed_url(monkeypatch):
    from pointsmax.adapters._http import LiveAdapterUnavailable
    from pointsmax.adapters.award_source_live import HttpAwardSource

    monkeypatch.delenv("POINTSMAX_AWARD_FEED_URL", raising=False)
    with pytest.raises(LiveAdapterUnavailable, match="POINTSMAX_AWARD_FEED_URL"):
        HttpAwardSource()


def test_live_fare_reference_requires_its_feed_url(monkeypatch):
    from pointsmax.adapters._http import LiveAdapterUnavailable
    from pointsmax.adapters.fare_reference_live import HttpFareReference

    monkeypatch.delenv("POINTSMAX_FARE_FEED_URL", raising=False)
    with pytest.raises(LiveAdapterUnavailable, match="POINTSMAX_FARE_FEED_URL"):
        HttpFareReference()


def test_llm_goal_parser_requires_credentials(world, monkeypatch):
    from pointsmax.adapters.goal_parser_llm import LLMGoalParser

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        LLMGoalParser(world)


def test_llm_goal_parser_validates_drafts_through_the_offline_builders(world):
    """The live adapter cannot smuggle an invalid goal past the schema (Non-goal 9)."""
    from pointsmax.adapters.goal_parser_llm import LLMGoalDraft, LLMGoalParser

    parser = LLMGoalParser.__new__(LLMGoalParser)  # no client, no network
    parser._world = world

    good = LLMGoalDraft(
        kind="flight",
        origin_city="AAA",
        dest_city="BBB",
        cabin="business",
        round_trip=True,
        passengers=2,
        month="2026-10",
    )
    goal = parser.build(good, "rt biz AAA to BBB in October")
    assert goal.origin_city == "AAA"
    assert goal.cabin is Cabin.BUSINESS
    assert goal.travel_window_start == date(2026, 10, 1)

    incomplete = LLMGoalDraft(kind="flight", dest_city="BBB", month="2026-10")
    assert parser.build(incomplete, "to BBB in October").message.startswith(
        "the language model returned an invalid goal"
    )

    flagged = LLMGoalDraft(kind="flight", missing=["origin_city"])
    assert parser.build(flagged, "to BBB").missing == ["origin_city"]

    nonsense = LLMGoalDraft(kind="flight", origin_city="AAA", dest_city="AAA", month="2026-10")
    assert parser.build(nonsense, "AAA to AAA").message.startswith(
        "the language model returned an invalid goal"
    )


def test_offline_import_path_never_pulls_in_networking():
    """CONVENTIONS: live adapters must not be imported at module load of the offline path.

    Runs in a subprocess so the check starts from a genuinely clean interpreter.
    """
    import subprocess
    import sys

    probe = (
        "import sys, pointsmax.adapters, pointsmax.engine.advisor, pointsmax.store\n"
        "live = sorted(m for m in sys.modules if m.startswith('pointsmax') "
        "and ('live' in m or m.endswith('_llm')))\n"
        "net = sorted(m for m in sys.modules if m in "
        "{'urllib.request', 'http.client', 'anthropic'})\n"
        "print(repr(live), repr(net))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[] []"
