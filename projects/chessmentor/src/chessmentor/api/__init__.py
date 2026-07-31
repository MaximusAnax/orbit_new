"""FastAPI surface (FR-14).  ``chessmentor.api.app:app`` is the ASGI app."""

from .app import app, create_app, get_service

__all__ = ["app", "create_app", "get_service"]
