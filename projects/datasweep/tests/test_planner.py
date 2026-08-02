"""FR-7: confidence formulas (D12), tier algebra, policy overrides, items."""

from __future__ import annotations

import pytest
from datasweep.engine.models import (
    RULES,
    AuditKind,
    IssueClass,
    Policy,
    Proposal,
    Stage,
    Tier,
    review_item_id,
    stricter,
)
from datasweep.engine.planner import (
    build_review_items,
    conf_tier,
    confidence_for,
    plan_fixes,
    resolve_tier,
)
from support_datasweep import CONTENT_SHA, ENGINE_VERSION

POLICY = Policy()


# --------------------------------------------------------------------------
# tier algebra
# --------------------------------------------------------------------------


def test_fr7_tier_combination_keeps_the_more_conservative_tier() -> None:
    assert stricter(Tier.AUTO, Tier.REVIEW) is Tier.REVIEW
    assert stricter(Tier.REVIEW, Tier.REPORT) is Tier.REPORT
    assert stricter(Tier.AUTO, Tier.AUTO) is Tier.AUTO
    assert stricter(Tier.REPORT, Tier.AUTO) is Tier.REPORT


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (1.0, Tier.AUTO),
        (0.95, Tier.AUTO),
        (0.949, Tier.REVIEW),
        (0.5, Tier.REVIEW),
        (0.49, Tier.REPORT),
    ],
)
def test_fr7_confidence_maps_to_a_tier(confidence: float, expected: Tier) -> None:
    assert conf_tier(confidence, POLICY.thresholds.auto_confidence) is expected


def test_fr7_reversible_rules_reach_auto_only_at_high_confidence() -> None:
    assert resolve_tier("fix.trim", 1.0, {}, POLICY) is Tier.AUTO
    assert resolve_tier("fix.mojibake", 1.0, {"share": 1.0}, POLICY) is Tier.AUTO
    assert resolve_tier("fix.mojibake", 0.9, {"share": 0.9}, POLICY) is Tier.REVIEW
    assert resolve_tier("fix.mojibake", 0.2, {"share": 0.2}, POLICY) is Tier.REPORT


def test_fr7_interpretive_rules_are_capped_at_review_even_at_confidence_one() -> None:
    for rule in ("fix.label_merge_nn", "fix.excel_serial", "fix.two_digit_year"):
        assert resolve_tier(rule, 1.0, {"ratio": 0.0}, POLICY) is Tier.REVIEW


def test_fr7_analytic_rules_are_capped_at_report() -> None:
    for rule in (
        "detect.outlier",
        "detect.numeric_sentinel",
        "detect.coercion_failure",
        "detect.mixed_number_conventions",
    ):
        assert resolve_tier(rule, 1.0, {}, POLICY) is Tier.REPORT


def test_fr7_collapse_spaces_is_review_in_a_text_column() -> None:
    evidence_text = {"column_type": "text"}
    evidence_other = {"column_type": "categorical"}
    assert confidence_for("fix.collapse_spaces", evidence_text, POLICY) == 0.6
    assert confidence_for("fix.collapse_spaces", evidence_other, POLICY) == 1.0
    assert resolve_tier("fix.collapse_spaces", 0.6, evidence_text, POLICY) is Tier.REVIEW
    assert resolve_tier("fix.collapse_spaces", 1.0, evidence_other, POLICY) is Tier.AUTO


def test_fr7_sentinel_tiers_follow_the_list_and_the_column_type() -> None:
    assert resolve_tier("fix.sentinel_null_hard", 1.0, {}, POLICY) is Tier.AUTO
    assert resolve_tier("fix.sentinel_null_soft", 0.7, {}, POLICY) is Tier.REVIEW


def test_fr7_ambiguous_number_rule_follows_d7_5_not_the_generic_mapping() -> None:
    """A column with no decisive cell still owes the user a review item."""
    undecidable = {"n_decisive": 0, "agree": 0.0}
    assert resolve_tier("fix.number_canon_ambiguous", 0.0, undecidable, POLICY) is Tier.REVIEW
    decisive = {"n_decisive": 12, "agree": 1.0}
    assert resolve_tier("fix.number_canon_ambiguous", 1.0, decisive, POLICY) is Tier.AUTO
    too_few = {"n_decisive": 2, "agree": 1.0}
    assert resolve_tier("fix.number_canon_ambiguous", 1.0, too_few, POLICY) is Tier.REVIEW
    disagreeing = {"n_decisive": 12, "agree": 0.8}
    assert resolve_tier("fix.number_canon_ambiguous", 0.8, disagreeing, POLICY) is Tier.REVIEW


