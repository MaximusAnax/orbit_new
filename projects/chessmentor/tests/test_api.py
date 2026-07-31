"""FR-14 — REST surface tests (FastAPI ``TestClient``, offline adapters only).

Every test drives the app through an in-memory repository and a cheap internal
analyst; nothing here touches SQLite on disk, the network or the clock beyond
the timestamps the API mints at its own edge.
"""

from __future__ import annotations

import chess
import pytest
from chessmentor.adapters import InternalAnalyst
from chessmentor.api.app import create_app
from chessmentor.models import GameStatus
from chessmentor.services import ChessMentorService
from chessmentor.store import InMemoryRepository
from fastapi.testclient import TestClient

NOW = "2026-07-31T12:00:00Z"


@pytest.fixture
def service(datasets) -> ChessMentorService:
    repo = InMemoryRepository()
    service = ChessMentorService(repo, datasets=datasets, analyst=InternalAnalyst(max_depth=2))
    repo.initialize(datasets.levels)
    return service


@pytest.fixture
def client(service: ChessMentorService) -> TestClient:
    return TestClient(create_app(service))


@pytest.fixture
def ready(client: TestClient) -> TestClient:
    response = client.put("/profile", json={"display_name": "Owner", "at": NOW})
    assert response.status_code == 200
    return client


