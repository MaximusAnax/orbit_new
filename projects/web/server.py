#!/usr/bin/env python3
"""One process, one port, twelve products.

Mounts every project's FastAPI app under ``/api/<slug>`` and serves the built
single-page UI from ``web/dist``. Without this, demoing would mean running
twelve uvicorn processes on twelve ports.

    uv run python web/server.py            # serve API + built UI on :8000
    uv run python web/server.py --reload   # API only, for use with `npm run dev`

The UI dev server (``npm run dev``) proxies ``/api`` here, so the same routes
work in development and in the built app.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).parent
DIST = WEB_DIR / "dist"

# Ordered for the launcher: the ones that demo well with zero setup come first.
PROJECTS: list[dict[str, str]] = [
    {"slug": "ethos", "name": "Ethos", "tagline": "Moral questions across ten traditions"},
    {"slug": "almanac", "name": "Almanac", "tagline": "Quotes that resurface when they should"},
    {"slug": "flowlist", "name": "Flowlist", "tagline": "Playlists reordered to flow"},
    {"slug": "chessmentor", "name": "ChessMentor", "tagline": "Chess that meets you at your level"},
    {"slug": "dresscast", "name": "DressCast", "tagline": "Outfits for the day's weather"},
    {"slug": "pointsmax", "name": "PointsMax", "tagline": "The best use of your points"},
    {"slug": "newsalpha", "name": "NewsAlpha", "tagline": "Market news, read and interpreted"},
    {"slug": "tickerpress", "name": "TickerPress", "tagline": "Your companies in the news"},
    {"slug": "grailtrader", "name": "GrailTrader", "tagline": "Designer resale as a market"},
    {"slug": "datasweep", "name": "DataSweep", "tagline": "Messy spreadsheets, safely cleaned"},
    {"slug": "formcoach", "name": "FormCoach", "tagline": "Training programs and form review"},
    {"slug": "voicekin", "name": "VoiceKin", "tagline": "A familiar voice, with consent"},
]

# almanac exposes its factory from api.routes; the rest from api.app.
_FACTORY_MODULE = {"almanac": "almanac.api.routes"}


def _factory_module(slug: str) -> str:
    return _FACTORY_MODULE.get(slug, f"{slug}.api.app")


def build_app() -> FastAPI:
    app = FastAPI(title="Projects", docs_url="/api/docs", openapi_url="/api/openapi.json")

    # The UI dev server runs on a different origin; in the built app it is same-origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    mounted: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for project in PROJECTS:
        slug = project["slug"]
        try:
            module = importlib.import_module(_factory_module(slug))
            sub = module.create_app()
            app.mount(f"/api/{slug}", sub)
            routes = len([r for r in sub.routes if getattr(r, "methods", None)])
            mounted.append({**project, "routes": routes})
        except Exception as exc:  # a broken project must not take the demo down
            failed.append({**project, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  ! {slug} failed to mount: {type(exc).__name__}: {exc}", file=sys.stderr)

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        """What the launcher renders. Unavailable projects are reported, not hidden."""
        return {"projects": mounted, "unavailable": failed}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": not failed, "mounted": len(mounted), "unavailable": len(failed)}

    if DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            """Serve the SPA, letting the client router own every non-API path."""
            candidate = (DIST / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(DIST.resolve()):
                return FileResponse(candidate)
            return FileResponse(DIST / "index.html")
    else:

        @app.get("/")
        def needs_build() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "ui_not_built",
                    "detail": "Run `npm install && npm run build` in projects/web/, "
                    "or `npm run dev` for the dev server on :5173.",
                    "api_docs": "/api/docs",
                },
            )

    return app


app = build_app()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="reload on code changes")
    args = parser.parse_args()

    import uvicorn

    banner = "built UI" if DIST.is_dir() else "API only (UI not built)"
    print(f"\n  Projects — {banner}")
    print(f"  http://{args.host}:{args.port}   docs at /api/docs\n")

    uvicorn.run(
        "web.server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(WEB_DIR.parent)] if args.reload else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
