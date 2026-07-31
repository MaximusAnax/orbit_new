"""Seeded generator for the committed GrailTrader eval fixtures (EVALS "Fixture strategy").

Run it as::

    uv run python grailtrader/evals/generate_scenario.py --seed 20260731            # scenario A
    uv run python grailtrader/evals/generate_scenario.py --seed 20260801 --mismatch  # scenario B

Everything it writes is deterministic given the seed, and ``evals/run.py
--regen-check`` re-runs it and diffs against the committed files.

**Ground truth never comes from the system under evaluation.** The latent price
level, the planted impact paths, the chain-linked parent truth, the clean-sale
counts behind the coverage floor and the planted-outlier labels are all computed
here, from construction parameters. The only things imported from the package are
(a) the condition table (shared *input* data — scenario A uses it verbatim,
scenario B perturbs it) and (b) the content-derived id helpers, so that the truth
tables can be joined to events by id. No engine estimator is used.

Derivations reproduced from EVALS.md (and the two fixture-composition changes
recorded in docs/REVIEW.md, findings G1 and G2):

*Index sampling error.* A 4-week window on a dense stratum holds n = 28-48
surviving sales; the sample median of a log-normal with sigma = 0.20 has
log-scale sd ~ 1.2533 * 0.20/sqrt(n) = 0.036-0.047, so median |error| =
0.6745 * sd ~ 0.024-0.032 and p90 = 1.6449 * sd ~ 0.060-0.078.

*Why the planted diffusion lag is 3-12 weeks and not a flat 2 (REVIEW G1).* The
recovered index at week t is a median over sales sold in weeks t-3..t, so it
estimates the truth at about t-1.5. An impact of size D_ln (log) phased in
linearly over L weeks moves the truth by D_ln/L per week, so inside the phase-in
the recovered index trails the truth by ~1.5 * D_ln/L. The largest planted move
is a designer death (ln 1.45 = 0.372); at L = 2 that is a 0.28 tracking error on
~10 % of cells, which alone breaks the M1a-p90 <= 0.14 gate for *any* correct
implementation, and it also makes the advisor's buy leg a coin flip (the truth
has finished moving before the entry week t+1). Slow, structural re-ratings
(departures, appointments, scandals) therefore diffuse over 8-12 weeks and fast
hype events (a death's viral spike, collabs, co-signs) over 3-4 weeks, which is
also the more faithful reading of SCOPE D-9.

*Why the latent GBM is 0.8 %/week and not 2 % (REVIEW G2).* An event stays active
for up to A_e = 26 weeks (FR-6), and FR-8 holds the baseline B fixed at the last
observation before the earliest active event. The gap the advisor trades is
therefore exposed to the stratum's own random walk for the whole window: at
2 %/week the 20-week drift has sd 0.089, so |r_hat| clears theta = 0.12 on ~27 %
of late-window cells purely from noise. Those cells are coin flips by
construction (a driftless random walk has no mean reversion), which caps M2a near
0.65 no matter how good the advisor is. At 0.8 %/week the same figure is ~7 %.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from grailtrader.datasets import load_conditions
from grailtrader.ids import event_id, garment_id
from grailtrader.models import EventType as _EventType
from grailtrader.models import identity_attrs_for

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# --------------------------------------------------------------------------- #
# Construction parameters                                                       #
# --------------------------------------------------------------------------- #

#: Item-level log-normal price noise (scenario A). EVALS "listings.jsonl".
ITEM_SIGMA_A = 0.20
#: Scenario B: Student-t(nu = 4) scaled to this sd.
ITEM_SIGMA_B = 0.24
T_DF = 4

#: Weekly latent GBM volatility, zero drift (REVIEW G2).
GBM_SIGMA = 0.008

#: Condition mix at sale (EVALS "listings.jsonl").
CONDITION_MIX: tuple[tuple[str, float], ...] = (
    ("new", 0.10),
    ("excellent", 0.45),
    ("good", 0.30),
    ("fair", 0.12),
    ("poor", 0.03),
)

#: Planted pollution rates and their latent multiplier bands.
FAKE_RATE, FAKE_BAND = 0.020, (0.15, 0.30)
MISLABEL_RATE, MISLABEL_BAND = 0.010, (3.0, 4.5)
AMBIGUOUS_RATE, AMBIGUOUS_BAND = 0.005, (0.40, 0.60)
#: Ask-only listings per leaf-week (Poisson), priced above latent.
ASK_LAMBDA = 0.70
ASK_BAND = (1.25, 1.45)

#: Category-typical USD anchors for an excellent-condition piece.
CATEGORY_ANCHOR: dict[str, float] = {
    "outerwear": 940.0,
    "tailoring": 700.0,
    "knitwear": 430.0,
    "tops": 260.0,
    "bottoms": 320.0,
    "denim": 300.0,
    "footwear": 480.0,
    "accessories": 220.0,
}

#: Planted impact anchors, written from the case literature *independently* of
#: data/impact_priors.json (EVALS: correlated through the literature, never
#: through code). Tuple = (permanent, transient, half-life weeks, diffusion weeks).
#:
#: * death / house closure: Off-White and Louis Vuitton x Nike on StockX after
#:   Virgil Abloh's death (Nov 2021) - double-digit spike within days, partial
#:   retrace over weeks, permanently finite supply. Viral: fast diffusion.
#: * resignation / ousted: the post-Philo "Old Celine" re-rating (The RealReal
#:   resale reporting, 2018) - a slow, months-long archive re-rating.
#: * appointment: Lyst Index brand-heat moves - slow and modest.
#: * collab: Supreme x Louis Vuitton (2017), Nike x Off-White "The Ten" - a sharp
#:   hype cycle that fades.
#: * co-sign: the Bella Hadid-driven vintage Jean Paul Gaultier revival - a
#:   search-spike shape with no permanent component.
#: * scandal: the Balenciaga campaign scandal (Nov 2022) and the adidas-Yeezy
#:   split (Oct 2022) - a step down plus a demand shock that partly retraces.
IMPACT_ANCHORS: dict[str, tuple[float, float, float, int]] = {
    "designer_departure.death": (0.21, 0.27, 6.5, 3),
    "designer_departure.house_closure": (0.19, 0.13, 10.0, 8),
    "designer_departure.resignation": (0.13, 0.11, 7.5, 10),
    "designer_departure.ousted": (0.13, 0.15, 7.0, 10),
    "designer_appointment.acclaimed": (0.065, 0.085, 8.0, 12),
    "designer_appointment.neutral": (0.022, 0.043, 8.0, 12),
    "designer_appointment.unproven": (0.004, 0.032, 8.0, 12),
    "designer_appointment.predecessor": (0.065, 0.043, 8.0, 12),
    "collab_announcement": (0.021, 0.105, 4.0, 4),
    "celebrity_cosign": (0.0, 0.105, 3.0, 3),
    "runway_reception.acclaimed": (0.021, 0.052, 6.0, 6),
    "runway_reception.panned": (0.0, -0.042, 6.0, 6),
    "brand_scandal.minor": (-0.052, -0.052, 6.0, 8),
    "brand_scandal.moderate": (-0.105, -0.105, 8.0, 8),
    "brand_scandal.severe": (-0.155, -0.155, 10.0, 8),
}
#: Celebrity co-sign transients scale by tier before the path is built (FR-6).
TIER_SCALE = {"a_list": 1.0, "b_list": 0.5, "niche": 0.25}
#: Seeded dispersion around the anchors (EVALS: sigma = 25 %).
ANCHOR_SIGMA = 0.25
#: A fizzled collab / co-sign is planted with a tenth of its nominal move.
FIZZLE_SCALE = 0.10

#: Index-build constants the coverage truth is derived against (mirror
#: data/advisor_config.json; asserted equal in evals/test_gates.py).
WINDOW_WEEKS = 4
MIN_SALES = 5
PARENT_WEIGHT_WINDOW_WEEKS = 8


# --------------------------------------------------------------------------- #
# Scenario shape                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LeafSpec:
    category: str
    lam: float
    density: str  # "dense" | "sparse"


@dataclass(frozen=True)
class EraSpec:
    suffix: str
    designer: str
    label: str
    start: str
    end: str | None
    leaves: tuple[LeafSpec, ...]
    notes: str = ""


@dataclass(frozen=True)
class BrandSpec:
    id: str
    name: str
    aliases: tuple[str, ...]
    notes: str
    price_factor: float
    eras: tuple[EraSpec, ...]


@dataclass(frozen=True)
class EventSpec:
    week: int
    brand: str
    kind: str
    attrs: dict[str, Any]
    era: str | None = None
    source: str = "news"
    refs: tuple[str, ...] = ()
    fizzled: bool = False
    contradicts_prior: bool = False
    notes: str = ""


def _dense(category: str, lam: float) -> LeafSpec:
    return LeafSpec(category, lam, "dense")


def _sparse(category: str, lam: float) -> LeafSpec:
    return LeafSpec(category, lam, "sparse")


# 8 fictional brands / 18 eras / 36 leaf strata (30 dense + 6 sparse), modelled on
# documented archetypes. Fixture brands are fictional by design (SCOPE D-14):
# scandal and death events must never be attached to real brands or people.
SCENARIO_A_BRANDS: tuple[BrandSpec, ...] = (
    BrandSpec(
        "maison-vantorre",
        "Maison Vantorre",
        ("Vantorre", "MV"),
        "Founder-era archive house; the founder era is the collectible one.",
        1.6,
        (
            EraSpec(
                "vantorre",
                "Elise Vantorre",
                "Elise Vantorre era",
                "1994-01",
                "2019-06",
                (_sparse("outerwear", 1.55), _dense("tailoring", 8.8), _dense("knitwear", 8.25)),
                "the archive era",
            ),
            EraSpec(
                "corbin",
                "Marc Corbin",
                "Marc Corbin era",
                "2019-07",
                None,
                (_dense("tops", 9.9), _dense("denim", 9.35)),
            ),
        ),
    ),
    BrandSpec(
        "atelier-kessler",
        "Atelier Kessler",
        ("Kessler",),
        "Deconstructionist house; two well-documented designer tenures.",
        1.25,
        (
            EraSpec(
                "kessler",
                "Ada Kessler",
                "Ada Kessler era",
                "1996-01",
                "2023-04",
                (_dense("outerwear", 10.45), _sparse("knitwear", 1.6), _dense("accessories", 8.8)),
            ),
            EraSpec(
                "renard",
                "Yves Renard",
                "Yves Renard era",
                "2023-05",
                None,
                (_dense("tops", 11.0), _dense("bottoms", 8.8)),
            ),
        ),
    ),
    BrandSpec(
        "nordkant",
        "Nordkant",
        ("Nord Kant",),
        "Quiet heritage brand; steady demand, little news flow.",
        0.9,
        (
            EraSpec(
                "holm",
                "Ingrid Holm",
                "Ingrid Holm era",
                "1988-01",
                "2021-02",
                (_dense("outerwear", 9.35), _dense("knitwear", 9.9)),
            ),
            EraSpec(
                "dahl",
                "Petter Dahl",
                "Petter Dahl era",
                "2021-03",
                None,
                (_dense("tailoring", 8.25), _dense("tops", 10.45)),
            ),
        ),
    ),
    BrandSpec(
        "voltaire-nine",
        "Voltaire Nine",
        ("V9", "Voltaire 9"),
        "Hype-collab streetwear label; frequent drops and celebrity moments.",
        1.1,
        (
            EraSpec(
                "okafor",
                "Deji Okafor",
                "Deji Okafor era",
                "2011-01",
                "2023-09",
                (_sparse("footwear", 1.5), _dense("tops", 12.0)),
            ),
            EraSpec(
                "mireille",
                "Sana Mireille",
                "Sana Mireille era",
                "2023-10",
                "2024-11",
                (_dense("outerwear", 8.8), _dense("denim", 10.45)),
            ),
            EraSpec(
                "sable",
                "Tomas Sable",
                "Tomas Sable era",
                "2024-12",
                None,
                (_dense("accessories", 9.9), _dense("bottoms", 9.35)),
            ),
        ),
    ),
    BrandSpec(
        "serrano-bloc",
        "Serrano Bloc",
        ("Serrano",),
        "Scandal-hit megabrand; three tenures, heavy and mixed news flow.",
        1.0,
        (
            EraSpec(
                "serrano",
                "Luis Serrano",
                "Luis Serrano era",
                "1999-01",
                "2020-08",
                (_sparse("outerwear", 1.6), _dense("tailoring", 9.35)),
            ),
            EraSpec(
                "bloc",
                "Nina Bloc",
                "Nina Bloc era",
                "2020-09",
                "2023-12",
                (_sparse("knitwear", 1.45), _dense("denim", 9.9)),
            ),
            EraSpec(
                "iversen",
                "Kai Iversen",
                "Kai Iversen era",
                "2024-01",
                None,
                (_dense("tops", 11.55), _dense("footwear", 8.25)),
            ),
        ),
    ),
    BrandSpec(
        "hausmann-studio",
        "Hausmann Studio",
        ("Hausmann",),
        "Cult tailoring studio; founder era closed by the founder's death.",
        1.35,
        (
            EraSpec(
                "hausmann",
                "Otto Hausmann",
                "Otto Hausmann era",
                "1991-01",
                "2024-06",
                (_sparse("outerwear", 1.5), _dense("tailoring", 9.9), _dense("knitwear", 7.7)),
            ),
            EraSpec(
                "petrov",
                "Lena Petrov",
                "Lena Petrov era",
                "2024-07",
                None,
                (_dense("tops", 8.8), _dense("accessories", 8.25)),
            ),
        ),
    ),
    BrandSpec(
        "kite-and-anvil",
        "Kite & Anvil",
        ("Kite and Anvil", "K&A"),
        "Workwear label that receives no events in this scenario (hold correctness).",
        0.75,
        (
            EraSpec(
                "kite",
                "Rosa Kite",
                "Rosa Kite era",
                "2004-01",
                "2022-05",
                (_dense("outerwear", 7.7), _dense("denim", 8.8)),
            ),
            EraSpec(
                "anvil",
                "Bo Anvil",
                "Bo Anvil era",
                "2022-06",
                None,
                (_dense("knitwear", 8.25),),
            ),
        ),
    ),
    BrandSpec(
        "okonkwo-atelier",
        "Okonkwo Atelier",
        ("Okonkwo",),
        "Small atelier that receives no events in this scenario (hold correctness).",
        1.15,
        (
            EraSpec(
                "okonkwo",
                "Ify Okonkwo",
                "Ify Okonkwo era",
                "2009-01",
                "2023-01",
                (_dense("tops", 8.8),),
            ),
            EraSpec(
                "ferro",
                "Gian Ferro",
                "Gian Ferro era",
                "2023-02",
                None,
                (_dense("accessories", 7.7),),
            ),
        ),
    ),
)


def _ev(
    week: int,
    brand: str,
    kind: str,
    attrs: dict[str, Any],
    *,
    era: str | None = None,
    source: str = "news",
    refs: Sequence[str] = (),
    fizzled: bool = False,
    contradicts_prior: bool = False,
    notes: str = "",
) -> EventSpec:
    return EventSpec(
        week=week,
        brand=brand,
        kind=kind,
        attrs=attrs,
        era=era,
        source=source,
        refs=tuple(refs),
        fizzled=fizzled,
        contradicts_prior=contradicts_prior,
        notes=notes,
    )


# 44 typed events. Counts (asserted in _check_scenario): 10 departures
# (4 resignation / 3 ousted / 2 death / 1 house_closure), 6 appointments
# (3 acclaimed / 2 neutral / 1 unproven), 8 collabs (2 fizzle), 10 co-signs
# (3 a_list / 4 b_list / 3 niche, 3 fizzle), 4 runway (3 acclaimed / 1 panned),
# 6 scandals (2 minor / 2 moderate / 2 severe). Two brands receive nothing.
# No impact-bearing event sits after week 90 (of 120), so even an H* = 26 call on
# the last event grades inside the window. Weeks 40-42 and 52-54 are deliberate
# hype clusters: a death plus a collab plus an a_list co-sign, which is what makes
# the observed index overshoot its own permanent level far enough for the
# sell-into-decay regime to be reachable at H* = 26 (EVALS M0d).
SCENARIO_A_EVENTS: tuple[EventSpec, ...] = (
    # -- maison-vantorre -------------------------------------------------------
    _ev(
        10,
        "maison-vantorre",
        "celebrity_cosign",
        {"celebrity": "Rae Okonjo", "tier": "b_list"},
        era="maison-vantorre:vantorre",
        source="social",
        refs=("social:rae-okonjo-vantorre",),
    ),
    _ev(
        22,
        "maison-vantorre",
        "runway_reception",
        {"polarity": "acclaimed"},
        refs=("https://www.modewire.example/vantorre-ss24",),
    ),
    _ev(
        40,
        "maison-vantorre",
        "designer_departure",
        {"reason": "death"},
        era="maison-vantorre:vantorre",
        source="news",
        refs=("https://www.modewire.example/vantorre-obituary", "manual:vantorre-death"),
        notes="Founder died; the house says the archive era is closed for good.",
    ),
    _ev(
        41,
        "maison-vantorre",
        "collab_announcement",
        {"counterparty": "Aldwin Sports"},
        refs=("https://www.threadledger.example/vantorre-aldwin",),
    ),
    _ev(
        42,
        "maison-vantorre",
        "celebrity_cosign",
        {"celebrity": "Juno Vasquez", "tier": "a_list"},
        era="maison-vantorre:vantorre",
        source="social",
        refs=("social:juno-vasquez-tribute",),
    ),
    _ev(
        62,
        "maison-vantorre",
        "brand_scandal",
        {"severity": "minor"},
        refs=("https://www.threadledger.example/vantorre-supplier",),
    ),
    _ev(
        78,
        "maison-vantorre",
        "designer_appointment",
        {"designer": "Marc Corbin", "acclaim": "neutral"},
        refs=("https://www.modewire.example/vantorre-corbin",),
    ),
    # -- atelier-kessler -------------------------------------------------------
    _ev(
        14,
        "atelier-kessler",
        "designer_departure",
        {"reason": "resignation"},
        era="atelier-kessler:kessler",
        source="manual",
        refs=("manual:kessler-exit",),
        notes="Announced at the end of the couture week.",
    ),
    _ev(
        26,
        "atelier-kessler",
        "collab_announcement",
        {"counterparty": "Bramley Optics"},
        refs=("https://www.threadledger.example/kessler-bramley",),
        fizzled=True,
    ),
    _ev(
        38,
        "atelier-kessler",
        "celebrity_cosign",
        {"celebrity": "Perrin Ash", "tier": "niche"},
        era="atelier-kessler:kessler",
        source="social",
        refs=("social:perrin-ash-kessler",),
    ),
    _ev(
        50,
        "atelier-kessler",
        "designer_appointment",
        {"designer": "Yves Renard", "acclaim": "acclaimed"},
        refs=(
            "https://www.modewire.example/kessler-renard",
            "https://www.threadledger.example/kessler-renard",
        ),
    ),
    _ev(
        70,
        "atelier-kessler",
        "runway_reception",
        {"polarity": "acclaimed"},
        refs=("https://www.modewire.example/kessler-aw25",),
    ),
    _ev(
        86,
        "atelier-kessler",
        "brand_scandal",
        {"severity": "moderate"},
        refs=("https://www.threadledger.example/kessler-factory",),
    ),
    # -- nordkant --------------------------------------------------------------
    _ev(
        18,
        "nordkant",
        "designer_departure",
        {"reason": "ousted"},
        era="nordkant:holm",
        refs=("https://www.modewire.example/nordkant-holm",),
    ),
    _ev(
        44,
        "nordkant",
        "celebrity_cosign",
        {"celebrity": "Isa Lindqvist", "tier": "niche"},
        source="social",
        refs=("social:isa-lindqvist-nordkant",),
        fizzled=True,
    ),
    _ev(
        72,
        "nordkant",
        "designer_appointment",
        {"designer": "Tove Sand", "acclaim": "unproven"},
        refs=("https://www.modewire.example/nordkant-sand",),
    ),
    _ev(
        74,
        "nordkant",
        "designer_departure",
        {"reason": "resignation"},
        era="nordkant:dahl",
        source="manual",
        refs=("manual:nordkant-dahl",),
    ),
    # -- voltaire-nine ---------------------------------------------------------
    _ev(
        8,
        "voltaire-nine",
        "collab_announcement",
        {"counterparty": "Halcyon Audio"},
        refs=("https://www.threadledger.example/v9-halcyon",),
    ),
    _ev(
        16,
        "voltaire-nine",
        "celebrity_cosign",
        {"celebrity": "Juno Vasquez", "tier": "a_list"},
        era="voltaire-nine:okafor",
        source="social",
        refs=("social:juno-vasquez-v9",),
    ),
    _ev(
        28,
        "voltaire-nine",
        "collab_announcement",
        {"counterparty": "Perrin Ash Studio"},
        refs=("https://www.threadledger.example/v9-perrin",),
        fizzled=True,
    ),
    _ev(
        36,
        "voltaire-nine",
        "designer_departure",
        {"reason": "resignation"},
        era="voltaire-nine:okafor",
        refs=("https://www.modewire.example/v9-okafor",),
    ),
    _ev(
        48,
        "voltaire-nine",
        "celebrity_cosign",
        {"celebrity": "Mika Toll", "tier": "b_list"},
        era="voltaire-nine:okafor",
        source="social",
        refs=("social:mika-toll-v9",),
        fizzled=True,
    ),
    _ev(
        56,
        "voltaire-nine",
        "collab_announcement",
        {"counterparty": "Nordkant", "counterparty_brand_id": "nordkant"},
        refs=("https://www.threadledger.example/v9-nordkant",),
    ),
    _ev(
        64,
        "voltaire-nine",
        "runway_reception",
        {"polarity": "panned"},
        refs=("https://www.modewire.example/v9-ss25",),
    ),
    _ev(
        58,
        "voltaire-nine",
        "designer_departure",
        {"reason": "resignation"},
        era="voltaire-nine:mireille",
        refs=("https://www.modewire.example/v9-mireille",),
    ),
    _ev(
        80,
        "voltaire-nine",
        "designer_appointment",
        {"designer": "Tomas Sable", "acclaim": "neutral"},
        refs=("https://www.modewire.example/v9-sable",),
    ),
    _ev(
        82,
        "voltaire-nine",
        "collab_announcement",
        {"counterparty": "Cove & Iron"},
        refs=("https://www.threadledger.example/v9-cove",),
    ),
    # -- serrano-bloc ----------------------------------------------------------
    _ev(
        12,
        "serrano-bloc",
        "brand_scandal",
        {"severity": "minor"},
        refs=("https://www.threadledger.example/serrano-ads",),
    ),
    _ev(
        20,
        "serrano-bloc",
        "designer_departure",
        {"reason": "ousted"},
        era="serrano-bloc:serrano",
        source="news",
        refs=("https://www.modewire.example/serrano-exit", "social:serrano-exit-thread"),
    ),
    _ev(
        30,
        "serrano-bloc",
        "brand_scandal",
        {"severity": "severe"},
        refs=("https://www.threadledger.example/serrano-campaign",),
        notes="Campaign pulled after sustained public criticism.",
    ),
    _ev(
        34,
        "serrano-bloc",
        "celebrity_cosign",
        {"celebrity": "Mika Toll", "tier": "b_list"},
        era="serrano-bloc:bloc",
        source="social",
        refs=("social:mika-toll-bloc",),
        fizzled=True,
    ),
    _ev(
        46,
        "serrano-bloc",
        "designer_departure",
        {"reason": "house_closure"},
        era="serrano-bloc:bloc",
        refs=("https://www.modewire.example/bloc-line-closed",),
    ),
    _ev(
        54,
        "serrano-bloc",
        "brand_scandal",
        {"severity": "moderate"},
        refs=("https://www.threadledger.example/serrano-labour",),
    ),
    _ev(
        68,
        "serrano-bloc",
        "designer_appointment",
        {"designer": "Kai Iversen", "acclaim": "acclaimed"},
        refs=("https://www.modewire.example/serrano-iversen",),
    ),
    _ev(
        88,
        "serrano-bloc",
        "celebrity_cosign",
        {"celebrity": "Perrin Ash", "tier": "niche"},
        source="social",
        refs=("social:perrin-ash-serrano",),
    ),
    # -- hausmann-studio -------------------------------------------------------
    _ev(
        24,
        "hausmann-studio",
        "celebrity_cosign",
        {"celebrity": "Rae Okonjo", "tier": "b_list"},
        source="social",
        refs=("social:rae-okonjo-hausmann",),
    ),
    _ev(
        32,
        "hausmann-studio",
        "collab_announcement",
        {"counterparty": "Ferrand Leather"},
        refs=("https://www.threadledger.example/hausmann-ferrand",),
    ),
    _ev(
        52,
        "hausmann-studio",
        "designer_departure",
        {"reason": "death"},
        era="hausmann-studio:hausmann",
        source="news",
        refs=("https://www.modewire.example/hausmann-obituary", "manual:hausmann-death"),
    ),
    _ev(
        53,
        "hausmann-studio",
        "collab_announcement",
        {"counterparty": "Aldwin Sports"},
        refs=("https://www.threadledger.example/hausmann-aldwin",),
    ),
    _ev(
        54,
        "hausmann-studio",
        "celebrity_cosign",
        {"celebrity": "Juno Vasquez", "tier": "a_list"},
        era="hausmann-studio:hausmann",
        source="social",
        refs=("social:juno-vasquez-hausmann",),
    ),
    _ev(
        66,
        "hausmann-studio",
        "brand_scandal",
        {"severity": "severe"},
        refs=("https://www.threadledger.example/hausmann-resale",),
    ),
    _ev(
        76,
        "hausmann-studio",
        "designer_appointment",
        {"designer": "Lena Petrov", "acclaim": "acclaimed"},
        refs=("https://www.modewire.example/hausmann-petrov",),
    ),
    _ev(
        84,
        "hausmann-studio",
        "runway_reception",
        {"polarity": "acclaimed"},
        refs=("https://www.modewire.example/hausmann-aw25",),
    ),
    _ev(
        90,
        "hausmann-studio",
        "designer_departure",
        {"reason": "ousted"},
        era="hausmann-studio:petrov",
        refs=("https://www.modewire.example/hausmann-petrov-out",),
    ),
)

# Scenario B (mismatch): 5 brands / 10 eras / 20 dense leaf strata, 80 weeks,
# 16 events, of which 3 move *opposite* to their prior.
SCENARIO_B_BRANDS: tuple[BrandSpec, ...] = (
    BrandSpec(
        "brack-holt",
        "Brack Holt",
        ("Brack",),
        "Archive outerwear house.",
        1.4,
        (
            EraSpec(
                "brack",
                "Ines Brack",
                "Ines Brack era",
                "1997-01",
                "2022-03",
                (_dense("outerwear", 9.9), _dense("knitwear", 8.8)),
            ),
            EraSpec(
                "holt",
                "Sam Holt",
                "Sam Holt era",
                "2022-04",
                None,
                (_dense("tops", 10.45), _dense("denim", 8.8)),
            ),
        ),
    ),
    BrandSpec(
        "delacroix-cie",
        "Delacroix & Cie",
        ("Delacroix",),
        "Tailoring house.",
        1.2,
        (
            EraSpec(
                "delacroix",
                "Remi Delacroix",
                "Remi Delacroix era",
                "1990-01",
                "2021-12",
                (_dense("tailoring", 9.35), _dense("outerwear", 8.25)),
            ),
            EraSpec(
                "noor",
                "Yara Noor",
                "Yara Noor era",
                "2022-01",
                None,
                (_dense("tops", 11.0), _dense("accessories", 8.8)),
            ),
        ),
    ),
    BrandSpec(
        "quarry-lane",
        "Quarry Lane",
        ("Quarry",),
        "Streetwear label, collab-driven.",
        0.95,
        (
            EraSpec(
                "quarry",
                "Obi Quarry",
                "Obi Quarry era",
                "2012-01",
                "2023-06",
                (_dense("tops", 12.0), _dense("footwear", 8.8)),
            ),
            EraSpec(
                "lane",
                "Tess Lane",
                "Tess Lane era",
                "2023-07",
                None,
                (_dense("bottoms", 9.9), _dense("denim", 9.35)),
            ),
        ),
    ),
    BrandSpec(
        "meridian-forge",
        "Meridian Forge",
        ("Meridian",),
        "Heritage knitwear.",
        0.85,
        (
            EraSpec(
                "meridian",
                "Kata Meridian",
                "Kata Meridian era",
                "2001-01",
                "2023-02",
                (_dense("knitwear", 9.9), _dense("tailoring", 8.25)),
            ),
            EraSpec(
                "forge",
                "Ivo Forge",
                "Ivo Forge era",
                "2023-03",
                None,
                (_dense("outerwear", 8.8), _dense("tops", 9.9)),
            ),
        ),
    ),
    BrandSpec(
        "solveig-mark",
        "Solveig Mark",
        ("Solveig",),
        "Quiet brand, few events.",
        1.05,
        (
            EraSpec(
                "solveig",
                "Ann Solveig",
                "Ann Solveig era",
                "2006-01",
                "2022-09",
                (_dense("accessories", 8.8), _dense("outerwear", 8.25)),
            ),
            EraSpec(
                "mark",
                "Petra Mark",
                "Petra Mark era",
                "2022-10",
                None,
                (_dense("knitwear", 9.35), _dense("bottoms", 8.8)),
            ),
        ),
    ),
)

SCENARIO_B_EVENTS: tuple[EventSpec, ...] = (
    _ev(
        8,
        "brack-holt",
        "designer_departure",
        {"reason": "resignation"},
        era="brack-holt:brack",
        source="manual",
        refs=("manual:brack-exit",),
    ),
    _ev(
        14,
        "quarry-lane",
        "collab_announcement",
        {"counterparty": "Halcyon Audio"},
        refs=("https://www.threadledger.example/quarry-halcyon",),
        contradicts_prior=True,
        notes="The drop was resold below retail within a fortnight.",
    ),
    _ev(
        18,
        "delacroix-cie",
        "brand_scandal",
        {"severity": "moderate"},
        refs=("https://www.threadledger.example/delacroix-labour",),
    ),
    _ev(
        20,
        "meridian-forge",
        "celebrity_cosign",
        {"celebrity": "Juno Vasquez", "tier": "a_list"},
        era="meridian-forge:meridian",
        source="social",
        refs=("social:juno-meridian",),
    ),
    _ev(
        24,
        "brack-holt",
        "runway_reception",
        {"polarity": "panned"},
        refs=("https://www.modewire.example/brack-ss24",),
        contradicts_prior=True,
    ),
    _ev(
        28,
        "quarry-lane",
        "designer_departure",
        {"reason": "ousted"},
        era="quarry-lane:quarry",
        refs=("https://www.modewire.example/quarry-obi",),
    ),
    _ev(
        32,
        "delacroix-cie",
        "designer_appointment",
        {"designer": "Yara Noor", "acclaim": "acclaimed"},
        refs=("https://www.modewire.example/delacroix-noor",),
        contradicts_prior=True,
    ),
    _ev(
        36,
        "meridian-forge",
        "designer_departure",
        {"reason": "death"},
        era="meridian-forge:meridian",
        source="news",
        refs=("https://www.modewire.example/meridian-obituary", "manual:meridian-death"),
    ),
    _ev(
        38,
        "meridian-forge",
        "collab_announcement",
        {"counterparty": "Cove & Iron"},
        refs=("https://www.threadledger.example/meridian-cove",),
    ),
    _ev(
        40,
        "brack-holt",
        "brand_scandal",
        {"severity": "severe"},
        refs=("https://www.threadledger.example/brack-campaign",),
    ),
    _ev(
        44,
        "solveig-mark",
        "designer_departure",
        {"reason": "resignation"},
        era="solveig-mark:solveig",
        refs=("https://www.modewire.example/solveig-exit",),
    ),
    _ev(
        48,
        "quarry-lane",
        "celebrity_cosign",
        {"celebrity": "Mika Toll", "tier": "b_list"},
        era="quarry-lane:quarry",
        source="social",
        refs=("social:mika-quarry",),
    ),
    _ev(
        52,
        "delacroix-cie",
        "designer_departure",
        {"reason": "house_closure"},
        era="delacroix-cie:delacroix",
        refs=("https://www.modewire.example/delacroix-closed",),
    ),
    _ev(
        56,
        "meridian-forge",
        "brand_scandal",
        {"severity": "minor"},
        refs=("https://www.threadledger.example/meridian-supplier",),
    ),
    _ev(
        60,
        "quarry-lane",
        "designer_appointment",
        {"designer": "Tess Lane", "acclaim": "neutral"},
        refs=("https://www.modewire.example/quarry-lane",),
    ),
    _ev(
        64,
        "brack-holt",
        "celebrity_cosign",
        {"celebrity": "Perrin Ash", "tier": "niche"},
        source="social",
        refs=("social:perrin-brack",),
    ),
)


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    weeks: int
    start: str
    brands: tuple[BrandSpec, ...]
    events: tuple[EventSpec, ...]
    mismatch: bool
    replay_start_week: int = 12

    @property
    def leaves(self) -> tuple[tuple[str, str, str, LeafSpec], ...]:
        """``(brand_id, era_suffix, era_id, leaf)`` for every leaf stratum."""
        out = []
        for brand in self.brands:
            for era in brand.eras:
                for leaf in era.leaves:
                    out.append((brand.id, era.suffix, f"{brand.id}:{era.suffix}", leaf))
        return tuple(out)


SCENARIOS: dict[str, Scenario] = {
    "scenario_a": Scenario(
        name="scenario_a",
        seed=20260731,
        weeks=120,
        start="2023-01-02",
        brands=SCENARIO_A_BRANDS,
        events=SCENARIO_A_EVENTS,
        mismatch=False,
    ),
    "scenario_b": Scenario(
        name="scenario_b",
        seed=20260801,
        weeks=80,
        start="2023-01-02",
        brands=SCENARIO_B_BRANDS,
        events=SCENARIO_B_EVENTS,
        mismatch=True,
    ),
}


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def week_dates(scenario: Scenario) -> list[str]:
    first = date.fromisoformat(scenario.start)
    return [(first + timedelta(weeks=i)).isoformat() for i in range(scenario.weeks)]


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm — stdlib only, so the draw is stable across environments."""
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def student_t(rng: random.Random, df: int) -> float:
    z = rng.gauss(0.0, 1.0)
    chi2 = rng.gammavariate(df / 2.0, 2.0)
    return z / math.sqrt(chi2 / df)


