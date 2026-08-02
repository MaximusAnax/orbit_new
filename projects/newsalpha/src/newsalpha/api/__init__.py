"""FastAPI surface (FR-12): thin parse -> delegate -> serialize."""

from .app import STATUS_BY_CODE, app, create_app

__all__ = ["STATUS_BY_CODE", "app", "create_app"]