def test_fr7_ambiguous_date_rule_is_always_review() -> None:
    assert resolve_tier("fix.date_canon_ambiguous", 0.0, {}, POLICY) is Tier.REVIEW
    assert resolve_tier("fix.date_canon_ambiguous", 1.0, {}, POLICY) is Tier.REVIEW


def test_fr7_mixed_currency_is_always_review() -> None:
    assert confidence_for("fix.currency_mixed", {}, POLICY) == pytest.approx(0.4)
    assert resolve_tier("fix.currency_mixed", 0.4, {}, POLICY) is Tier.REVIEW


def test_fr7_fingerprint_merge_needs_dominance_and_confidence() -> None:
    strong = {"dominance": 0.97}
    assert resolve_tier("fix.label_merge_fingerprint", 0.97, strong, POLICY) is Tier.AUTO
    middling = {"dominance": 0.85}
    assert resolve_tier("fix.label_merge_fingerprint", 0.85, middling, POLICY) is Tier.REVIEW
    weak = {"dominance": 0.6}
    assert resolve_tier("fix.label_merge_fingerprint", 0.6, weak, POLICY) is Tier.REVIEW


def test_fr7_nearest_neighbour_confidence_formula() -> None:
    assert confidence_for("fix.label_merge_nn", {"ratio": 0.0}, POLICY) == pytest.approx(1.0)
    assert confidence_for("fix.label_merge_nn", {"ratio": 0.025}, POLICY) == pytest.approx(0.5)
    assert confidence_for("fix.label_merge_nn", {"ratio": 0.05}, POLICY) == pytest.approx(0.0)


def test_fr7_every_rule_id_in_d12_has_a_spec() -> None:
    expected = {
        "fix.trim",
        "fix.nbsp",
        "fix.zero_width",
        "fix.nfc",
        "fix.collapse_spaces",
        "fix.mojibake",
        "fix.sentinel_null_hard",
        "fix.sentinel_null_soft",
        "detect.numeric_sentinel",
        "fix.number_canon",
        "fix.number_canon_ambiguous",
        "detect.mixed_number_conventions",
        "fix.currency_strip",
        "fix.currency_mixed",
        "fix.strip_apostrophe",
        "fix.date_canon",
        "fix.date_canon_ambiguous",
        "fix.excel_serial",
        "fix.two_digit_year",
        "fix.label_merge_fingerprint",
        "fix.label_merge_nn",
        "fix.drop_duplicate_row",
        "fix.pad_row",
        "fix.dedupe_header",
        "fix.overflow_column",
        "fix.synthetic_headers",
        "detect.outlier",
        "detect.coercion_failure",
    }
    assert expected <= set(RULES)


# --------------------------------------------------------------------------
# policy overrides
# --------------------------------------------------------------------------


def test_fr7_policy_can_switch_a_rule_off() -> None:
    policy = Policy.model_validate({"tiers": {"fix.drop_duplicate_row": "off"}})
    assert resolve_tier("fix.drop_duplicate_row", 1.0, {}, policy) is None
    assert policy.rule_enabled("fix.drop_duplicate_row") is False
    assert policy.disabled_rules() == ["fix.drop_duplicate_row"]


def test_fr7_policy_can_demote_a_rule() -> None:
    policy = Policy.model_validate({"tiers": {"fix.label_merge_nn": "report"}})
    assert resolve_tier("fix.label_merge_nn", 0.9, {"ratio": 0.005}, policy) is Tier.REPORT


def test_fr7_policy_override_cannot_promote_past_the_confidence_gate() -> None:
    policy = Policy.model_validate({"tiers": {"fix.excel_serial": "auto"}})
    assert resolve_tier("fix.excel_serial", 0.6, {}, policy) is Tier.REVIEW


def test_fr7_disabling_a_detector_family_disables_its_rules() -> None:
    policy = Policy.model_validate({"detectors": {"dup": False}})
    assert resolve_tier("fix.drop_duplicate_row", 1.0, {}, policy) is None


def test_fr7_unknown_rule_id_in_policy_is_a_validation_error() -> None:
    with pytest.raises(ValueError, match="unknown rule id"):
        Policy.model_validate({"tiers": {"fix.nope": "off"}})


def test_fr7_unknown_policy_key_is_a_validation_error() -> None:
    with pytest.raises(ValueError):
        Policy.model_validate({"general": {"settle_secondz": 3}})


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------


def _proposal(rule: str, **kwargs) -> Proposal:
    payload = {"row": 0, "col": 0, "before": "a", "after": "b"}
    payload.update(kwargs)
    return Proposal(rule=rule, **payload)