def leaf_path(brand_id: str, era_suffix: str, category: str) -> str:
    return f"{brand_id}/{era_suffix}/{category}"


def _pick_condition(rng: random.Random) -> str:
    draw = rng.random()
    total = 0.0
    for grade, share in CONDITION_MIX:
        total += share
        if draw < total:
            return grade
    return CONDITION_MIX[-1][0]


def _prior_key(spec: EventSpec) -> str:
    if spec.kind == "designer_departure":
        return f"designer_departure.{spec.attrs['reason']}"
    if spec.kind == "designer_appointment":
        return f"designer_appointment.{spec.attrs['acclaim']}"
    if spec.kind == "runway_reception":
        return f"runway_reception.{spec.attrs['polarity']}"
    if spec.kind == "brand_scandal":
        return f"brand_scandal.{spec.attrs['severity']}"
    return spec.kind


def _predecessor_era(scenario: Scenario, spec: EventSpec) -> str | None:
    """The era a designer appointment closes — resolved on the construction side."""
    brand = next(b for b in scenario.brands if b.id == spec.brand)
    designer = spec.attrs.get("designer", "")
    ordered = sorted(brand.eras, key=lambda e: e.start)
    for position, era in enumerate(ordered):
        if era.designer == designer:
            return ordered[position - 1].suffix if position > 0 else None
    month = week_dates(scenario)[spec.week][:7]
    candidates = [e for e in ordered if e.start <= month and e.designer != designer]
    return candidates[-1].suffix if candidates else None


