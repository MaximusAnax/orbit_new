"""Orchestration between the adapters, the store and the pure engine.

This is the only layer that touches both I/O and the engine (SCOPE.md
§Architecture).  It reads the clock *here* — never inside the engine (FR-19) —
resolves the config file (DATA_MODEL.md §5), turns edge-level requests into
validated domain objects and persists whatever the engine produces.

The API and the CLI call this module and nothing below it.
"""

from __future__ import annotations

import shutil
import tomllib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dresscast.adapters.extractor import (
    AttributeExtractor,
    FixtureAttributeExtractor,
    NullAttributeExtractor,
    photo_sha256,
)
from dresscast.adapters.weather import (
    FixtureWeatherProvider,
    OpenMeteoWeatherProvider,
    WeatherProvider,
)
from dresscast.engine import assemble, comfort
from dresscast.engine.models import (
    CATEGORY_PRESETS,
    DEFAULT_COMMUTE_HOURS,
    DEFAULT_K,
    DEFAULT_OCCASIONS,
    DEFAULT_WEAR_WINDOW,
    ENGINE_VERSION,
    MET_DEFAULT,
    OVERRIDABLE_FIELDS,
    AttributeSuggestion,
    Color,
    DayBrief,
    DayForecast,
    Garment,
    LaundryEvent,
    Location,
    Recommendation,
    RequestParams,
    WearLog,
    warmth_to_clo,
)
from pydantic import ValidationError

from dresscast.errors import InvalidParams, UnknownGarment
from dresscast.store.repository import Repository

DEFAULT_DATA_DIR = Path.home() / ".dresscast"

# --------------------------------------------------------------------------
# Colour vocabulary (D8's neutral doctrine, at the tagging edge)
# --------------------------------------------------------------------------

#: The neutral set follows SCOPE.md D8 (menswear / capsule-wardrobe practice):
#: neutrals pair with anything and therefore carry no hue.
NEUTRAL_COLORS: frozenset[str] = frozenset(
    {
        "black",
        "white",
        "gray",
        "grey",
        "charcoal",
        "navy",
        "beige",
        "tan",
        "khaki",
        "cream",
        "denim",
        "olive",
        "brown",
    }
)

#: Hues on Itten's wheel for the non-neutral names the CLI/API accept.  A name
#: outside both tables must be given an explicit hue (``"rust:20"``).
HUE_BOOK: dict[str, float] = {
    "red": 0.0,
    "crimson": 350.0,
    "rust": 20.0,
    "orange": 30.0,
    "amber": 45.0,
    "yellow": 60.0,
    "lime": 90.0,
    "green": 120.0,
    "emerald": 150.0,
    "teal": 180.0,
    "cyan": 195.0,
    "blue": 220.0,
    "cobalt": 230.0,
    "indigo": 255.0,
    "purple": 280.0,
    "violet": 290.0,
    "magenta": 310.0,
    "pink": 330.0,
    "burgundy": 345.0,
}


def parse_color(spec: str | dict[str, Any] | Color, role: str = "main") -> Color:
    """Parse ``"navy"``, ``"blue"``, ``"rust:20"`` or a full mapping into a Color."""
    if isinstance(spec, Color):
        return spec
    if isinstance(spec, dict):
        return Color.model_validate({"role": role, **spec})
    text = spec.strip().lower()
    if not text:
        raise InvalidParams("a colour name must be non-empty", field="colors")
    hue: float | None = None
    if ":" in text:
        text, _, raw = text.partition(":")
        try:
            hue = float(raw)
        except ValueError as exc:
            raise InvalidParams(f"{raw!r} is not a hue in 0-360", field="colors") from exc
    if hue is not None:
        return Color(name=text, hue=hue % 360.0, neutral=False, role=role)  # type: ignore[arg-type]
    if text in NEUTRAL_COLORS:
        return Color(name=text, hue=None, neutral=True, role=role)  # type: ignore[arg-type]
    if text in HUE_BOOK:
        return Color(name=text, hue=HUE_BOOK[text], neutral=False, role=role)  # type: ignore[arg-type]
    raise InvalidParams(
        f"unknown colour {text!r}; give it a hue as '{text}:210' or use a neutral name",
        field="colors",
        neutrals=sorted(NEUTRAL_COLORS),
        known=sorted(HUE_BOOK),
    )