def test_fr7_plan_drops_identity_cell_changes() -> None:
    fixes = plan_fixes([_proposal("fix.trim", after="a")], POLICY, ["name"])
    assert fixes == []


def test_fr7_plan_orders_by_stage_then_column_then_row() -> None:
    proposals = [
        _proposal(
            "fix.drop_duplicate_row",
            kind=AuditKind.ROW_DROP,
            row=5,
            col=None,
            before=None,
            after=None,
            before_row=["x"],
        ),
        _proposal("fix.trim", row=3, col=1),
        _proposal("fix.trim", row=1, col=1),
        _proposal("fix.mojibake", row=9, col=0, evidence={"share": 1.0}),
    ]
    fixes = plan_fixes(proposals, POLICY, ["a", "b"])
    assert [(fix.stage, fix.col, fix.row) for fix in fixes] == [
        (Stage.ENC, 0, 9),
        (Stage.WS, 1, 1),
        (Stage.WS, 1, 3),
        (Stage.DUP, None, 5),
    ]


def test_fr7_plan_stamps_class_stage_and_column_name() -> None:
    fix = plan_fixes([_proposal("fix.trim", col=1)], POLICY, ["a", "amount"])[0]
    assert fix.klass is IssueClass.WS
    assert fix.stage is Stage.WS
    assert fix.col_name == "amount"
    assert fix.tier is Tier.AUTO
    assert fix.confidence == 1.0


def test_fr7_composite_cell_takes_the_strictest_sub_rule() -> None:
    proposal = _proposal(
        "fix.trim",
        evidence={"rules": ["fix.trim", "fix.collapse_spaces"], "column_type": "text"},
    )
    fix = plan_fixes([proposal], POLICY, ["comment"])[0]
    assert fix.tier is Tier.REVIEW
    assert fix.confidence == pytest.approx(0.6)


# --------------------------------------------------------------------------
# review items (FR-11, DATA_MODEL §2.6)
# --------------------------------------------------------------------------


def _review_fixes() -> list:
    proposals = [
        _proposal(
            "fix.label_merge_nn",
            row=2,
            col=1,
            before="Missisippi",
            after="Mississippi",
            group="1:Missisippi→Mississippi",
            evidence={"ratio": 0.01},
        ),
        _proposal(
            "fix.label_merge_nn",
            row=7,
            col=1,
            before="Missisippi",
            after="Mississippi",
            group="1:Missisippi→Mississippi",
            evidence={"ratio": 0.01},
        ),
        _proposal(
            "fix.excel_serial",
            row=4,
            col=2,
            before="44927",
            after="2023-01-01",
            group="2:excel_serial",
            evidence={},
        ),
    ]
    return plan_fixes(proposals, POLICY, ["a", "state", "restock_date"])


def test_fr11_review_items_group_cells_into_one_decision() -> None:
    items, fixes = build_review_items(
        _review_fixes(),
        content_sha256=CONTENT_SHA,
        policy_hash="0123456789abcdef",
        engine_version=ENGINE_VERSION,
    )
    assert len(items) == 2
    merge = next(item for item in items if item.rule == "fix.label_merge_nn")
    assert merge.affected_cells == 2
    assert merge.proposal["cells"][0]["row"] == 2
    assert merge.proposal["canonical"] == "Mississippi"
    assert all(fix.item_id for fix in fixes if fix.tier is Tier.REVIEW)


def test_fr11_item_ids_are_content_derived_and_stable() -> None:
    kwargs = {
        "content_sha256": CONTENT_SHA,
        "policy_hash": "0123456789abcdef",
        "engine_version": ENGINE_VERSION,
    }
    first, _ = build_review_items(_review_fixes(), **kwargs)
    second, _ = build_review_items(_review_fixes(), **kwargs)
    assert [item.id for item in first] == [item.id for item in second]
    assert all(len(item.id) == 8 for item in first)

    other, _ = build_review_items(_review_fixes(), **{**kwargs, "policy_hash": "ffffffffffffffff"})
    assert [item.id for item in other] != [item.id for item in first]


def test_fr16_item_id_carries_no_run_id_or_timestamp() -> None:
    computed = review_item_id(
        content_sha256=CONTENT_SHA,
        policy_hash="0123456789abcdef",
        engine_version=ENGINE_VERSION,
        rule="fix.date_canon_ambiguous",
        col_index=4,
        first_cell_row=0,
    )
    assert computed == review_item_id(
        content_sha256=CONTENT_SHA,
        policy_hash="0123456789abcdef",
        engine_version=ENGINE_VERSION,
        rule="fix.date_canon_ambiguous",
        col_index=4,
        first_cell_row=0,
    )
    assert len(computed) == 8
