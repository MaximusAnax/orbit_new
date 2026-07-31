"""FR-3: committed dataset loading and the init-time validation invariants."""

from __future__ import annotations

import json
import shutil

import pytest
from newsalpha.datasets import (
    DATA_DIR,
    DatasetError,
    forbidden_regexes,
    load_datasets,
    reachable_polarities,
    reachable_stages,
)
from newsalpha.models import (
    SIGNAL_ROLES_BY_TYPE,
    Asset,
    AssetKind,
    Direction,
    EventPattern,
    EventPrior,
    EventType,
    Stage,
)


@pytest.fixture
def data_dir(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


def rewrite(path, mutate):
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- #
# The shipped datasets satisfy every FR-3 invariant
# --------------------------------------------------------------------------- #


def test_fr3_committed_datasets_load_and_validate(datasets):
    assert len(datasets.assets) > 200
    assert len(datasets.patterns) >= 14
    assert datasets.templates.footer


def test_fr3_alias_uniqueness_and_prefix_invariants(datasets):
    seen: dict[str, str] = {}
    for asset in datasets.assets.values():
        prefix = {"equity": "eq:", "crypto": "cx:", "index": "idx:"}[asset.kind.value]
        assert asset.id.startswith(prefix)
        assert (asset.benchmark_id is None) == (asset.kind is AssetKind.index)
        assert bool(asset.ambiguous) == bool(asset.context_keywords)
        for alias in asset.aliases:
            assert alias not in seen, f"{alias} on {seen.get(alias)} and {asset.id}"
            seen[alias] = asset.id


def test_fr3_every_event_type_has_at_least_two_patterns(datasets):
    for event_type in EventType:
        assert len(datasets.patterns_for(event_type)) >= 2


def test_fr3_trigger_sets_are_disjoint_across_event_types(datasets):
    owner: dict[str, EventType] = {}
    for pattern in datasets.patterns:
        for trigger in pattern.triggers:
            folded = trigger.casefold()
            if folded in owner:
                assert owner[folded] is pattern.event_type, folded
            owner[folded] = pattern.event_type


def test_fr3_every_pattern_cites_a_real_world_example(datasets):
    for pattern in datasets.patterns:
        assert pattern.real_world_example.strip()
        assert len(pattern.real_world_example.split()) >= 4


def test_fr3_attribute_regexes_compile_with_at_most_one_group(datasets):
    import re

    for pattern in datasets.patterns:
        for regex in pattern.attribute_extractors.values():
            assert re.compile(regex).groups <= 1


def test_fr3_prior_resolution_is_total_over_reachable_combinations(datasets):
    for event_type in EventType:
        for role in SIGNAL_ROLES_BY_TYPE[event_type]:
            for polarity in reachable_polarities(datasets, event_type):
                for kind in ("equity", "crypto"):
                    key = f"{event_type.value}.{role.value}.{polarity}"
                    assert datasets.resolve_prior(key, kind) is not None, (key, kind)


def test_fr3_every_reachable_stage_resolves_to_a_valid_effective_prior(datasets):
    for prior in datasets.priors.values():
        for stage in reachable_stages(datasets, prior.event_type):
            effective = prior.effective(stage)
            assert effective.horizon_bars in (1, 5, 20)


def test_fr3_templates_cover_every_reachable_direction(datasets):
    for prior in datasets.priors.values():
        for stage in reachable_stages(datasets, prior.event_type):
            direction = prior.effective(stage).direction
            if direction is Direction.unclear:
                continue
            assert datasets.templates.resolve(prior.event_type, direction)


def test_fr3_no_template_or_rationale_contains_a_forbidden_term(datasets):
    matchers = forbidden_regexes(datasets.lexicons.forbidden_lexicon)
    blobs = [datasets.templates.footer]
    for template in datasets.templates.templates:
        blobs += [
            template.what_happened,
            template.why_it_matters,
            template.uncertainty_note,
            *template.what_to_watch,
        ]
    for prior in datasets.priors.values():
        blobs += [prior.rationale, prior.source_note]
    for blob in blobs:
        for term, matcher in matchers:
            assert matcher.search(blob) is None, (term, blob[:60])


def test_fr3_venue_lexicon_asset_ids_exist(datasets):
    for surface, asset_id in datasets.lexicons.venue_lexicon.items():
        assert surface.strip()
        if asset_id is not None:
            assert asset_id in datasets.assets


def test_fr3_benchmark_basket_members_exist(datasets):
    for index_id, members in datasets.benchmarks.items():
        assert index_id in datasets.assets
        for member in members:
            if not member.startswith("live_proxy:"):
                assert member in datasets.assets


def test_fr3_crypto_benchmark_is_a_basket_not_a_single_asset(datasets):
    """D-5: BTC is itself a universe asset, so it may not be its own benchmark."""
    basket = datasets.benchmarks["idx:CX"]
    assert len(basket) >= 8
    assert "cx:BTC" in basket


# --------------------------------------------------------------------------- #
# Validation rejects broken datasets, naming the record
# --------------------------------------------------------------------------- #


def test_fr3_duplicate_alias_aborts_init(data_dir):
    def mutate(payload):
        payload["assets"][1]["aliases"] = list(payload["assets"][0]["aliases"])

    rewrite(data_dir / "assets.json", mutate)
    with pytest.raises(DatasetError, match="alias"):
        load_datasets(data_dir)


def test_fr3_ambiguous_alias_without_context_keywords_aborts_init(data_dir):
    def mutate(payload):
        payload["assets"][0]["ambiguous"] = True
        payload["assets"][0]["context_keywords"] = []

    rewrite(data_dir / "assets.json", mutate)
    with pytest.raises(DatasetError, match="context_keywords"):
        load_datasets(data_dir)


def test_fr3_id_prefix_must_match_kind(data_dir):
    def mutate(payload):
        payload["assets"][0]["kind"] = "crypto"

    rewrite(data_dir / "assets.json", mutate)
    with pytest.raises(DatasetError, match="must start with"):
        load_datasets(data_dir)


def test_fr3_pattern_without_a_real_world_example_aborts_init(data_dir):
    def mutate(payload):
        payload["patterns"][0]["real_world_example"] = "  "

    rewrite(data_dir / "patterns.json", mutate)
    with pytest.raises(DatasetError, match="real_world_example"):
        load_datasets(data_dir)


def test_fr3_overlapping_triggers_across_types_abort_init(data_dir):
    def mutate(payload):
        mna = next(p for p in payload["patterns"] if p["event_type"] == "mna")
        mna["triggers"] = [*mna["triggers"], "beat estimates"]

    rewrite(data_dir / "patterns.json", mutate)
    with pytest.raises(DatasetError, match="disjoint"):
        load_datasets(data_dir)


def test_fr3_regex_with_two_capture_groups_aborts_init(data_dir):
    def mutate(payload):
        payload["patterns"][0]["attribute_extractors"] = {"surprise_pct": r"(\d+)(%)"}

    rewrite(data_dir / "patterns.json", mutate)
    with pytest.raises(DatasetError, match="capture groups"):
        load_datasets(data_dir)


def test_fr3_prior_key_containing_a_stage_token_aborts_init(data_dir):
    def mutate(payload):
        payload["priors"][0]["key"] = "mna.target.confirmed"

    rewrite(data_dir / "priors.json", mutate)
    with pytest.raises(DatasetError, match="stage token"):
        load_datasets(data_dir)


def test_fr3_sign_mismatch_between_direction_and_band_aborts_init(data_dir):
    def mutate(payload):
        payload["priors"][0]["direction"] = "bearish"

    rewrite(data_dir / "priors.json", mutate)
    with pytest.raises(DatasetError, match="sign"):
        load_datasets(data_dir)


def test_fr3_magnitude_mismatch_aborts_init(data_dir):
    def mutate(payload):
        payload["priors"][0]["magnitude"] = "major"

    rewrite(data_dir / "priors.json", mutate)
    with pytest.raises(DatasetError, match="magnitude"):
        load_datasets(data_dir)


def test_fr3_missing_prior_row_aborts_init(data_dir):
    def mutate(payload):
        payload["priors"] = [
            p for p in payload["priors"] if p["key"] != "earnings_surprise.subject.beat"
        ]

    rewrite(data_dir / "priors.json", mutate)
    with pytest.raises(DatasetError, match="no row resolves"):
        load_datasets(data_dir)


def test_fr3_forbidden_term_in_a_template_aborts_init(data_dir):
    def mutate(payload):
        payload["templates"][0]["what_happened"] = "You should buy {asset_name}."

    rewrite(data_dir / "templates.json", mutate)
    with pytest.raises(DatasetError, match="forbidden term"):
        load_datasets(data_dir)


def test_fr3_template_missing_a_required_slot_aborts_init(data_dir):
    def mutate(payload):
        payload["templates"][0]["uncertainty_note"] = "No slots here."

    rewrite(data_dir / "templates.json", mutate)
    with pytest.raises(DatasetError, match="uncertainty_note must include"):
        load_datasets(data_dir)


def test_fr3_missing_file_names_the_dataset(tmp_path):
    with pytest.raises(DatasetError, match="missing committed dataset"):
        load_datasets(tmp_path)


# --------------------------------------------------------------------------- #
# Model-level invariants
# --------------------------------------------------------------------------- #


def test_fr3_asset_benchmark_is_set_iff_not_an_index():
    with pytest.raises(ValueError, match="benchmark_id"):
        Asset(id="idx:US", kind=AssetKind.index, symbol="US", name="US", benchmark_id="idx:US")


def test_fr3_pattern_polarity_must_be_valid_for_its_type():
    with pytest.raises(ValueError, match="polarity"):
        EventPattern(
            id="p",
            event_type=EventType.listing,
            triggers=("to list",),
            polarity="beat",
            real_world_example="x y z w",
        )


def test_fr3_prior_role_must_be_signal_bearing():
    with pytest.raises(ValueError, match="signal-bearing role"):
        EventPrior(
            key="mna.mentioned.*",
            kind="*",
            direction=Direction.bullish,
            magnitude="moderate",
            expected_ar_lo=0.01,
            expected_ar_hi=0.03,
            announcement_ar_lo=0.1,
            announcement_ar_hi=0.2,
            horizon_bars=5,
            base_conf=0.8,
            supersession_days=21,
            rationale="r",
            source_note="s",
        )


def test_fr3_stage_override_with_unclear_direction_must_have_a_zero_band(datasets):
    prior = datasets.resolve_prior("mna.acquirer.*", "equity")
    override = prior.stage_overrides[Stage.denied]
    assert override.direction is Direction.unclear
    assert override.mid_expected_ar == 0.0