def parse_window(text: str) -> tuple[int, int]:
    """Parse ``"07:00-22:00"`` or ``"7-22"`` into an inclusive/exclusive hour pair."""
    raw = text.strip()
    if "-" not in raw:
        raise InvalidParams(f"{text!r} is not a window like '07:00-22:00'", field="wear_window")
    start, _, end = raw.partition("-")

    def hour(part: str) -> int:
        piece = part.strip().split(":")[0]
        try:
            return int(piece)
        except ValueError as exc:
            raise InvalidParams(
                f"{text!r} is not a window like '07:00-22:00'", field="wear_window"
            ) from exc

    return (hour(start), hour(end))


def parse_hours(text: str) -> tuple[int, ...]:
    """Parse ``"7-9,17-19"`` (or ``"7,8,9"``) into a sorted tuple of hours."""
    hours: set[int] = set()
    for chunk in text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        try:
            if "-" in piece:
                lo, _, hi = piece.partition("-")
                hours.update(range(int(lo), int(hi) + 1))
            else:
                hours.add(int(piece))
        except ValueError as exc:
            raise InvalidParams(
                f"{text!r} is not an hour list like '7-9,17-19'", field="commute_hours"
            ) from exc
    return tuple(sorted(hours))


def parse_colors(specs: Sequence[str | dict[str, Any] | Color]) -> list[Color]:
    """Parse a colour list; the first entry is the main colour (DATA_MODEL §2.1)."""
    if not specs:
        raise InvalidParams("a garment needs at least one colour", field="colors")
    return [parse_color(s, "main" if i == 0 else "accent") for i, s in enumerate(specs)]


# --------------------------------------------------------------------------
# Config (DATA_MODEL.md §5) — every key is read by named code
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    """The resolved contents of ``~/.dresscast/config.toml`` plus its defaults."""

    location: Location = Location(name="home", lat=40.71, lon=-74.01, timezone="America/New_York")
    met: float = MET_DEFAULT
    wear_window: tuple[int, int] = DEFAULT_WEAR_WINDOW
    commute_hours: tuple[int, ...] = DEFAULT_COMMUTE_HOURS
    k: int = DEFAULT_K
    occasion_weekday: str = "work"
    occasion_weekend: str = "casual"
    weather_provider: str = "fixture"
    fixture_dir: Path = DEFAULT_DATA_DIR / "weather"
    occasion_vocabulary: tuple[str, ...] = DEFAULT_OCCASIONS
    data_dir: Path = DEFAULT_DATA_DIR

    def default_occasion(self, day: str) -> str:
        """``[defaults] occasion_weekday`` / ``occasion_weekend`` (CLI edge only)."""
        weekday = date_cls.fromisoformat(day).weekday()
        return self.occasion_weekend if weekday >= 5 else self.occasion_weekday


def load_config(path: str | Path | None = None, *, data_dir: str | Path | None = None) -> Config:
    """Read the TOML config if present; every key has a default (DATA_MODEL §5)."""
    root = Path(data_dir).expanduser() if data_dir else DEFAULT_DATA_DIR
    cfg = Config(data_dir=root, fixture_dir=root / "weather")
    candidate = Path(path).expanduser() if path else root / "config.toml"
    if not candidate.is_file():
        if path is not None:
            raise InvalidParams(f"no config file at {candidate}", field="config", path=str(candidate))
        return cfg
    with candidate.open("rb") as handle:
        raw = tomllib.load(handle)
    loc = raw.get("location") or {}
    defaults = raw.get("defaults") or {}
    weather = raw.get("weather") or {}
    occasions = raw.get("occasions") or {}
    location = Location(
        name=str(loc.get("name", cfg.location.name)),
        lat=float(loc.get("lat", cfg.location.lat)),
        lon=float(loc.get("lon", cfg.location.lon)),
        timezone=str(loc.get("timezone", cfg.location.timezone)),
    )
    window = defaults.get("wear_window", list(cfg.wear_window))
    fixture_dir = weather.get("fixture_dir", str(cfg.fixture_dir))
    return Config(
        location=location,
        met=float(defaults.get("met", cfg.met)),
        wear_window=(int(window[0]), int(window[1])),
        commute_hours=tuple(int(h) for h in defaults.get("commute_hours", cfg.commute_hours)),
        k=int(defaults.get("k", cfg.k)),
        occasion_weekday=str(defaults.get("occasion_weekday", cfg.occasion_weekday)),
        occasion_weekend=str(defaults.get("occasion_weekend", cfg.occasion_weekend)),
        weather_provider=str(weather.get("provider", cfg.weather_provider)),
        fixture_dir=Path(str(fixture_dir)).expanduser(),
        occasion_vocabulary=tuple(occasions.get("vocabulary", cfg.occasion_vocabulary)),
        data_dir=root,
    )


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validation_error(exc: ValidationError) -> InvalidParams:
    """Turn a model-layer failure into FR-17's ``invalid_params`` detail code."""
    first = exc.errors()[0]
    location = first.get("loc") or ("record",)
    field = str(location[0])
    return InvalidParams(f"{field}: {first.get('msg', 'validation failed')}", field=field)


