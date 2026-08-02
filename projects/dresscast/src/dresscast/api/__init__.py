"""FastAPI surface (SCOPE.md FR-17).  Thin: parse, delegate, serialize."""

from dresscast.api.app import STATUS_BY_CODE, app, create_app

__all__ = ["STATUS_BY_CODE", "app", "create_app"]
