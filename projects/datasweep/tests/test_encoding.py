"""FR-4 encoding detection and mojibake repair."""

from __future__ import annotations

import pytest
from datasweep.adapters.encoding import (
    CharsetNormalizerDetector,
    DetectedEncoding,
    SimpleEncodingDetector,
)
from datasweep.adapters.readers import CsvReader
from datasweep.engine.detectors import repair_mojibake
from datasweep.engine.models import Policy

POLICY = Policy()


def read_csv(text: str, *, path: str = "sample.csv", encoding: str = "utf-8"):
    reader = CsvReader()
    data = text.encode(encoding)
    return reader.read(data, reader.sniff(path, data), POLICY)


# --------------------------------------------------------------------------
# FR-4 — encoding cascade
# --------------------------------------------------------------------------


def test_fr4_detection_cascade_order() -> None:
    detector = SimpleEncodingDetector()
    assert detector.detect(b"a,b\n1,2\n").name == "utf-8"
    assert detector.detect("José".encode("cp1252")).name == "cp1252"
    assert detector.detect(b"\xff\xfe\x61\x00").name == "utf-16"
    bom = detector.detect("﻿a,b\n".encode())
    assert bom.name == "utf-8" and bom.had_bom is True


def test_fr4_latin1_never_fails() -> None:
    detector = SimpleEncodingDetector()
    detected = detector.detect(bytes([0x81, 0x8D, 0x90]))
    assert detected.name == "latin-1"
    assert detected.decode(bytes([0x81])) == "\x81"


def test_fr4_bom_is_stripped_from_the_first_header() -> None:
    result = read_csv("﻿name,age\nAna,31\n")
    assert result.table.headers == ["name", "age"]
    assert result.meta.encoding == "utf-8"
    assert result.meta.had_bom is True


def test_fr4_cp1252_file_decodes_and_records_its_encoding() -> None:
    reader = CsvReader()
    data = "name\nJosé\n".encode("cp1252")
    meta = reader.sniff("x.csv", data)
    assert meta.encoding == "cp1252"
    assert reader.read(data, meta, POLICY).table.rows == [["José"]]


def test_fr4_mojibake_round_trip_is_bijective_where_it_applies() -> None:
    for original in ["José", "caffè", "Zürich", "naïve"]:
        damaged = original.encode("utf-8").decode("cp1252")
        assert damaged != original
        assert repair_mojibake(damaged) == original


def test_fr4_detected_encoding_is_a_frozen_record() -> None:
    detected = DetectedEncoding(name="cp1252", had_bom=False, confidence=0.8)
    with pytest.raises(ValueError):
        detected.name = "utf-8"


def test_fr4_live_detector_is_gated_on_its_optional_extra() -> None:
    """The live adapter must never be reachable without its dependency."""
    pytest.importorskip("charset_normalizer", reason="optional 'charset' extra")
    detector = CharsetNormalizerDetector()
    assert detector.detect(b"a,b\n1,2\n").name in {"utf-8", "ascii"}
