"""FR-15: the FastAPI edge. Thin — parse, delegate to the service, serialize."""

from almanac.api.app import create_app

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    """Build the default ASGI app lazily so importing the package is side-effect free."""
    if name == "app":
        return create_app()
    raise AttributeError(name)