@dataclass
class PlantedImpact:
    """One planted leg: a target stratum plus the true path applied to it."""

    event_key: str
    event_id: str
    kind: str
    target: str
    permanent: float
    transient: float
    half_life: float
    diffusion_weeks: int
    week: int
    fizzled: bool
    contradicts_prior: bool

    def factor(self, week: int) -> float:
        """The multiplicative impact on the latent level at ``week`` (1.0 before it)."""
        age = week - self.week
        if age < 0:
            return 1.0
        ramp = min(1.0, age / self.diffusion_weeks) if self.diffusion_weeks > 0 else 1.0
        return 1.0 + ramp * (self.permanent + self.transient * 0.5 ** (age / self.half_life))


def planted_impacts(scenario: Scenario) -> list[PlantedImpact]:
    """Draw the true impact path of every event, seeded, around the case anchors."""
    rng = random.Random(f"{scenario.seed}:impacts")
    out: list[PlantedImpact] = []
    for spec in scenario.events:
        key = _prior_key(spec)
        targets: list[tuple[str, str]] = []
        if spec.kind == "designer_departure":
            assert spec.era is not None
            targets.append((f"{spec.brand}/{spec.era.split(':', 1)[1]}", key))
        elif spec.kind == "designer_appointment":
            targets.append((spec.brand, key))
            predecessor = _predecessor_era(scenario, spec)
            if predecessor is not None:
                targets.append((f"{spec.brand}/{predecessor}", "designer_appointment.predecessor"))
        elif spec.kind == "celebrity_cosign":
            if spec.era is not None:
                targets.append((f"{spec.brand}/{spec.era.split(':', 1)[1]}", key))
            else:
                targets.append((spec.brand, key))
        elif spec.kind == "collab_announcement":
            targets.append((spec.brand, key))
            counterparty = spec.attrs.get("counterparty_brand_id")
            if counterparty:
                targets.append((counterparty, key))
        else:
            targets.append((spec.brand, key))

        for target, anchor_key in targets:
            permanent, transient, half_life, diffusion = IMPACT_ANCHORS[anchor_key]
            if spec.kind == "celebrity_cosign":
                transient *= TIER_SCALE[spec.attrs["tier"]]
            jitter_p = math.exp(rng.gauss(0.0, ANCHOR_SIGMA))
            jitter_t = math.exp(rng.gauss(0.0, ANCHOR_SIGMA))
            jitter_h = math.exp(rng.gauss(0.0, ANCHOR_SIGMA / 2))
            permanent *= jitter_p
            transient *= jitter_t
            half_life = max(1.0, min(26.0, half_life * jitter_h))
            if spec.fizzled:
                permanent *= FIZZLE_SCALE
                transient *= FIZZLE_SCALE
            if spec.contradicts_prior:
                permanent, transient = -permanent, -transient
            out.append(
                PlantedImpact(
                    event_key=f"{spec.brand}|{spec.kind}|{spec.week}",
                    event_id=event_id_for(scenario, spec),
                    kind=spec.kind,
                    target=target,
                    permanent=round(permanent, 6),
                    transient=round(transient, 6),
                    half_life=round(half_life, 4),
                    diffusion_weeks=diffusion,
                    week=spec.week,
                    fizzled=spec.fizzled,
                    contradicts_prior=spec.contradicts_prior,
                )
            )
    return out