class DresscastService:
    """Everything the API and the CLI can do, with no presentation concerns."""

    def __init__(
        self,
        repo: Repository,
        config: Config | None = None,
        *,
        weather: WeatherProvider | None = None,
        extractor: AttributeExtractor | None = None,
    ) -> None:
        self.repo = repo
        self.config = config or Config()
        self.weather = weather if weather is not None else self._default_weather()
        self.extractor = extractor if extractor is not None else self._default_extractor()

    # -- construction ------------------------------------------------------

    def _default_weather(self) -> WeatherProvider:
        if self.config.weather_provider == "open_meteo" and OpenMeteoWeatherProvider.enabled():
            return OpenMeteoWeatherProvider()
        return FixtureWeatherProvider(self.config.fixture_dir)

    def _default_extractor(self) -> AttributeExtractor:
        mapping = self.config.data_dir / "suggestions.json"
        if mapping.is_file():
            return FixtureAttributeExtractor(mapping)
        return NullAttributeExtractor()

    def close(self) -> None:
        self.repo.close()

    # -- garments (FR-1) ---------------------------------------------------

    def create_garment(
        self,
        *,
        name: str,
        category: str,
        colors: Sequence[str | dict[str, Any] | Color],
        occasions: Sequence[str] = (),
        style_tags: Sequence[str] = (),
        clo: float | None = None,
        warmth: int | None = None,
        layer_role: str | None = None,
        accessory_class: str | None = None,
        formality: int | None = None,
        waterproofness: int = 0,
        windproofness: int = 0,
        wears_before_laundry: int | None = None,
        notes: str | None = None,
        now: datetime | None = None,
    ) -> Garment:
        """FR-1: create a garment, defaulting from the D1/D11 preset table.

        Every field the caller sets explicitly (rather than accepting the
        preset) is recorded in ``overridden_fields`` — that is exactly the set
        FR-2's category cascade later refuses to rewrite.
        """
        stamp = now or _utcnow()
        if category not in CATEGORY_PRESETS:
            raise InvalidParams(
                f"unknown category {category!r}",
                field="category",
                known=sorted(CATEGORY_PRESETS),
            )
        preset = CATEGORY_PRESETS[category]
        if clo is not None and warmth is not None:
            raise InvalidParams("give either --clo or --warmth, not both", field="clo")
        overridden = [
            field
            for field, value in (
                ("clo", clo if clo is not None else warmth),
                ("layer_role", layer_role),
                ("formality", formality),
                ("wears_before_laundry", wears_before_laundry),
            )
            if value is not None and field in OVERRIDABLE_FIELDS
        ]
        role = layer_role or preset.layer_role
        resolved_clo = preset.clo
        if clo is not None:
            resolved_clo = clo
        elif warmth is not None:
            resolved_clo = warmth_to_clo(category, warmth)
        if role == "accessory":
            resolved_clo = 0.0
            accessory_class = accessory_class or category
        try:
            garment = Garment(
                id=str(uuid.uuid4()),
                name=name,
                category=category,
                layer_role=role,  # type: ignore[arg-type]
                accessory_class=(accessory_class if role == "accessory" else None),  # type: ignore[arg-type]
                clo=resolved_clo,
                waterproofness=waterproofness,
                windproofness=windproofness,
                formality=preset.formality if formality is None else formality,
                colors=parse_colors(list(colors)),
                style_tags=list(style_tags),
                occasions=self._validate_occasions(occasions, role),
                wears_before_laundry=(
                    preset.wears_before_laundry
                    if wears_before_laundry is None
                    else wears_before_laundry
                ),
                wears_since_wash=0,
                status="clean",
                overridden_fields=overridden,
                notes=notes,
                created_at=stamp,
                updated_at=stamp,
            )
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        return self.repo.add_garment(garment)

    def _validate_occasions(self, occasions: Sequence[str], role: str) -> list[str]:
        values = [o.strip().lower() for o in occasions if o.strip()]
        if not values and role != "accessory":
            raise InvalidParams(
                "occasions must be non-empty for non-accessory garments",
                field="occasions",
                vocabulary=list(self.config.occasion_vocabulary),
            )
        unknown = [o for o in values if o not in self.config.occasion_vocabulary]
        if unknown:
            raise InvalidParams(
                f"unknown occasion {unknown[0]!r}",
                field="occasions",
                vocabulary=list(self.config.occasion_vocabulary),
            )
        return values

    def resolve_garment(self, needle: str) -> Garment:
        """Look a garment up by id or by (case-insensitive) name — CLI addressing."""
        found = self.repo.find_garment(needle)
        if found is None:
            raise UnknownGarment(f"no garment named {needle!r}", garment_id=needle)
        return found

    def list_garments(
        self,
        *,
        status: str | None = None,
        occasion: str | None = None,
        category: str | None = None,
        include_retired: bool = True,
    ) -> list[Garment]:
        return self.repo.list_garments(
            status=status,
            occasion=occasion,
            category=category,
            include_retired=include_retired,
        )

    def edit_garment(
        self, needle: str, *, now: datetime | None = None, **fields: Any
    ) -> Garment:
        """FR-1/FR-3: patch a garment, honouring the status state machine."""
        stamp = now or _utcnow()
        garment = self.resolve_garment(needle)
        updates = {k: v for k, v in fields.items() if v is not None}
        status = updates.pop("status", None)
        if "colors" in updates:
            updates["colors"] = parse_colors(list(updates["colors"]))
        if "occasions" in updates:
            updates["occasions"] = self._validate_occasions(
                list(updates["occasions"]), str(updates.get("layer_role", garment.layer_role))
            )
        if "warmth" in updates:
            level = updates.pop("warmth")
            updates["clo"] = warmth_to_clo(str(updates.get("category", garment.category)), level)
        overridden = set(garment.overridden_fields)
        overridden |= {f for f in updates if f in OVERRIDABLE_FIELDS}
        if updates:
            updates["overridden_fields"] = sorted(overridden)
            updates["updated_at"] = stamp
            try:
                garment = garment.model_copy(update=updates)
                garment = Garment.model_validate(garment.model_dump())
            except ValueError as exc:
                raise InvalidParams(str(exc), field="garment") from exc
            garment = self.repo.update_garment(garment)
        if status is not None and status != garment.status:
            garment = self.repo.set_status(garment.id, status, now=stamp)
        return garment

    def attach_photo(
        self, needle: str, source: str | Path, *, now: datetime | None = None
    ) -> Garment:
        """FR-2: copy the photo under the data directory and record its SHA-256."""
        stamp = now or _utcnow()
        garment = self.resolve_garment(needle)
        src = Path(source).expanduser()
        if not src.is_file():
            raise InvalidParams(f"no photo file at {src}", field="photo", path=str(src))
        photos = self.config.data_dir / "photos"
        photos.mkdir(parents=True, exist_ok=True)
        target = photos / f"{garment.id}{src.suffix.lower()}"
        if not target.exists() or src.resolve() != target.resolve():
            shutil.copyfile(src, target)
        updated = garment.model_copy(
            update={
                "photo_path": str(target),
                "photo_sha256": photo_sha256(target),
                "updated_at": stamp,
            }
        )
        return self.repo.update_garment(updated)

    # -- suggestions (FR-2) ------------------------------------------------

    def suggest(self, needle: str, *, now: datetime | None = None) -> AttributeSuggestion:
        """FR-2: stage an extractor proposal; never mutates the garment."""
        stamp = now or _utcnow()
        garment = self.resolve_garment(needle)
        if not garment.photo_path:
            raise InvalidParams(
                f"{garment.name!r} has no photo attached; attach one first",
                field="photo_path",
                garment_id=garment.id,
            )
        payload = self.extractor.extract(garment.photo_path)
        if payload is None:
            raise InvalidParams(
                f"the {self.extractor.name!r} extractor has nothing to propose for "
                f"{garment.name!r}",
                field="photo_path",
                garment_id=garment.id,
            )
        source = "vision" if self.extractor.name == "vision" else "fixture"
        suggestion = AttributeSuggestion(
            id="",
            garment_id=garment.id,
            source=source,  # type: ignore[arg-type]
            payload=payload.as_payload(),
            status="pending",
            created_at=stamp,
        )
        return self.repo.add_suggestion(suggestion)

    def accept_suggestion(
        self, suggestion_id: str, fields: Sequence[str], *, now: datetime | None = None
    ) -> tuple[AttributeSuggestion, Garment]:
        return self.repo.accept_suggestion(suggestion_id, list(fields), now=now or _utcnow())

    def reject_suggestion(
        self, suggestion_id: str, *, now: datetime | None = None
    ) -> AttributeSuggestion:
        return self.repo.reject_suggestion(suggestion_id, now=now or _utcnow())

    def list_suggestions(self, garment_id: str | None = None) -> list[AttributeSuggestion]:
        return self.repo.list_suggestions(garment_id)

    # -- forecasts (FR-4) --------------------------------------------------

    def fetch_forecast(self, date: str, *, now: datetime | None = None) -> DayForecast:
        """Fetch and append a snapshot (append-only, provenance recorded)."""
        stamp = now or _utcnow()
        forecast = self.weather.get_day(self.config.location, date, now=stamp)
        return self.repo.add_snapshot(forecast)

    def ensure_forecast(self, date: str, *, now: datetime | None = None) -> DayForecast:
        """The latest stored snapshot for ``date``, fetching one if there is none."""
        existing = self.repo.latest_snapshot(date)
        if existing is not None:
            return existing
        return self.fetch_forecast(date, now=now)

    # -- request params ----------------------------------------------------

    def build_params(
        self,
        *,
        date: str,
        occasion: str | None = None,
        wear_window: tuple[int, int] | None = None,
        commute_hours: Sequence[int] | None = None,
        met: float | None = None,
        k: int | None = None,
        seed: int = 0,
    ) -> RequestParams:
        cfg = self.config
        chosen = (occasion or cfg.default_occasion(date)).strip().lower()
        if chosen not in cfg.occasion_vocabulary:
            raise InvalidParams(
                f"unknown occasion {chosen!r}",
                field="occasion",
                vocabulary=list(cfg.occasion_vocabulary),
            )
        return RequestParams(
            date=date,
            occasion=chosen,
            wear_window=tuple(wear_window) if wear_window else cfg.wear_window,  # type: ignore[arg-type]
            commute_hours=tuple(commute_hours) if commute_hours is not None else cfg.commute_hours,
            met=cfg.met if met is None else met,
            k=cfg.k if k is None else k,
            seed=seed,
        )

    # -- brief (FR-16) -----------------------------------------------------

    def brief(
        self,
        *,
        date: str,
        met: float | None = None,
        wear_window: tuple[int, int] | None = None,
        occasion: str | None = None,
        now: datetime | None = None,
    ) -> DayBrief:
        """FR-16: what the day demands — works on an empty database."""
        forecast = self.ensure_forecast(date, now=now)
        params = self.build_params(
            date=date,
            occasion=occasion or self.config.occasion_vocabulary[0],
            wear_window=wear_window,
            met=met,
        )
        return comfort.day_brief(forecast, params)

    # -- recommendations (FR-8, FR-13) -------------------------------------

    def recommend(
        self,
        *,
        date: str,
        occasion: str | None = None,
        wear_window: tuple[int, int] | None = None,
        commute_hours: Sequence[int] | None = None,
        met: float | None = None,
        k: int | None = None,
        seed: int = 0,
        now: datetime | None = None,
        persist: bool = True,
    ) -> Recommendation:
        """FR-8: assemble the top-k outfits and append the run to the store."""
        stamp = now or _utcnow()
        forecast = self.ensure_forecast(date, now=stamp)
        params = self.build_params(
            date=date,
            occasion=occasion,
            wear_window=wear_window,
            commute_hours=commute_hours,
            met=met,
            k=k,
            seed=seed,
        )
        wardrobe = self.repo.list_garments(include_retired=False)
        history = self.repo.wear_history(date)
        recommendation = assemble.recommend(
            wardrobe,
            forecast,
            history,
            params,
            now=stamp,
            engine_version=ENGINE_VERSION,
        )
        if not persist:
            return recommendation
        return self.repo.add_recommendation(recommendation)

    def get_recommendation(self, recommendation_id: str) -> Recommendation:
        return self.repo.get_recommendation(recommendation_id)

    def latest_recommendation(self, date: str | None = None) -> Recommendation | None:
        return self.repo.latest_recommendation(date)

    def resolve_recommendation(self, needle: str | None, date: str | None = None) -> Recommendation:
        """``latest`` (or nothing) resolves to the most recent stored run."""
        if needle and needle != "latest":
            return self.repo.get_recommendation(needle)
        found = self.repo.latest_recommendation(date)
        if found is None:
            raise InvalidParams(
                "no recommendation has been stored yet", field="recommendation_id"
            )
        return found

    # -- wear logging (FR-12) ----------------------------------------------

    def wear_recommendation(
        self,
        recommendation_id: str,
        *,
        rank: int = 1,
        date: str | None = None,
        now: datetime | None = None,
    ) -> WearLog:
        stamp = now or _utcnow()
        recommendation = self.resolve_recommendation(recommendation_id)
        matches = [o for o in recommendation.outfits if o.rank == rank]
        if not matches:
            raise InvalidParams(
                f"recommendation {recommendation.id!r} has no rank {rank}",
                field="rank",
                available=[o.rank for o in recommendation.outfits],
            )
        outfit = matches[0]
        return self.repo.add_wear_log(
            date=date or recommendation.date,
            garment_ids=[i.garment_id for i in outfit.items],
            source="recommendation",
            outfit_id=outfit.id,
            now=stamp,
        )

    def wear_items(
        self,
        needles: Sequence[str],
        *,
        date: str | None = None,
        now: datetime | None = None,
    ) -> WearLog:
        stamp = now or _utcnow()
        ids = [self.resolve_garment(n).id for n in needles]
        return self.repo.add_wear_log(
            date=date or stamp.date().isoformat(),
            garment_ids=ids,
            source="manual",
            now=stamp,
        )

    def undo_wear(
        self, log_id: str, *, today: str | None = None, now: datetime | None = None
    ) -> None:
        stamp = now or _utcnow()
        self.repo.undo_wear_log(
            log_id, today=today or stamp.date().isoformat(), now=stamp
        )

    def history(
        self, *, until: str | None = None, days: int = 14
    ) -> list[WearLog]:
        stamp = until or _utcnow().date().isoformat()
        end = date_cls.fromisoformat(stamp)
        start = end.toordinal() - max(0, days - 1)
        return self.repo.list_wear_logs(
            since=date_cls.fromordinal(start).isoformat(), until=stamp
        )

    # -- laundry (FR-3) ----------------------------------------------------

    def launder(
        self,
        needles: Sequence[str] = (),
        *,
        all_dirty: bool = False,
        note: str | None = None,
        now: datetime | None = None,
    ) -> LaundryEvent:
        stamp = now or _utcnow()
        if all_dirty:
            ids = self.repo.dirty_garment_ids()
        else:
            ids = [self.resolve_garment(n).id for n in needles]
        if not ids:
            raise InvalidParams(
                "nothing to launder: no dirty garments" if all_dirty else "name a garment",
                field="garment_ids",
            )
        return self.repo.add_laundry_event(ids, now=stamp, note=note)


def build_service(
    *,
    db: str | Path | None = None,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    weather: WeatherProvider | None = None,
    extractor: AttributeExtractor | None = None,
) -> DresscastService:
    """Open the SQLite store and wire the offline adapters (the CLI/API edge)."""
    from dresscast.store.sqlite_repo import SqliteRepository

    config = load_config(config_path, data_dir=data_dir)
    if db is None:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        path: str | Path = config.data_dir / "dresscast.db"
    else:
        path = db
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    repo = SqliteRepository(path)
    return DresscastService(repo, config, weather=weather, extractor=extractor)


def with_fixture_weather(service: DresscastService, directory: str | Path) -> DresscastService:
    """Point a service at a directory of committed weather fixtures."""
    service.config = replace(service.config, fixture_dir=Path(directory))
    service.weather = FixtureWeatherProvider(directory)
    return service
