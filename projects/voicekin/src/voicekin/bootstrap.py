"""Wiring shared by the CLI and the API: build a service against a data home.

This module (not the engine) is where wall-clock defaults live: FR-15 keeps
``now`` an explicit input everywhere below ``services.py``, and the two thin
surfaces default it to the current UTC time only when the caller omits it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from voicekin.adapters.deliver import DeviceDeliverer
from voicekin.adapters.deliver_filesink import FileSinkDeliverer
from voicekin.adapters.deliver_hass import HomeAssistantDeliverer
from voicekin.models import TargetKind
from voicekin.services import (
    DB_FILENAME,
    DEFAULT_DATA_HOME,
    VoiceKinService,
    load_calibration,
    load_statement_template,
)
from voicekin.store import SQLiteRepository

DATA_HOME_ENV = "VOICEKIN_HOME"


def resolve_data_home(explicit: Path | str | None = None) -> Path:
    """``--data-home`` flag > ``VOICEKIN_HOME`` env > ``~/.voicekin``."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(DATA_HOME_ENV)
    if env:
        return Path(env).expanduser()
    return DEFAULT_DATA_HOME


def default_deliverers() -> dict[TargetKind, DeviceDeliverer]:
    """File sink always; Home Assistant only when its env credentials are set."""
    deliverers: dict[TargetKind, DeviceDeliverer] = {TargetKind.FILE_SINK: FileSinkDeliverer()}
    live = HomeAssistantDeliverer.from_env()
    if live is not None:
        deliverers[TargetKind.HOME_ASSISTANT] = live
    return deliverers


def build_service(data_home: Path | str | None = None) -> VoiceKinService:
    """A fully wired service over the SQLite store in ``data_home``."""
    home = resolve_data_home(data_home)
    home.mkdir(parents=True, exist_ok=True)
    repository = SQLiteRepository(home / DB_FILENAME)
    repository.initialize()
    return VoiceKinService(
        repository=repository,
        data_home=home,
        calibration=load_calibration(),
        statement_template=load_statement_template(),
        deliverers=default_deliverers(),
    )


def now_utc() -> str:
    """Wall-clock ISO timestamp for the thin surfaces' ``now`` defaults."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "DATA_HOME_ENV",
    "build_service",
    "default_deliverers",
    "now_utc",
    "resolve_data_home",
]
