from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import ambiguity, events, people, proposals, search, tasks
from app.db.connection import lifespan

app = FastAPI(title="Orbit API", version="1.0.0", lifespan=lifespan)

app.include_router(events.router, prefix="/api/v1")
app.include_router(proposals.router, prefix="/api/v1")
app.include_router(people.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(ambiguity.router, prefix="/api/v1")

_static = Path(__file__).parent / "static"
app.mount("/playground", StaticFiles(directory=_static / "playground", html=True), name="playground")


@app.get("/")
async def root():
    return RedirectResponse(url="/playground/")


@app.get("/health")
async def health():
    return {"status": "ok"}