def test_fr14_health_reports_the_loaded_datasets(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["levels"] == 10
    assert body["openings"] > 100
    assert body["advice_entries"] >= 9
    assert body["initialized"] is False


def test_fr14_endpoints_409_before_init(client: TestClient) -> None:
    response = client.get("/rating")
    assert response.status_code == 409
    assert response.json()["code"] == "not_initialized"


def test_fr14_put_profile_initialises_and_sets_the_cold_start_level(client: TestClient) -> None:
    created = client.put(
        "/profile", json={"display_name": "Owner", "challenge_mode": "balanced", "at": NOW}
    )
    assert created.status_code == 200
    assert created.json()["profile"]["display_name"] == "Owner"
    ladder = client.get("/levels").json()
    assert ladder["r_hat"] == pytest.approx(800.0)
    assert ladder["target_score"] == pytest.approx(0.50)
    recommended = [level for level in ladder["levels"] if level["recommended"]]
    assert len(recommended) == 1
    # Cold start: the rung closest to elo* for R_INIT.
    assert abs(recommended[0]["elo_internal"] - 800.0) <= 170


def test_fr14_changing_challenge_mode_recomputes_the_recommendation(ready: TestClient) -> None:
    before = ready.get("/levels").json()["recommended_level_id"]
    ready.put("/profile", json={"challenge_mode": "stretch", "at": NOW})
    after = ready.get("/levels").json()
    assert after["challenge_mode"] == "stretch"
    assert after["target_score"] == pytest.approx(0.42)
    assert after["recommended_level_id"] >= before


def test_fr6_creating_a_second_game_is_409_with_the_open_game_id(ready: TestClient) -> None:
    first = ready.post("/games", json={"seed": 42, "color": "white", "started_at": NOW})
    assert first.status_code == 201
    game_id = first.json()["game"]["id"]
    second = ready.post("/games", json={"seed": 43, "color": "white", "started_at": NOW})
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "game_in_progress"
    assert body["in_progress_game_id"] == game_id


def test_fr6_playing_black_gets_the_cpus_first_move_immediately(ready: TestClient) -> None:
    created = ready.post("/games", json={"seed": 7, "color": "black", "started_at": NOW})
    detail = created.json()
    assert detail["game"]["player_color"] == "black"
    assert len(detail["moves"]) == 1
    assert detail["turn"] == "black"
    assert detail["player_to_move"] is True


def test_fr1_an_illegal_move_is_400_with_the_legal_move_list(ready: TestClient) -> None:
    game_id = ready.post("/games", json={"seed": 11, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    response = ready.post(f"/games/{game_id}/moves", json={"move": "e5", "at": NOW})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "illegal_move"
    assert "e4" in body["legal_moves_san"]


def test_fr6_a_move_persists_and_returns_the_cpu_reply(ready: TestClient) -> None:
    game_id = ready.post("/games", json={"seed": 11, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    body = ready.post(f"/games/{game_id}/moves", json={"move": "e4", "at": NOW}).json()
    assert body["player_move"]["san"] == "e4"
    assert body["cpu_move"] is not None
    assert body["status"] == "in_progress"
    # US-1: the node budget is the acceptance criterion, never a wall clock.
    meta = body["cpu_move"]["cpu_meta"]
    if meta is not None:
        level = ready.get(f"/games/{game_id}").json()["game"]["level_id"]
        budget = next(
            rung["node_budget"] for rung in ready.get("/levels").json()["levels"] if rung["id"] == level
        )
        assert meta["nodes"] <= budget
    stored = ready.get(f"/games/{game_id}").json()
    assert len(stored["moves"]) == 2
    assert chess.Board(stored["fen"]).is_valid()


def test_fr6_abort_before_ply_8_then_409_after(ready: TestClient) -> None:
    game_id = ready.post("/games", json={"seed": 13, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    aborted = ready.post(f"/games/{game_id}/abort", json={"move": "--", "at": NOW})
    assert aborted.status_code == 200
    assert aborted.json()["game"]["status"] == "aborted"
    assert aborted.json()["game"]["rated"] is False

    game_id = ready.post("/games", json={"seed": 17, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    for move in ("e4", "Nf3", "Nc3", "d4"):
        response = ready.post(f"/games/{game_id}/moves", json={"move": move, "at": NOW})
        if response.status_code != 200:
            break
    late = ready.post(f"/games/{game_id}/abort", json={"move": "--", "at": NOW})
    assert late.status_code == 409
    assert late.json()["code"] == "abort_too_late"


def test_fr1_resignation_finishes_rates_and_analyses_the_game(ready: TestClient) -> None:
    game_id = ready.post("/games", json={"seed": 19, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    for move in ("e4", "Nf3", "Bc4", "d3", "Nc3"):
        if ready.post(f"/games/{game_id}/moves", json={"move": move, "at": NOW}).status_code != 200:
            break
    body = ready.post(f"/games/{game_id}/resign", json={"move": "--", "at": NOW}).json()
    assert body["status"] == "opponent_win"
    assert body["termination"] == "resignation"
    assert body["result_score"] == 0.0
    assert body["game"]["rated"] is True
    # SCOPE non-goal 10: the judge pass always runs, so the rating event exists.
    assert body["analysis"] is not None
    assert body["analysis"]["node_budget"] == 6000
    assert body["analysis"]["is_rating_basis"] is True
    assert body["rating_event"] is not None
    assert body["rating_event"]["perf_game"] > 0

    history = ready.get("/rating/history").json()["events"]
    assert len(history) == 1
    rating = ready.get("/rating").json()
    assert rating["state"]["rated_games"] == 1
    assert 0.0 < rating["lambda_used"] < 1.0


def test_fr1_pgn_export_is_valid(ready: TestClient) -> None:
    game_id = ready.post("/games", json={"seed": 23, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    ready.post(f"/games/{game_id}/moves", json={"move": "e4", "at": NOW})
    pgn = ready.get(f"/games/{game_id}/pgn").json()["pgn"]
    assert '[White "Owner"]' in pgn
    import io

    import chess.pgn

    parsed = chess.pgn.read_game(io.StringIO(pgn))
    assert parsed is not None
    assert list(parsed.mainline_moves())


PGN_TWO_GAMES = """[Event "Test"]
[White "Owner"]
[Black "Rival"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 1-0

[Event "Test"]
[White "Rival"]
[Black "Owner"]
[Result "*"]

1. d4 d5 2. c4 e6 3. Nc3 Nf6 *
"""


def test_fr13_import_matches_the_display_name_and_never_rates(ready: TestClient) -> None:
    response = ready.post("/imports/pgn", json={"pgn": PGN_TWO_GAMES, "as": None, "at": NOW})
    assert response.status_code == 201
    imported = response.json()["imported"]
    assert len(imported) == 2
    assert imported[0]["game"]["player_color"] == "white"
    assert imported[0]["game"]["status"] == "player_win"
    assert imported[1]["game"]["player_color"] == "black"
    assert imported[1]["game"]["status"] == "unfinished"
    assert imported[1]["game"]["result_score"] is None
    for item in imported:
        assert item["game"]["rated"] is False
        assert item["game"]["source"] == "imported"
        assert item["game"]["level_id"] is None
    assert ready.get("/rating/history").json()["events"] == []


def test_fr13_ambiguous_auto_side_is_422_naming_the_flag(ready: TestClient) -> None:
    pgn = PGN_TWO_GAMES.replace('[Black "Rival"]', '[Black "Owner"]')
    response = ready.post("/imports/pgn", json={"pgn": pgn, "at": NOW})
    assert response.status_code == 422
    assert response.json()["code"] == "ambiguous_side"


def test_fr13_unparseable_pgn_is_422(ready: TestClient) -> None:
    response = ready.post("/imports/pgn", json={"pgn": "   \n", "at": NOW})
    assert response.status_code == 422
    assert response.json()["code"] == "pgn_parse_error"


def test_fr9_analysis_is_deduped_on_its_config_tuple(ready: TestClient) -> None:
    response = ready.post("/imports/pgn", json={"pgn": PGN_TWO_GAMES, "as": "white", "at": NOW})
    game_id = response.json()["imported"][0]["game"]["id"]
    first = ready.post(
        f"/games/{game_id}/analysis", json={"analyst": "internal", "node_budget": 400, "at": NOW}
    )
    assert first.status_code == 201
    assert first.json()["created"] is True
    second = ready.post(
        f"/games/{game_id}/analysis", json={"analyst": "internal", "node_budget": 400, "at": NOW}
    )
    assert second.json()["created"] is False
    assert second.json()["analysis"]["id"] == first.json()["analysis"]["id"]
    latest = ready.get(f"/games/{game_id}/analysis").json()["analysis"]
    assert latest["analyst"] == "internal"


def test_fr14_missing_entities_are_404(ready: TestClient) -> None:
    assert ready.get("/games/999").status_code == 404
    assert ready.get("/coach/reports/999").status_code == 404


def test_fr12_report_lists_skipped_games_and_snapshots_advice(ready: TestClient) -> None:
    game_id = ready.post("/games", json={"seed": 29, "color": "white", "started_at": NOW}).json()[
        "game"
    ]["id"]
    for move in ("e4", "Nf3", "Bc4", "Qh5", "Nc3"):
        if ready.post(f"/games/{game_id}/moves", json={"move": move, "at": NOW}).status_code != 200:
            break
    ready.post(f"/games/{game_id}/resign", json={"move": "--", "at": NOW})
    ready.post("/imports/pgn", json={"pgn": PGN_TWO_GAMES, "as": "white", "at": NOW})

    report = ready.post("/coach/reports", json={"last_games": 10, "at": NOW})
    assert report.status_code == 201
    body = report.json()["report"]
    assert body["window"]
    for suggestion in body["suggestions"]:
        assert suggestion["advice_title"]
        assert suggestion["advice_body"]
        assert suggestion["evidence"]
    fetched = ready.get(f"/coach/reports/{body['id']}").json()["report"]
    assert fetched["id"] == body["id"]
    assert ready.get("/coach/reports").json()["reports"]

    with_imported = ready.post(
        "/coach/reports", json={"last_games": 10, "include_imported": True, "at": NOW}
    ).json()["report"]
    assert with_imported["skipped_game_ids"], "imported games without a judge pass must be skipped"


def test_fr14_game_listing_filters(ready: TestClient) -> None:
    ready.post("/imports/pgn", json={"pgn": PGN_TWO_GAMES, "as": "white", "at": NOW})
    everything = ready.get("/games").json()["games"]
    assert len(everything) == 2
    imported = ready.get("/games", params={"source": "imported"}).json()["games"]
    assert len(imported) == 2
    finished = ready.get("/games", params={"status": GameStatus.UNFINISHED.value}).json()["games"]
    assert len(finished) == 1
