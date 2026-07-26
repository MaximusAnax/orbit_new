"""API contract tests that do not require Postgres."""

from app.main import app


def test_routes_include_maintenance_and_ambiguity():
    paths = app.openapi()["paths"]
    assert "/api/v1/maintenance" in paths
    assert "/api/v1/ambiguity-flags/{flag_id}/resolve" in paths
    assert "/api/v1/search" in paths
    assert "/api/v1/discover" in paths


def test_health_route_registered():
    assert "/health" in app.openapi()["paths"]
