"""Build the derived trial fixtures from ``labels.json`` (EVALS.md "Fixture strategy").

Everything here is **mechanical**: speaker roles, sibling relations and channel
variants come from the committed ``labels.json``, and the only free choices are
seeded random draws, so the derived fixtures cannot drift from the corpus. The
outputs are committed; re-running this script must reproduce them byte-for-byte.

Composition (EVALS.md):

* ``trials.json`` — M1. 96 genuine trials (24 eval speakers x 4 probes; 48
  clean, 48 channel-filtered) and 288 impostor trials: for each eval speaker,
  every probe of its own siblings/mimic first, then seeded-random other-speaker
  probes up to 12 non-self probes per speaker. Pooled composition: 24 joint,
  48 single-axis (16 per axis), 12 mimic, 204 random-other.
* ``consent_trials.json`` — M2. 48 genuine consent attempts (24 clean, 12
  harsh, 12 channel-filtered) and 42 impostor attempts (12 joint, 12
  single-axis — 4 per axis, 6 mimic, 12 random-other: each of the first 12 eval
  speakers' clean consent scored against a seeded non-matching enrollment).
* ``coherence_sets.json`` — M6. 24 pure sets (each eval speaker's 3 enrollment
  takes) and 24 mixed sets (2 own enrollment takes + 1 foreign clip): within
  each single-axis group of four (S01-S04 f0, S05-S08 vtl, S09-S12 tilt) the
  first two speakers take their single-axis sibling's first probe (2 per axis =
  6), the last two take their joint sibling's first probe (6), and S13-S24 take
  a seeded unrelated eval speaker's first enrollment take (12).

Usage::

    uv run python voicekin/evals/fixtures/build_derived.py          # verify
    uv run python voicekin/evals/fixtures/build_derived.py --write  # rewrite
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in (None, ""):  # `python voicekin/evals/fixtures/build_derived.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import corpus

FIXTURES_DIR = Path(__file__).resolve().parent

TRIAL_SEED = 20260731
"""Seed of the random-other probe draws (M1)."""

CONSENT_SEED = 20260732
"""One stream drawn in a fixed order: first the 12 M2 random-other consent
targets, then the 12 M6 unrelated foreign picks — so both are reproducible."""

PROBES_PER_SPEAKER = 12
RANDOM_CONSENT_SPEAKERS = 12

#: Within each single-axis group of four, the first two contribute the
#: single-axis mixed sets and the last two the joint mixed sets (module doc).
SINGLE_AXIS_MIXED = ("S01", "S02", "S05", "S06", "S09", "S10")
JOINT_MIXED = ("S03", "S04", "S07", "S08", "S11", "S12")


def eval_bases() -> list[str]:
    return [s["id"] for s in corpus.speakers(split="eval", role="base")]


def eval_impostors() -> list[dict[str, Any]]:
    return corpus.speakers(split="eval", role="impostor")


def impostor_class(speaker: dict[str, Any]) -> str:
    if speaker["relation"] == "single_axis":
        return f"single_{speaker['axis']}"
    return str(speaker["relation"])


def build_trials() -> dict[str, Any]:
    rng = np.random.default_rng(TRIAL_SEED)
    genuine = []
    for sid in eval_bases():
        for role in corpus.roles(sid, "probe"):
            genuine.append(
                {
                    "target": sid,
                    "probe": role,
                    "condition": corpus.utterance(role)["variant"],
                }
            )

    by_base: dict[str, list[dict[str, Any]]] = {}
    for speaker in eval_impostors():
        by_base.setdefault(speaker["base_id"], []).append(speaker)

    impostor = []
    for sid in eval_bases():
        own = [
            {"target": sid, "probe": role, "class": impostor_class(speaker)}
            for speaker in by_base.get(sid, [])
            for role in corpus.roles(speaker["id"], "probe")
        ]
        other_probes = [
            role
            for other in eval_bases()
            if other != sid
            for role in corpus.roles(other, "probe")
        ]
        picks = rng.choice(len(other_probes), size=PROBES_PER_SPEAKER - len(own), replace=False)
        impostor.extend(own)
        impostor.extend(
            {"target": sid, "probe": other_probes[int(i)], "class": "random_other"}
            for i in picks
        )
    return {
        "seed": TRIAL_SEED,
        "source": "labels.json via build_derived.py",
        "genuine": genuine,
        "impostor": impostor,
    }


def build_consent_and_coherence() -> tuple[dict[str, Any], dict[str, Any]]:
    rng = np.random.default_rng(CONSENT_SEED)
    bases = eval_bases()

    genuine = []
    for sid in bases:
        for role in corpus.roles(sid, "consent"):
            genuine.append(
                {
                    "target": sid,
                    "recording": role,
                    "condition": corpus.utterance(role)["variant"],
                }
            )

    impostor = []
    for speaker in eval_impostors():
        role = corpus.roles(speaker["id"], "consent")[0]
        impostor.append(
            {
                "target": speaker["base_id"],
                "recording": role,
                "class": impostor_class(speaker),
            }
        )
    for sid in bases[:RANDOM_CONSENT_SPEAKERS]:
        others = [o for o in bases if o != sid]
        target = others[int(rng.integers(0, len(others)))]
        impostor.append(
            {"target": target, "recording": f"{sid}/consent/0", "class": "random_other"}
        )

    consent_trials = {
        "seed": CONSENT_SEED,
        "source": "labels.json via build_derived.py",
        "genuine": genuine,
        "impostor": impostor,
    }

    pure = [{"speaker": sid, "roles": corpus.roles(sid, "enroll")} for sid in bases]
    impostors = eval_impostors()
    mixed = []
    for sid in bases:
        own = corpus.roles(sid, "enroll")[:2]
        if sid in SINGLE_AXIS_MIXED:
            sibling = next(
                s for s in impostors if s["base_id"] == sid and s["relation"] == "single_axis"
            )
            foreign = corpus.roles(sibling["id"], "probe")[0]
            foreign_class = impostor_class(sibling)
        elif sid in JOINT_MIXED:
            sibling = next(
                s for s in impostors if s["base_id"] == sid and s["relation"] == "joint"
            )
            foreign = corpus.roles(sibling["id"], "probe")[0]
            foreign_class = "joint"
        else:
            others = [o for o in bases if o != sid]
            other = others[int(rng.integers(0, len(others)))]
            foreign = corpus.roles(other, "enroll")[0]
            foreign_class = "unrelated"
        mixed.append(
            {
                "speaker": sid,
                "own_roles": own,
                "foreign_role": foreign,
                "foreign_class": foreign_class,
            }
        )
    coherence_sets = {
        "seed": CONSENT_SEED,
        "source": "labels.json via build_derived.py",
        "pure": pure,
        "mixed": mixed,
    }
    return consent_trials, coherence_sets


def validate(trials: dict[str, Any], consent: dict[str, Any], coherence: dict[str, Any]) -> None:
    """Assert the EVALS.md composition, so a corpus change cannot pass silently."""
    assert len(trials["genuine"]) == 96, len(trials["genuine"])
    conditions = Counter(t["condition"] for t in trials["genuine"])
    assert conditions["clean"] == 48 and sum(conditions.values()) - conditions["clean"] == 48
    assert len(trials["impostor"]) == 288, len(trials["impostor"])
    classes = Counter(t["class"] for t in trials["impostor"])
    assert classes == Counter(
        {"random_other": 204, "joint": 24, "single_f0": 16, "single_vtl": 16,
         "single_tilt": 16, "mimic": 12}
    ), classes

    assert len(consent["genuine"]) == 48
    consent_conditions = Counter(t["condition"] for t in consent["genuine"])
    assert consent_conditions == Counter({"clean": 24, "harsh": 12, "channel": 12}), (
        consent_conditions
    )
    assert len(consent["impostor"]) == 42
    consent_classes = Counter(t["class"] for t in consent["impostor"])
    assert consent_classes == Counter(
        {"joint": 12, "single_f0": 4, "single_vtl": 4, "single_tilt": 4,
         "mimic": 6, "random_other": 12}
    ), consent_classes
    for trial in consent["impostor"]:
        speaker = corpus.utterance(trial["recording"])["speaker_id"]
        assert speaker != trial["target"], trial

    assert len(coherence["pure"]) == 24
    assert all(len(s["roles"]) == 3 for s in coherence["pure"])
    assert len(coherence["mixed"]) == 24
    mixed_classes = Counter(s["foreign_class"] for s in coherence["mixed"])
    assert mixed_classes == Counter(
        {"joint": 6, "single_f0": 2, "single_vtl": 2, "single_tilt": 2, "unrelated": 12}
    ), mixed_classes
    for entry in coherence["mixed"]:
        assert corpus.utterance(entry["foreign_role"])["speaker_id"] != entry["speaker"]


def dump(path: Path, payload: dict[str, Any], write: bool) -> bool:
    text = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    if write:
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
        return True
    if not path.exists():
        print(f"MISSING {path}")
        return False
    if path.read_text(encoding="utf-8") != text:
        print(f"STALE {path} (re-run with --write)")
        return False
    print(f"ok {path.name}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive trial fixtures from labels.json.")
    parser.add_argument("--write", action="store_true", help="rewrite the committed fixtures")
    args = parser.parse_args(argv)

    trials = build_trials()
    consent, coherence = build_consent_and_coherence()
    validate(trials, consent, coherence)

    ok = True
    for name, payload in (
        ("trials.json", trials),
        ("consent_trials.json", consent),
        ("coherence_sets.json", coherence),
    ):
        ok &= dump(FIXTURES_DIR / name, payload, args.write)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