def event_id_for(scenario: Scenario, spec: EventSpec) -> str:
    """The content-derived FR-5 id of a specified event (identity, not ground truth)."""
    occurred = week_dates(scenario)[spec.week]
    return event_id(
        spec.kind,
        spec.brand,
        spec.era,
        occurred,
        identity_attrs_for(_EventType(spec.kind), spec.attrs),
    )


def _applies(target: str, leaf: str) -> bool:
    a, b = target.split("/"), leaf.split("/")
    return len(a) <= len(b) and b[: len(a)] == a


# --------------------------------------------------------------------------- #
# Generation                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class GeneratedScenario:
    scenario: Scenario
    weeks: list[str]
    multipliers: dict[str, float]
    latent: dict[str, list[float]]
    parent_truth: dict[str, list[float]]
    clean_counts: dict[str, list[int]]
    listings: list[dict[str, Any]]
    listing_classes: dict[str, list[str]]
    impacts: list[PlantedImpact]
    portfolio: list[dict[str, Any]]
    titles: dict[str, str] = field(default_factory=dict)


def generate(scenario: Scenario) -> GeneratedScenario:
    weeks = week_dates(scenario)
    n_weeks = len(weeks)
    table = load_conditions()
    multipliers = {row.grade.value: row.multiplier for row in table.grades}
    aliases = {row.grade.value: list(row.platform_aliases) for row in table.grades}

    world_multipliers = dict(multipliers)
    if scenario.mismatch:
        # The world's condition discounts differ from the committed ones by +/-12 %
        # (EVALS M6): the pipeline must survive reading the wrong quality curve.
        rng = random.Random(f"{scenario.seed}:multipliers")
        for grade in world_multipliers:
            world_multipliers[grade] *= 1.0 + rng.uniform(-0.12, 0.12)

    impacts = planted_impacts(scenario)

    # -- latent truth per leaf ---------------------------------------------- #
    latent: dict[str, list[float]] = {}
    lam_of: dict[str, float] = {}
    density_of: dict[str, str] = {}
    for brand_id, era_suffix, _era_id, leaf in scenario.leaves:
        path = leaf_path(brand_id, era_suffix, leaf.category)
        brand = next(b for b in scenario.brands if b.id == brand_id)
        rng = random.Random(f"{scenario.seed}:gbm:{path}")
        anchor = CATEGORY_ANCHOR[leaf.category] * brand.price_factor
        legs = [imp for imp in impacts if _applies(imp.target, path)]
        series: list[float] = []
        cumulative = 0.0
        for index in range(n_weeks):
            if index:
                cumulative += rng.gauss(0.0, GBM_SIGMA)
            level = anchor * math.exp(cumulative)
            for leg in legs:
                level *= leg.factor(index)
            series.append(level)
        latent[path] = series
        lam_of[path] = leaf.lam
        density_of[path] = leaf.density

    # -- listings ------------------------------------------------------------ #
    listings: list[dict[str, Any]] = []
    listing_classes: dict[str, list[str]] = {"fake": [], "mislabel": [], "ambiguous": []}
    clean_counts: dict[str, list[int]] = {}
    weekly_clean: dict[str, list[int]] = {}
    titles: dict[str, str] = {}
    item_sigma = ITEM_SIGMA_B if scenario.mismatch else ITEM_SIGMA_A
    t_scale = item_sigma / math.sqrt(T_DF / (T_DF - 2.0))

    for brand_id, era_suffix, _era_id, leaf in scenario.leaves:
        path = leaf_path(brand_id, era_suffix, leaf.category)
        rng = random.Random(f"{scenario.seed}:sales:{path}")
        per_week: list[int] = []
        counter = 0
        for index, week in enumerate(weeks):
            level = latent[path][index]
            n_sold = poisson(rng, leaf.lam)
            clean_here = 0
            for _ in range(n_sold):
                counter += 1
                grade = _pick_condition(rng)
                noise = (
                    math.exp(t_scale * student_t(rng, T_DF))
                    if scenario.mismatch
                    else math.exp(rng.gauss(0.0, item_sigma))
                )
                price = level * world_multipliers[grade] * noise
                draw = rng.random()
                external_id = f"{scenario.name[-1]}-{path.replace('/', '.')}-{counter:05d}"
                if draw < FAKE_RATE:
                    price *= rng.uniform(*FAKE_BAND)
                    listing_classes["fake"].append(external_id)
                elif draw < FAKE_RATE + MISLABEL_RATE:
                    price *= rng.uniform(*MISLABEL_BAND)
                    listing_classes["mislabel"].append(external_id)
                elif draw < FAKE_RATE + MISLABEL_RATE + AMBIGUOUS_RATE:
                    price *= rng.uniform(*AMBIGUOUS_BAND)
                    listing_classes["ambiguous"].append(external_id)
                else:
                    clean_here += 1
                listed = (date.fromisoformat(week) - timedelta(days=rng.randint(7, 60))).isoformat()
                row: dict[str, Any] = {
                    "external_id": external_id,
                    "brand_ref": brand_id,
                    "era_ref": era_suffix,
                    "category": leaf.category,
                    "platform_condition": rng.choice(aliases[grade]),
                    "status": "sold",
                    "listed_at": listed,
                    "sold_at": week,
                    "sold_price": round(price, 2),
                }
                if counter % 20 == 0:
                    title = _title(rng, brand_id, era_suffix, leaf.category)
                    row["title"] = title
                    titles[external_id] = title
                listings.append(row)
            per_week.append(clean_here)

            for _ in range(poisson(rng, ASK_LAMBDA)):
                counter += 1
                grade = _pick_condition(rng)
                ask = level * world_multipliers[grade] * rng.uniform(*ASK_BAND)
                listed = (date.fromisoformat(week) - timedelta(days=rng.randint(1, 45))).isoformat()
                listings.append(
                    {
                        "external_id": f"{scenario.name[-1]}-{path.replace('/', '.')}-{counter:05d}",
                        "brand_ref": brand_id,
                        "era_ref": era_suffix,
                        "category": leaf.category,
                        "platform_condition": rng.choice(aliases[grade]),
                        "status": "active",
                        "listed_at": listed,
                        "ask_price": round(ask, 2),
                    }
                )
        weekly_clean[path] = per_week

    for path, per_week in weekly_clean.items():
        window: list[int] = []
        for index in range(len(weeks)):
            low = max(0, index - WINDOW_WEEKS + 1)
            window.append(sum(per_week[low : index + 1]))
        clean_counts[path] = window

    listings.sort(key=lambda row: (row.get("sold_at") or row["listed_at"], row["external_id"]))

    # -- chain-linked parent truth ------------------------------------------ #
    parent_truth = _chain_link_truth(scenario, latent, weekly_clean)

    # -- eval portfolio ------------------------------------------------------ #
    portfolio = _portfolio(scenario, latent, weeks)

    return GeneratedScenario(
        scenario=scenario,
        weeks=weeks,
        multipliers=world_multipliers,
        latent=latent,
        parent_truth=parent_truth,
        clean_counts=clean_counts,
        listings=listings,
        listing_classes=listing_classes,
        impacts=impacts,
        portfolio=portfolio,
        titles=titles,
    )


