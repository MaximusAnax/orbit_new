"""D9: stable, deterministic track identity."""

from __future__ import annotations

import pytest
from flowlist.engine.identity import (
    meta_hash,
    normalize,
    parse_filename,
    spotify_id_from_uri,
    track_id,
)


def test_d9_precedence_spotify_then_file_then_meta() -> None:
    assert track_id(spotify_id="3n3Ppam7vgaVa1iaRUc9Lp", file_sha1="aa", artist="A", title="T") == (
        "spotify:3n3Ppam7vgaVa1iaRUc9Lp"
    )
    assert track_id(file_sha1="aa", artist="A", title="T") == "file:aa"
    assert track_id(artist="Vera Lux", title="Night Drive").startswith("meta:")


def test_d9_meta_ids_are_deterministic_and_40_hex() -> None:
    first = track_id(artist="Vera Lux", title="Night Drive")
    assert first == track_id(artist="Vera Lux", title="Night Drive")
    assert len(first) == len("meta:") + 40


@pytest.mark.parametrize(
    ("artist_a", "title_a", "artist_b", "title_b"),
    [
        ("Vera Lux", "Night Drive", "vera lux", "night drive"),
        ("Vera Lux", "Night Drive", "Vera  Lux", " Night Drive "),
        ("Vera Lux", "Night Drive", "Vera Lux", "Night Drive (feat. Ono)"),
        ("Vera Lux", "Night Drive", "Vera Lux", "Night Drive ft. Ono"),
        ("Vera Lux", "Night Drive", "Vera Lux", "Night, Drive!"),
        ("Vera Lux", "Night Drive", "Véra Lux", "Night Drive"),
    ],
)
def test_d9_normalisation_collapses_cosmetic_differences(
    artist_a: str, title_a: str, artist_b: str, title_b: str
) -> None:
    assert meta_hash(artist_a, title_a) == meta_hash(artist_b, title_b)


def test_d9_distinct_songs_get_distinct_ids() -> None:
    assert meta_hash("Vera Lux", "Night Drive") != meta_hash("Vera Lux", "Day Drive")
    assert meta_hash("Vera Lux", "Night Drive") != meta_hash("Synthetic Sun", "Night Drive")
    # The separator prevents artist/title bleed.
    assert meta_hash("ab", "c") != meta_hash("a", "bc")


def test_d9_requires_something_to_hash() -> None:
    with pytest.raises(ValueError):
        track_id(artist="  ", title="")


def test_d9_normalize_examples() -> None:
    assert normalize("Vera Lux") == "vera lux"
    assert normalize("Night Drive (feat. Ono)") == "night drive"
    assert normalize("Don't Stop!") == "don t stop"
    assert normalize("   spaced   out  ") == "spaced out"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("spotify:track:3n3Ppam7vgaVa1iaRUc9Lp", "3n3Ppam7vgaVa1iaRUc9Lp"),
        ("https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp", "3n3Ppam7vgaVa1iaRUc9Lp"),
        ("https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp?si=x", "3n3Ppam7vgaVa1iaRUc9Lp"),
        ("3n3Ppam7vgaVa1iaRUc9Lp", "3n3Ppam7vgaVa1iaRUc9Lp"),
        ("  3n3Ppam7vgaVa1iaRUc9Lp  ", "3n3Ppam7vgaVa1iaRUc9Lp"),
        ("spotify:album:3n3Ppam7vgaVa1iaRUc9Lp", None),
        ("not-a-uri", None),
        ("", None),
    ],
)
def test_d9_spotify_uri_parsing(value: str, expected: str | None) -> None:
    assert spotify_id_from_uri(value) == expected


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("Vera Lux - Night Drive", ("Vera Lux", "Night Drive")),
        ("01 - Night Drive", ("01", "Night Drive")),
        ("night_drive", ("", "night_drive")),
        ("Vera Lux - ", ("", "Vera Lux -")),
        (" - Night Drive", ("", "- Night Drive")),
    ],
)
def test_fr2_filename_fallback(stem: str, expected: tuple[str, str]) -> None:
    assert parse_filename(stem) == expected
