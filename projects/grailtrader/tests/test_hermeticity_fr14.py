"""T2 / FR-14: the offline path is hermetic and never imports a live adapter.

The check runs in a subprocess so that other test modules (which legitimately
import ``news_rss`` to unit-test its pure classifier) cannot pollute the
``sys.modules`` assertion.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path

PROBE = textwrap.dedent(
    """
    import socket
    import sys


    class _Blocked(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("the offline path must not open a socket")


    socket.socket = _Blocked
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the offline path must not open a connection")
    )

    from grailtrader.adapters import FixtureListingsFeed, FixtureNewsFeed, FixtureSocialFeed
    from grailtrader.datasets import build_context
    from grailtrader.engine.advisor import advise_garment
    from grailtrader.engine.backtest import ReferenceIndex, run_backtest
    from grailtrader.engine.index import build_index
    from grailtrader.engine.ingest import normalize_listings
    from grailtrader.ids import garment_id
    from grailtrader.models import (
        BacktestParams, Category, ConditionGrade, GarmentStatus, Garment,
    )
    from grailtrader.store import InMemoryRepository
    from grailtrader.weeks import add_weeks

    ctx = build_context()
    repo = InMemoryRepository()
    repo.initialize(reset=True)

    listings = FixtureListingsFeed(sys.argv[1]).fetch()
    normalized, report = normalize_listings(
        listings, gazetteer=ctx.gazetteer, mapper=ctx.mapper
    )
    repo.add_listings(normalized)
    events = FixtureNewsFeed(sys.argv[2]).fetch("2000-01-01", "2100-01-01")
    repo.upsert_events(events)

    as_of = "2024-08-05T00:00:00Z"
    index = build_index(
        repo.list_listings(), mapper=ctx.mapper, config=ctx.index_config,
        as_of=as_of, built_as_of=as_of,
    )
    added_at = "2026-01-01T00:00:00Z"
    piece = Garment(
        id=garment_id("helmut-lang/helmut/outerwear", "2024-01-08", 800.0, added_at),
        label="probe",
        brand_id="helmut-lang",
        era_id="helmut-lang:helmut",
        category=Category.OUTERWEAR,
        condition=ConditionGrade.EXCELLENT,
        anchor_condition=ConditionGrade.EXCELLENT,
        status=GarmentStatus.OWNED,
        acquisition_price=800.0,
        acquired_on="2024-01-08",
        added_at=added_at,
    )
    decision = advise_garment(
        piece, as_of_week="2024-07-29", index=index, events=repo.list_events(), ctx=ctx
    )
    params = BacktestParams(
        start_week="2024-04-01", end_week="2024-07-29", reference="recovered", scenario="t2"
    )
    run_backtest(
        params=params,
        garments=[piece],
        events=repo.list_events(),
        index=index,
        reference=ReferenceIndex.from_index(index, carry_back_weeks=8),
        ctx=ctx,
        as_of=as_of,
    )

    banned = [name for name in sys.modules if "news_rss" in name or "feedparser" in name]
    assert not banned, f"live adapter imported on the offline path: {banned}"
    assert "requests" not in sys.modules and "urllib.request" not in sys.modules
    assert decision.action is not None
    print("OK")
    """
)


def build_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    start = date(2024, 1, 1)
    rows = []
    for offset in range(30):
        sold = (start + timedelta(weeks=offset)).isoformat()
        for i in range(7):
            rows.append(
                {
                    "external_id": f"L-{offset}-{i}",
                    "brand_ref": "helmut-lang",
                    "era_ref": "helmut-lang:helmut",
                    "category": "outerwear",
                    "platform_condition": "Gently Used",
                    "status": "sold",
                    "listed_at": f"{sold}T00:00:00Z",
                    "sold_at": f"{sold}T12:00:00Z",
                    "sold_price": 1000.0 + i,
                    "currency": "USD",
                }
            )
    listings = tmp_path / "listings.jsonl"
    listings.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_type": "designer_departure",
                "brand_id": "helmut-lang",
                "era_id": "helmut-lang:helmut",
                "attributes": {"reason": "resignation"},
                "occurred_on": "2024-06-03",
                "source": "news",
                "source_refs": ["https://www.wwd.com/a"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return listings, events


def test_t2_fr14_offline_pipeline_is_hermetic(tmp_path):
    listings, events = build_fixtures(tmp_path)
    script = tmp_path / "probe.py"
    script.write_text(PROBE, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script), str(listings), str(events)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("OK")


def test_t2_fr14_live_adapter_is_not_imported_by_the_adapters_package():
    """Importing the package's offline surface must not pull in the RSS adapter."""
    script = (
        "import grailtrader.adapters, sys; "
        "assert 'grailtrader.adapters.news_rss' not in sys.modules; print('OK')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "OK"


EVAL_PROBE = textwrap.dedent(
    """
    import socket
    import sys


    class _Blocked(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("the eval path must not open a socket")


    socket.socket = _Blocked
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the eval path must not open a connection")
    )

    sys.path.insert(0, sys.argv[1])
    from evals import metrics, run  # the scorecard's own entry point module
    from evals.harness import load_scenario, run_pipeline

    result = run_pipeline(load_scenario("scenario_b"))
    card_rows = metrics.index_fidelity(result)
    assert card_rows.n_cells > 0
    assert result.real.aggregates["n_decisions"] > 0
    assert run.VALIDITY_CAVEAT

    banned = [name for name in sys.modules if "news_rss" in name or "feedparser" in name]
    assert not banned, f"live adapter imported on the eval path: {banned}"
    assert "requests" not in sys.modules and "urllib.request" not in sys.modules
    print("OK")
    """
)


def test_t2_fr14_eval_suite_is_hermetic(tmp_path):
    """T2: the eval entry point itself runs with sockets blocked and no live adapter."""
    project_root = Path(__file__).resolve().parents[1]
    script = tmp_path / "eval_probe.py"
    script.write_text(EVAL_PROBE, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script), str(project_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("OK")