_TITLE_WORDS = ("archive", "runway sample", "deadstock", "rare", "seasonal", "workshop")


def _title(rng: random.Random, brand_id: str, era_suffix: str, category: str) -> str:
    return f"{brand_id.replace('-', ' ')} {era_suffix} {rng.choice(_TITLE_WORDS)} {category}"


def _chain_link_truth(
    scenario: Scenario,
    latent: dict[str, list[float]],
    weekly_clean: dict[str, list[int]],
) -> dict[str, list[float]]:
    """Chain-link the *truth* leaf levels with the *true* sale counts (EVALS M1a).

    Entirely construction-side: the FR-4 parent implementation is graded against
    this rather than trusted. Weights are the trailing
    ``parent_weight_window_weeks`` true sale counts, exactly the quantity FR-4
    names — but computed from the generator's own counts, never from the engine.
    """
    n_weeks = len(next(iter(latent.values())))
    weights: dict[str, list[float]] = {}
    for path, per_week in weekly_clean.items():
        rolling: list[float] = []
        for index in range(n_weeks):
            low = max(0, index - PARENT_WEIGHT_WINDOW_WEEKS + 1)
            rolling.append(float(sum(per_week[low : index + 1])))
        weights[path] = rolling

    era_children: dict[str, list[str]] = {}
    for path in latent:
        era_children.setdefault(path.rsplit("/", 1)[0], []).append(path)

    out: dict[str, list[float]] = {}
    for era, children in era_children.items():
        out[era] = _chain(children, latent, weights, n_weeks)

    brand_children: dict[str, list[str]] = {}
    for era in era_children:
        brand_children.setdefault(era.split("/", 1)[0], []).append(era)
    for brand, eras in brand_children.items():
        era_weights = {
            era: [
                sum(weights[leaf][index] for leaf in era_children[era]) for index in range(n_weeks)
            ]
            for era in eras
        }
        out[brand] = _chain(eras, out, era_weights, n_weeks)
    return out


