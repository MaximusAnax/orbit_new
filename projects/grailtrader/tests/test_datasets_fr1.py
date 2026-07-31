"""FR-1: reference data loading and validation."""

from __future__ import annotations

import copy
import json

import pytest
from grailtrader.datasets import (
    DEFAULT_DATA_DIR,
    build_context,
    load_brands,
    load_conditions,
    load_config,
    load_priors,
    load_templates,
)
from grailtrader.engine.impact import reachable_prior_keys
from grailtrader.engine.validate import DatasetValidationError, validate_datasets
from grailtrader.models import (
    AdviceTemplateCatalog,
    AdvisorConfig,
    Brand,
    ConditionTable,
    ImpactPrior,
)
from pydantic import ValidationError


def raw(name: str) -> dict:
    with (DEFAULT_DATA_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_all(**overrides):
    payload = {
        "brands": load_brands(),
        "priors": load_priors(),
        "conditions": load_conditions(),
        "templates": load_templates(),
        "config": load_config(),
    }
    payload.update(overrides)
    validate_datasets(**payload)


def test_fr1_committed_datasets_validate(ctx):
    assert ctx.config.config_version
    assert len(ctx.gazetteer.brands) >= 25
    assert len(ctx.gazetteer.eras) >= 55
    assert set(reachable_prior_keys()) <= set(ctx.priors)


def test_fr1_every_reachable_prior_key_resolves_to_one_row(ctx):
    for key, kind in reachable_prior_keys().items():
        prior = ctx.priors[key]
        assert prior.target is kind
        assert prior.rationale.strip()
        assert prior.source_note.strip()


def test_fr1_brand_eras_are_non_overlapping_and_ordered():
    for brand in load_brands():
        starts = [era.start for era in brand.eras]
        assert starts == sorted(starts), brand.id
        open_eras = [era for era in brand.eras if era.end is None]
        assert len(open_eras) <= 1, brand.id
        assert brand.eras


def test_fr1_rejects_overlapping_eras():
    payload = copy.deepcopy(raw("brands.json")["brands"][1])
    payload["eras"][1]["start"] = payload["eras"][0]["start"]
    with pytest.raises(ValidationError, match=r"overlap|chronological"):
        Brand.model_validate(payload)


def test_fr1_rejects_two_open_eras():
    payload = copy.deepcopy(raw("brands.json")["brands"][0])
    payload["eras"][0]["end"] = None
    with pytest.raises(ValidationError, match="open era"):
        Brand.model_validate(payload)


def test_fr1_rejects_duplicate_era_ids():
    payload = copy.deepcopy(raw("brands.json")["brands"][0])
    payload["eras"][1]["id"] = payload["eras"][0]["id"]
    with pytest.raises(ValidationError, match="duplicate era"):
        Brand.model_validate(payload)


def test_fr1_rejects_duplicate_brand_alias():
    brands = list(load_brands())
    clashing = brands[1].model_copy(update={"aliases": (*brands[1].aliases, brands[0].name)})
    with pytest.raises(DatasetValidationError, match="alias"):
        validate_all(brands=[brands[0], clashing, *brands[2:]])


def test_fr1_rejects_missing_prior_row():
    priors = [p for p in load_priors() if p.key != "brand_scandal.severe"]
    with pytest.raises(DatasetValidationError, match=r"brand_scandal\.severe"):
        validate_all(priors=priors)


def test_fr1_rejects_prior_whose_direction_contradicts_its_components():
    row = raw("impact_priors.json")["priors"][0]
    row = {**row, "direction": "bearish"}
    with pytest.raises(ValidationError, match="bearish"):
        ImpactPrior.model_validate(row)


def test_fr1_rejects_prior_rationale_with_forbidden_lexicon():
    priors = list(load_priors())
    tainted = priors[0].model_copy(update={"rationale": "This is a sure thing every time."})
    with pytest.raises(DatasetValidationError, match="forbidden lexicon"):
        validate_all(priors=[tainted, *priors[1:]])


def test_fr1_rejects_prior_outside_sanity_bounds():
    row = {**raw("impact_priors.json")["priors"][0], "permanent_pct": 0.9}
    with pytest.raises(ValidationError):
        ImpactPrior.model_validate(row)


def test_fr1_condition_multipliers_strictly_decrease():
    payload = copy.deepcopy(raw("conditions.json"))
    payload["grades"][2]["multiplier"] = payload["grades"][1]["multiplier"]
    with pytest.raises(ValidationError, match="strictly decrease"):
        ConditionTable.model_validate(payload)


def test_fr1_condition_aliases_are_unique_across_grades():
    payload = copy.deepcopy(raw("conditions.json"))
    payload["grades"][2]["platform_aliases"].append("NWT")
    with pytest.raises(ValidationError, match="maps to both"):
        ConditionTable.model_validate(payload)


def test_fr1_rejects_template_missing_required_placeholder():
    payload = copy.deepcopy(raw("advice_templates.json"))
    payload["templates"][0]["fee_note"] = "Fees & liquidity: some fees apply."
    with pytest.raises(DatasetValidationError, match="fee_assumption_pct"):
        validate_all(templates=AdviceTemplateCatalog.model_validate(payload))


def test_fr1_rejects_template_containing_forbidden_word():
    payload = copy.deepcopy(raw("advice_templates.json"))
    payload["templates"][0]["headline"] += " — a sure thing"
    with pytest.raises(DatasetValidationError, match="forbidden lexicon"):
        validate_all(templates=AdviceTemplateCatalog.model_validate(payload))


def test_fr1_rejects_template_missing_required_marker():
    payload = copy.deepcopy(raw("advice_templates.json"))
    payload["templates"][0]["uncertainty"] = "How sure: {confidence} · Falsifier: {falsifier}"
    with pytest.raises(DatasetValidationError, match="Confidence:"):
        validate_all(templates=AdviceTemplateCatalog.model_validate(payload))


def test_fr1_rejects_unreachable_hold_reason_template():
    payload = copy.deepcopy(raw("advice_templates.json"))
    payload["templates"] = [t for t in payload["templates"] if t["id"] != "hold-no-baseline"]
    with pytest.raises(DatasetValidationError, match="no_baseline"):
        validate_all(templates=AdviceTemplateCatalog.model_validate(payload))


def test_fr1_rejects_theta_not_equal_to_fee_assumption():
    payload = copy.deepcopy(raw("advisor_config.json"))
    payload["advisor"]["theta_buy"] = 0.1
    with pytest.raises(ValidationError, match="theta_buy == theta_sell == fee_assumption_pct"):
        AdvisorConfig.model_validate(payload)


def test_fr1_rejects_wrong_fence_floor():
    payload = copy.deepcopy(raw("advisor_config.json"))
    payload["index"]["fence_floor_log"] = 0.5
    with pytest.raises(ValidationError, match="fence_floor_log"):
        AdvisorConfig.model_validate(payload)


def test_fr1_rejects_q_index_not_matching_stale_max():
    payload = copy.deepcopy(raw("advisor_config.json"))
    payload["advisor"]["q_index"][-1]["max_stale_weeks"] = 6
    with pytest.raises(ValidationError, match="stale_max_weeks"):
        AdvisorConfig.model_validate(payload)


def test_fr1_rejects_bucket_edges_outside_confidence_range():
    payload = copy.deepcopy(raw("advisor_config.json"))
    payload["calibration"]["bucket_edges"] = [0.02, 0.62]
    with pytest.raises(ValidationError, match="bucket_edges"):
        AdvisorConfig.model_validate(payload)


def test_fr1_missing_dataset_names_the_file(tmp_path):
    with pytest.raises(DatasetValidationError, match=r"brands\.json"):
        build_context(tmp_path)
