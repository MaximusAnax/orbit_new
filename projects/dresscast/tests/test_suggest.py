"""FR-2: photo attach, attribute suggestion, and the accept cascade."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from conftest import NOW, garment
from dresscast.adapters.extractor import (
    FixtureAttributeExtractor,
    NullAttributeExtractor,
    VisionAttributeExtractor,
    photo_sha256,
)
from dresscast.engine.models import AttributeSuggestion, AttributeSuggestionPayload
from dresscast.errors import InvalidParams, NoExtractorConfigured
from dresscast.store.memory import InMemoryRepository

LATER = datetime(2026, 4, 14, 20, 0, 0)


@pytest.fixture()
def repo():
    return InMemoryRepository()


def _suggestion(garment_id: str, payload: dict, sid: str = "s1") -> AttributeSuggestion:
    return AttributeSuggestion(
        id=sid,
        garment_id=garment_id,
        source="fixture",
        payload=payload,
        created_at=NOW,
    )


def _payload(**fields) -> dict:
    return {k: {"value": v, "confidence": 0.9} for k, v in fields.items()}


# --------------------------------------------------------------------------
# The adapters
# --------------------------------------------------------------------------


def test_fr2_photo_sha256_is_content_addressed(tmp_path):
    photo = tmp_path / "coat.jpg"
    photo.write_bytes(b"not-a-real-jpeg")
    digest = photo_sha256(photo)
    assert len(digest) == 64
    same = tmp_path / "copy.jpg"
    same.write_bytes(b"not-a-real-jpeg")
    assert photo_sha256(same) == digest


def test_fr2_fixture_extractor_keys_on_sha_then_basename(tmp_path):
    photo = tmp_path / "coat.jpg"
    photo.write_bytes(b"fixture-photo")
    digest = photo_sha256(photo)
    extractor = FixtureAttributeExtractor(
        {
            digest: {"fields": {"category": "wool_coat"}, "confidences": {"category": 0.91}},
            "other.jpg": {"fields": {"formality": 5}},
        }
    )
    proposal = extractor.extract(str(photo))
    assert proposal.fields == {"category": "wool_coat"}
    assert proposal.confidences == {"category": 0.91}
    assert extractor.extract(str(tmp_path / "other.jpg")).fields == {"formality": 5}
    assert extractor.extract(str(tmp_path / "unknown.jpg")) is None


def test_fr2_fixture_extractor_reads_a_committed_json_map(tmp_path):
    mapping = tmp_path / "suggestions.json"
    mapping.write_text(json.dumps({"coat.jpg": {"fields": {"category": "parka"}}}))
    extractor = FixtureAttributeExtractor(mapping)
    assert extractor.extract(str(tmp_path / "coat.jpg")).fields == {"category": "parka"}


def test_fr2_missing_extractor_fails_loudly_without_touching_the_garment():
    with pytest.raises(NoExtractorConfigured) as excinfo:
        NullAttributeExtractor().extract("/photos/coat.jpg")
    assert excinfo.value.code == "no_extractor_configured"


def test_fr2_vision_extractor_is_credential_gated(monkeypatch):
    monkeypatch.delenv("DRESSCAST_VISION_MODEL", raising=False)
    extractor = VisionAttributeExtractor()
    assert extractor.available() is False
    with pytest.raises(NoExtractorConfigured) as excinfo:
        extractor.extract("/photos/coat.jpg")
    assert "DRESSCAST_VISION_MODEL" in str(excinfo.value)


def test_fr2_payload_rejects_unsuggestible_fields():
    with pytest.raises(ValueError, match="suggestible"):
        AttributeSuggestionPayload(fields={"clo": 0.4})
    with pytest.raises(ValueError, match="confidence"):
        AttributeSuggestionPayload(fields={"category": "parka"}, confidences={"category": 1.5})


def test_fr2_payload_serializes_to_the_stored_shape():
    payload = AttributeSuggestionPayload(
        fields={"category": "parka"}, confidences={"category": 0.8}
    )
    assert payload.as_payload() == {"category": {"value": "parka", "confidence": 0.8}}


# --------------------------------------------------------------------------
# Staging and resolution
# --------------------------------------------------------------------------


def test_fr2_suggestions_never_mutate_a_garment_on_their_own(repo):
    original = garment("g1", "fleece", "fleece")
    repo.add_garment(original)
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    assert repo.get_garment("g1") == original
    assert repo.list_suggestions("g1")[0].status == "pending"


def test_fr2_accept_applies_the_category_cascade(repo):
    """DATA_MODEL.md §6: fleece → wool_coat cascades all four preset fields."""
    repo.add_garment(garment("g1", "fleece", "fleece"))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    resolved, merged = repo.accept_suggestion("s1", ["category"], now=LATER)
    assert merged.category == "wool_coat"
    assert merged.clo == 0.60
    assert merged.layer_role == "outer"
    assert merged.formality == 4
    assert merged.wears_before_laundry == 30
    assert merged.updated_at == LATER
    assert resolved.status == "accepted"
    assert resolved.resolved_at == LATER
    fields = {f.field: f for f in resolved.accepted_fields}
    assert fields["category"].via == "explicit"
    assert fields["category"].old == "fleece"
    assert fields["clo"].via == "cascade"
    assert fields["clo"].old == 0.30
    assert fields["clo"].new == 0.60


def test_fr2_accept_merges_only_the_named_keys(repo):
    repo.add_garment(garment("g1", "fleece", "fleece", style_tags=("outdoorsy",)))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat", style_tags=["preppy"])))
    _, merged = repo.accept_suggestion("s1", ["style_tags"], now=LATER)
    assert merged.style_tags == ["preppy"]
    assert merged.category == "fleece"


def test_fr2_cascade_skips_a_user_overridden_clo_and_rolls_back(repo):
    """The documented failure: an overridden 0.30 clo cannot become a wool coat."""
    repo.add_garment(garment("g1", "fleece", "fleece", clo=0.30, overridden_fields=("clo",)))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    with pytest.raises(InvalidParams) as excinfo:
        repo.accept_suggestion("s1", ["category"], now=LATER)
    message = str(excinfo.value)
    assert "0.3" in message and "wool_coat" in message
    assert "0.45" in message and "0.75" in message
    assert excinfo.value.code == "invalid_params"
    assert repo.get_garment("g1").category == "fleece"
    assert repo.get_suggestion("s1").status == "pending"


def test_fr2_the_documented_remedy_accepting_clo_and_category_together(repo):
    repo.add_garment(garment("g1", "fleece", "fleece", clo=0.30, overridden_fields=("clo",)))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat", formality=4)))
    payload = repo.get_suggestion("s1").payload
    payload["clo"] = {"value": 0.60, "confidence": 1.0}
    with pytest.raises(ValueError, match="suggestible"):
        AttributeSuggestion(
            id="s2",
            garment_id="g1",
            source="fixture",
            payload=payload,
            created_at=NOW,
        )
    # 'clo' is not a suggestible field, so the remedy is to edit it first
    original = repo.get_garment("g1")
    repo.update_garment(original.model_copy(update={"clo": 0.60}))
    _, merged = repo.accept_suggestion("s1", ["category"], now=LATER)
    assert merged.category == "wool_coat"
    assert merged.clo == 0.60


def test_fr2_explicit_keys_win_over_the_cascade(repo):
    repo.add_garment(garment("g1", "fleece", "fleece"))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat", formality=2)))
    _, merged = repo.accept_suggestion("s1", ["category", "formality"], now=LATER)
    assert merged.formality == 2
    assert merged.clo == 0.60


def test_fr2_accepting_an_unproposed_field_is_refused(repo):
    repo.add_garment(garment("g1", "fleece", "fleece"))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    with pytest.raises(InvalidParams):
        repo.accept_suggestion("s1", ["colors"], now=LATER)
    assert repo.get_suggestion("s1").status == "pending"


def test_fr2_accepted_fields_tag_explicit_versus_cascade(repo):
    repo.add_garment(garment("g1", "fleece", "fleece"))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    resolved, _ = repo.accept_suggestion("s1", ["category"], now=LATER)
    vias = {f.field: f.via for f in resolved.accepted_fields}
    assert vias == {
        "category": "explicit",
        "clo": "cascade",
        "layer_role": "cascade",
        "formality": "cascade",
        "wears_before_laundry": "cascade",
    }


def test_fr2_rejecting_leaves_the_garment_alone(repo):
    original = garment("g1", "fleece", "fleece")
    repo.add_garment(original)
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    resolved = repo.reject_suggestion("s1", now=LATER)
    assert resolved.status == "rejected"
    assert resolved.resolved_at == LATER
    assert repo.get_garment("g1") == original


def test_fr2_a_resolved_suggestion_cannot_be_resolved_twice(repo):
    repo.add_garment(garment("g1", "fleece", "fleece"))
    repo.add_suggestion(_suggestion("g1", _payload(category="wool_coat")))
    repo.accept_suggestion("s1", ["category"], now=LATER)
    with pytest.raises(InvalidParams):
        repo.accept_suggestion("s1", ["category"], now=LATER)
    with pytest.raises(InvalidParams):
        repo.reject_suggestion("s1", now=LATER)


def test_fr2_suggestion_invariants_are_enforced_by_the_model():
    with pytest.raises(ValueError, match="resolved_at"):
        AttributeSuggestion(
            id="s",
            garment_id="g",
            source="fixture",
            payload={},
            status="accepted",
            accepted_fields=[],
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="accepted_fields"):
        AttributeSuggestion(
            id="s",
            garment_id="g",
            source="fixture",
            payload={},
            status="accepted",
            created_at=NOW,
            resolved_at=LATER,
        )


def test_fr2_colors_are_coerced_when_accepted(repo):
    repo.add_garment(garment("g1", "fleece", "fleece", colors=("olive",)))
    repo.add_suggestion(
        _suggestion(
            "g1",
            _payload(colors=[{"name": "navy", "hue": None, "neutral": True, "role": "main"}]),
        )
    )
    _, merged = repo.accept_suggestion("s1", ["colors"], now=LATER)
    assert merged.colors[0].name == "navy"
    assert merged.colors[0].neutral is True