def _chain(
    children: Sequence[str],
    series: dict[str, list[float]],
    weights: dict[str, list[float]],
    n_weeks: int,
) -> list[float]:
    values = [100.0]
    for index in range(1, n_weeks):
        total = sum(weights[child][index] for child in children)
        if total <= 0:
            weights_now = {child: 1.0 for child in children}
            total = float(len(children))
        else:
            weights_now = {child: weights[child][index] for child in children}
        delta = 0.0
        for child in children:
            step = math.log(series[child][index]) - math.log(series[child][index - 1])
            delta += weights_now[child] * step
        values.append(values[-1] * math.exp(delta / total))
    return values


#: Three of the eval portfolio's labels carry forbidden-lexicon words in quoted
#: context, so the FR-9 quoting path is exercised on every replay week (EVALS M5).
_LOADED_LABELS = (
    "guaranteed authentic AW99 moto",
    "sure thing grail — seller's words",
    "easy money flip, allegedly",
)


def _portfolio(
    scenario: Scenario, latent: dict[str, list[float]], weeks: list[str]
) -> list[dict[str, Any]]:
    """One garment per leaf stratum, acquired in week 8 at the then-truth level."""
    added_at = f"{weeks[0]}T00:00:00Z"
    anchor_index = 8
    rows: list[dict[str, Any]] = []
    for position, (brand_id, era_suffix, era_id, leaf) in enumerate(scenario.leaves):
        path = leaf_path(brand_id, era_suffix, leaf.category)
        price = round(latent[path][anchor_index], 2)
        if position < len(_LOADED_LABELS):
            label = _LOADED_LABELS[position]
        else:
            label = f"{brand_id.replace('-', ' ').title()} {era_suffix} {leaf.category}"
        rows.append(
            {
                "id": garment_id(path, weeks[anchor_index], price, added_at),
                "label": label,
                "brand_id": brand_id,
                "era_id": era_id,
                "category": leaf.category,
                "condition": "excellent",
                "anchor_condition": "excellent",
                "status": "owned",
                "acquisition_price": price,
                "acquired_on": weeks[anchor_index],
                "added_at": added_at,
                "notes": "",
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Writing                                                                       #
# --------------------------------------------------------------------------- #


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def _dump_jsonl(path: Path, rows: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def write_scenario(generated: GeneratedScenario, out_dir: Path) -> None:
    scenario = generated.scenario
    out_dir.mkdir(parents=True, exist_ok=True)

    _dump(
        out_dir / "brands_fixture.json",
        {
            "brands": [
                {
                    "id": brand.id,
                    "name": brand.name,
                    "aliases": list(brand.aliases),
                    "notes": brand.notes,
                    "eras": [
                        {
                            "id": f"{brand.id}:{era.suffix}",
                            "designer": era.designer,
                            "label": era.label,
                            "start": era.start,
                            "end": era.end,
                            "notes": era.notes,
                        }
                        for era in brand.eras
                    ],
                }
                for brand in scenario.brands
            ]
        },
    )

    _dump_jsonl(out_dir / "listings.jsonl", generated.listings)

    _dump(
        out_dir / "listings_truth.json",
        {
            "construction": {
                "item_noise": "student_t4" if scenario.mismatch else "lognormal",
                "item_sigma": ITEM_SIGMA_B if scenario.mismatch else ITEM_SIGMA_A,
                "condition_mix": dict(CONDITION_MIX),
                "world_condition_multipliers": generated.multipliers,
                "fake_rate": FAKE_RATE,
                "fake_band": list(FAKE_BAND),
                "mislabel_rate": MISLABEL_RATE,
                "mislabel_band": list(MISLABEL_BAND),
                "ambiguous_rate": AMBIGUOUS_RATE,
                "ambiguous_band": list(AMBIGUOUS_BAND),
            },
            "n_sold": sum(1 for row in generated.listings if row["status"] == "sold"),
            "n_active": sum(1 for row in generated.listings if row["status"] == "active"),
            "classes": {
                name: sorted(ids) for name, ids in sorted(generated.listing_classes.items())
            },
        },
    )

    _dump(
        out_dir / "index_truth.json",
        {
            "weeks": generated.weeks,
            "leaf_level_usd": {
                path: [round(value, 4) for value in series]
                for path, series in sorted(generated.latent.items())
            },
            "parent_index": {
                path: [round(value, 6) for value in series]
                for path, series in sorted(generated.parent_truth.items())
            },
        },
    )

    density = {
        leaf_path(brand_id, era_suffix, leaf.category): {
            "density": leaf.density,
            "weekly_lambda": leaf.lam,
        }
        for brand_id, era_suffix, _era_id, leaf in scenario.leaves
    }
    _dump(
        out_dir / "coverage_truth.json",
        {
            "window_weeks": WINDOW_WEEKS,
            "min_sales": MIN_SALES,
            "weeks": generated.weeks,
            "strata": density,
            "clean_window_sales": {
                path: counts for path, counts in sorted(generated.clean_counts.items())
            },
            "truth_eligible": {
                path: [count >= MIN_SALES for count in counts]
                for path, counts in sorted(generated.clean_counts.items())
            },
            "n_truth_eligible_cells": sum(
                sum(1 for count in counts if count >= MIN_SALES)
                for counts in generated.clean_counts.values()
            ),
        },
    )

    _dump_jsonl(out_dir / "events.jsonl", list(_event_rows(generated)))

    _dump(
        out_dir / "impact_truth.json",
        {
            "anchors": {key: list(value) for key, value in IMPACT_ANCHORS.items()},
            "anchor_sigma": ANCHOR_SIGMA,
            "legs": [
                {
                    "event_id": leg.event_id,
                    "event_key": leg.event_key,
                    "event_type": leg.kind,
                    "event_week": generated.weeks[leg.week],
                    "target_stratum": leg.target,
                    "permanent": leg.permanent,
                    "transient": leg.transient,
                    "half_life_weeks": leg.half_life,
                    "diffusion_weeks": leg.diffusion_weeks,
                    "fizzled": leg.fizzled,
                    "contradicts_prior": leg.contradicts_prior,
                }
                for leg in generated.impacts
            ],
        },
    )

    _dump(out_dir / "eval_portfolio.json", {"garments": generated.portfolio})


def _event_rows(generated: GeneratedScenario) -> Iterator[dict[str, Any]]:
    """One JSONL row per *sighting*: four events are reported twice (FR-5 T5)."""
    weeks = generated.weeks
    for spec in sorted(generated.scenario.events, key=lambda s: (s.week, s.brand, s.kind)):
        refs = spec.refs or (f"{spec.source}:{spec.brand}-{spec.week}",)
        for position, ref in enumerate(refs):
            source = spec.source
            if position:
                source = (
                    "manual"
                    if ref.startswith("manual:")
                    else ("social" if ref.startswith("social:") else "news")
                )
            row: dict[str, Any] = {
                "event_type": spec.kind,
                "brand_id": spec.brand,
                "occurred_on": weeks[spec.week],
                "source": source,
                "source_refs": [ref],
                "status": "confirmed",
                "attributes": spec.attrs,
            }
            if spec.era is not None:
                row["era_id"] = spec.era
            if spec.notes and position == 0:
                row["notes"] = spec.notes
            yield row


def _check_scenario(scenario: Scenario) -> None:
    """Assert the composition EVALS.md specifies, so drift fails loudly."""
    leaves = scenario.leaves
    dense = sum(1 for *_, leaf in leaves if leaf.density == "dense")
    sparse = len(leaves) - dense
    if scenario.name == "scenario_a":
        assert len(scenario.brands) == 8, len(scenario.brands)
        assert sum(len(b.eras) for b in scenario.brands) == 18
        assert (len(leaves), dense, sparse) == (36, 30, 6), (len(leaves), dense, sparse)
        assert len(scenario.events) == 44, len(scenario.events)
        kinds = [spec.kind for spec in scenario.events]
        assert kinds.count("designer_departure") == 10
        assert kinds.count("designer_appointment") == 6
        assert kinds.count("collab_announcement") == 8
        assert kinds.count("celebrity_cosign") == 10
        assert kinds.count("runway_reception") == 4
        assert kinds.count("brand_scandal") == 6
        reasons = [s.attrs["reason"] for s in scenario.events if s.kind == "designer_departure"]
        assert (reasons.count("resignation"), reasons.count("ousted")) == (4, 3), reasons
        assert (reasons.count("death"), reasons.count("house_closure")) == (2, 1), reasons
        tiers = [s.attrs["tier"] for s in scenario.events if s.kind == "celebrity_cosign"]
        assert (tiers.count("a_list"), tiers.count("b_list"), tiers.count("niche")) == (3, 4, 3)
        assert sum(1 for s in scenario.events if s.fizzled and s.kind == "collab_announcement") == 2
        assert sum(1 for s in scenario.events if s.fizzled and s.kind == "celebrity_cosign") == 3
        assert sum(1 for s in scenario.events if len(s.refs) > 1) == 4
        assert max(spec.week for spec in scenario.events) <= 90
        branded = {spec.brand for spec in scenario.events}
        assert len(set(b.id for b in scenario.brands) - branded) == 2
        # every sparse leaf sits under an era that receives at least two events
        for brand_id, _era_suffix, era_id, leaf in leaves:
            if leaf.density != "sparse":
                continue
            touching = [
                spec
                for spec in scenario.events
                if spec.brand == brand_id and (spec.era in (None, era_id))
            ]
            assert len(touching) >= 2, (era_id, len(touching))
    else:
        assert len(scenario.brands) == 5
        assert (len(leaves), dense, sparse) == (20, 20, 0)
        assert len(scenario.events) == 16
        assert sum(1 for s in scenario.events if s.contradicts_prior) == 3


def build(name: str, out_root: Path = FIXTURES) -> GeneratedScenario:
    scenario = SCENARIOS[name]
    _check_scenario(scenario)
    generated = generate(scenario)
    write_scenario(generated, out_root / name)
    return generated


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=None, help="scenario seed (selects a scenario)")
    parser.add_argument("--mismatch", action="store_true", help="generate the mismatch scenario B")
    parser.add_argument("--out", type=Path, default=FIXTURES, help="fixture root directory")
    parser.add_argument("--all", action="store_true", help="generate both scenarios")
    args = parser.parse_args(argv)

    names: list[str]
    if args.all or args.seed is None:
        names = ["scenario_a", "scenario_b"]
    else:
        names = ["scenario_b" if args.mismatch else "scenario_a"]
        expected = SCENARIOS[names[0]].seed
        if args.seed != expected:
            parser.error(f"{names[0]} is generated with --seed {expected}, got {args.seed}")

    for name in names:
        generated = build(name, args.out)
        sold = sum(1 for row in generated.listings if row["status"] == "sold")
        active = len(generated.listings) - sold
        print(
            f"{name}: {len(generated.scenario.leaves)} leaf strata, "
            f"{len(generated.weeks)} weeks, {sold} sold + {active} ask-only listings, "
            f"{len(generated.scenario.events)} events, {len(generated.portfolio)} garments"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
