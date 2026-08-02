"""HTTP surface (FR-13).

``create_app`` builds the FastAPI application against an injected
:class:`~flowlist.store.base.Repository`; ``default_app`` opens the default
SQLite database for ``uvicorn flowlist.api:app``.
"""

from flowlist.api.app import STATUS_BY_CODE, create_app, default_app, get_repository

__all__ = ["STATUS_BY_CODE", "create_app", "default_app", "get_repository"]
