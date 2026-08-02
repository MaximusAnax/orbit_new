"""Provider interfaces with offline (default) and live implementations."""

from dresscast.adapters.extractor import (
    AttributeExtractor,
    FixtureAttributeExtractor,
    NullAttributeExtractor,
    VisionAttributeExtractor,
    photo_sha256,
)
from dresscast.adapters.weather import (
    FixtureWeatherProvider,
    OpenMeteoWeatherProvider,
    WeatherProvider,
    map_open_meteo,
)

__all__ = [
    "AttributeExtractor",
    "FixtureAttributeExtractor",
    "FixtureWeatherProvider",
    "NullAttributeExtractor",
    "OpenMeteoWeatherProvider",
    "VisionAttributeExtractor",
    "WeatherProvider",
    "map_open_meteo",
    "photo_sha256",
]
