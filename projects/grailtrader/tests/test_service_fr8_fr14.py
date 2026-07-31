"""The application service: advice supersession, determinism and the pipeline guards.

These exercise the seam the API and CLI share (FR-8 identity, FR-14 determinism)
without going through either surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from grailtrader.models import EventStatus, EventType
from grailtrader.service import GrailTraderService, PreconditionError
from grailtrader.store import InMemoryRepository


def _service(clock: str = "2026-07-31T08:00:00Z") -> GrailTraderService:
    repo = InMemoryRepository()
    repo.initialize(reset=True)
    api = GrailTraderService(repo, clock=lambda: clock)
    api.initialize()
    return api


@pytest.fixture
def loaded(mini_feed: tuple[Path, Path], mini_weeks: list[str]) -> GrailTraderService:
    listings, events = mini_feed
    api = _service()
    api.load_listings(path=listings)
    api.ingest_event_feed(path=events)
    api.build_index(as_of=mini_weeks[-1])
    api.add_garment(
        label="HL astro moto",
        brand="helmut-lang",
        era="helmut",
        category="outerwear",
        condition="excellent",
        price=1200.0,
        date=mini_weeks[10],
        added_at="2026-01-01T00:00:00Z",
    )
    return api


def test_fr14_index_rebuild_is_idempotent(
    loaded: GrailTraderService, mini_weeks: list[str]
) -> None:
    first = loaded.repo.list_index_points()
    report = loaded.build_index(as_of=mini_weeks[-1])
    second = loaded.repo.list_index_points()
    assert report.points_written == len(first)
    assert [row.model_dump() for row in first] == [row.model_dump() for row in second]


def test_fr8_rerunning_advise_with_unchanged_inputs_is_a_no_op(
    loaded: GrailTraderService, mini_weeks: list[str]
) -> None:
    week = mini_weeks[47]
    first = loaded.advise(as_of=week)
    second = loaded.advise(as_of=week)
    assert [row.id for row in first] == [row.id for row in second]
    assert [row.rendered_text for row in first] == [row.rendered_text for row in second]
    assert len(loaded.list_advice(as_of=week, history=True)) == 1


def test_fr8_a_new_confirmed_event_supersedes_the_current_advice(
    loaded: GrailTraderService, mini_weeks: list[str]
) -> None:
    week = mini_weeks[47]
    before = loaded.advise(as_of=week)[0]

    loaded.add_event(
        event_type=EventType.CELEBRITY_COSIGN,
        brand="helmut-lang",
        era="helmut",
        occurred_on=mini_weeks[46],
        attributes={"celebrity": "Juno Vasquez", "tier": "a_list"},
        source="social",
    )
    loaded.clock = lambda: "2026-07-31T09:00:00Z"
    after = loaded.advise(as_of=week)[0]

    assert after.inputs_hash != before.inputs_hash
    assert after.id != before.id
    assert after.expected_return > before.expected_return  # the co-sign adds to the path
    current = loaded.list_advice(as_of=week)
    assert [row.id for row in current] == [after.id]
    assert len(loaded.list_advice(as_of=week, history=True)) == 2


def test_fr5_pending_events_do_not_move_advice(
    loaded: GrailTraderService, mini_weeks: list[str]
) -> None:
    week = mini_weeks[47]
    baseline = loaded.advise(as_of=week)[0]
    event, _ = loaded.add_event(
        event_type=EventType.BRAND_SCANDAL,
        brand="helmut-lang",
        occurred_on=mini_weeks[46],
        attributes={"severity": "severe"},
    )
    pending = event.model_copy(update={"status": EventStatus.PENDING})
    loaded.repo.upsert_events([pending])
    loaded.clock = lambda: "2026-07-31T10:00:00Z"
    after = loaded.advise(as_of=week)[0]
    assert after.inputs_hash == baseline.inputs_hash

    loaded.set_event_status(event.id, EventStatus.CONFIRMED)
    loaded.clock = lambda: "2026-07-31T11:00:00Z"
    confirmed = loaded.advise(as_of=week)[0]
    assert confirmed.inputs_hash != baseline.inputs_hash
    assert confirmed.expected_return < baseline.expected_return


def test_pipeline_order_is_enforced(mini_feed: tuple[Path, Path]) -> None:
    api = _service()
    with pytest.raises(PreconditionError, match="listings load"):
        api.build_index()
    with pytest.raises(PreconditionError, match="index build"):
        api.advise()
    api.load_listings(path=mini_feed[0])
    api.build_index()
    with pytest.raises(PreconditionError, match="portfolio add"):
        api.backtest()


def test_fr11_immutable_garment_fields_are_rejected(loaded: GrailTraderService) -> None:
    garment = loaded.list_garments()[0]
    with pytest.raises(ValueError, match="immutable or unknown"):
        loaded.edit_garment(garment.id, brand_id="celine")


def test_fr11_soft_deleted_garments_leave_the_pipeline(
    loaded: GrailTraderService, mini_weeks: list[str]
) -> None:
    garment = loaded.list_garments()[0]
    loaded.advise(as_of=mini_weeks[47])
    loaded.remove_garment(garment.id)
    assert loaded.list_garments() == []
    loaded.clock = lambda: "2026-07-31T12:00:00Z"
    assert loaded.advise(as_of=mini_weeks[47]) == []
    # historical advice stays readable
    assert loaded.list_advice(garment=garment.id, history=True)
