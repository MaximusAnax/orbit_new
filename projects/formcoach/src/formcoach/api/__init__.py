"""FR-11 — the FastAPI surface.

``from formcoach.api import create_app`` builds an app around an injected
:class:`~formcoach.services.FormCoachService`; ``formcoach.api.app:app`` is the
module-level instance uvicorn serves.
"""

from formcoach.api.app import create_app

__all__ = ["create_app"]
