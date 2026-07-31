"""FastAPI surface — thin: parse, delegate to services, serialize."""

from .app import DB_ENV, app, create_app, default_db_path, default_service_factory

__all__ = ["DB_ENV", "app", "create_app", "default_db_path", "default_service_factory"]
