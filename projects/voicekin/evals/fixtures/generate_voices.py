"""Seeded synthetic voice corpus generator (EVALS.md "Fixture strategy").

Committing real human voices would contradict the product's own consent ethics
(SCOPE decision 14); parameterized source-filter voices give **exact identity
ground truth** and hermetic CI. This script is the ground truth: every label in
``labels.json`` is a generation parameter, never a measurement of the system
under test.

The generator drives the shared synthesis core ``engine/voicebox.py`` in a
*richer* regime than the product stub ever uses — 4-formant stacks with
per-speaker offsets, jitter, shimmer, breathiness, and three channel conditions
— so the embedder is evaluated on audio whose parameter space it was not
co-designed with (SCOPE decision 17).

Two artefacts are committed:

``labels.json``           the population, the sibling/mimic relations, and every
                          per-utterance generation parameter;
``corpus_manifest.json``  relative path -> sha256 of the rendered PCM payload,
                          plus the corpus totals.

The WAVs themselves materialize into the gitignored ``.cache/voices/`` on first
use (REVIEW deviation 1) — ~100 MB of PCM16 does not belong in the monorepo.

Usage::

    uv run python voicekin/evals/fixtures/generate_voices.py            # render + verify
    uv run python voicekin/evals/fixtures/generate_voices.py --write    # re-author labels
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip, encode_wav
from voicekin.engine.dsp import apply_sos, butter_bandpass_sos
from voicekin.engine.voicebox import Unit, VoiceboxParams, synthesize_units

DEFAULT_SEED = 20260731

FIXTURES_DIR = Path(__file__).resolve().parent
LABELS_PATH = FIXTURES_DIR / "labels.json"
MANIFEST_PATH = FIXTURES_DIR / "corpus_manifest.json"
CACHE_DIR = FIXTURES_DIR / ".cache"
VOICES_DIR = CACHE_DIR / "voices"

# --------------------------------------------------------------------------- #
# Population design (EVALS "Synthetic voice corpus")
# --------------------------------------------------------------------------- #

N_EVAL_SPEAKERS = 24
N_DEV_SPEAKERS = 12

#: Adult speaking-F0 norms (Titze, *Principles of Voice Production*).
F0_BANDS = {"male": (85.0, 155.0), "female": (165.0, 255.0)}
#: Vocal-tract-length factor scaling the Peterson & Barney (1952) formant stack.
VTL_BANDS = {"male": (1.00, 1.16), "female": (0.84, 1.00)}
TILT_BAND = (-15.0, -6.0)
F0_SD_BAND = (0.030, 0.060)
"""Per-speaker F0 spread: how widely this voice moves in pitch *within* an
utterance (prosody). A speaker parameter, not a session parameter."""
JITTER_BAND = (0.005, 0.020)
SHIMMER_BAND = (0.02, 0.08)
BREATHINESS_BAND = (0.15, 0.45)
BANDWIDTH_BAND = (0.90, 1.15)
FORMANT_OFFSET_SD_HZ = 22.0

#: Session-to-session F0 offset, as a fraction. Nobody speaks at exactly the same
#: pitch twice; this is *not* an identity difference, which is why it stays an
#: order of magnitude below the smallest impostor margin.
WITHIN_SPEAKER_F0_SD = 0.008

# Feature-space sensitivity of each identity axis, in within-speaker standard
# deviations per unit of parameter change, measured on controlled probe pairs
# (same unit sequence, one parameter varied). Used only to *space the population*
# — never by the metrics.
SIGMA_PER_F0_PERCENT = 0.8
SIGMA_PER_VTL_PERCENT = 1.6
SIGMA_PER_TILT_DB = 4.0
MIN_SPEAKER_SEPARATION_SIGMA = 8.0
"""No two base speakers may be closer than this in the sensitivity metric above.

Without the constraint, sampling 36 voices from a three-parameter space produces
accidental near-duplicates — two "unrelated" speakers 3 sigma apart, closer than
any *designed* impostor — and the corpus then asks the embedder to separate two
people who are physically the same person. 8 sigma keeps every unrelated pair at
least as far apart as the hardest designed impostor (the Δf0-only sibling, at
12 % ≈ 9.6 sigma)."""
MAX_SAMPLING_ATTEMPTS = 20_000

# Impostor margins. Each single-axis margin is >= 3x the within-speaker standard
# deviation *of that axis's own feature dimensions*, which is why the single-axis
# Δvtl and Δtilt are larger than the joint sibling's (EVALS M1b).
JOINT_DF0 = 0.12
JOINT_DVTL = 0.04
JOINT_DTILT = 2.0
AXIS_DF0 = 0.12
AXIS_DVTL = 0.08
AXIS_DTILT = 4.0
MIMIC_F0_TOLERANCE = 0.02  # <= 1x within-speaker sigma
MIMIC_DVTL = 0.10
MIMIC_DTILT = 5.0

ENROLL_DURATION_S = (6.5, 8.0)
PROBE_DURATION_S = (3.4, 4.8)
CONSENT_DURATION_S = (10.5, 13.5)
SUPPORT_SHORT_S = 2.0
"""Below the FR-2 consent minimum (5 s) and the enrollment minimum (3 s):
M4 scenario #20's `audio_quality` rejection needs a clip that cannot pass."""

CLEAN_GAIN_DB = (-6.0, 6.0)
CLEAN_SNR_DB = (20.0, 30.0)
HARSH_GAIN_DB = -6.0
HARSH_SNR_DB = 20.0

PHONE_BAND_HZ = (300.0, 3400.0)
PHONE_ORDER = 4
ROOM_TAPS_MS = (11.0, 17.0, 29.0)
ROOM_GAINS = (0.35, 0.22, 0.14)

VOWELS = ("a", "e", "i", "o", "u")
CONSONANTS = ("fricative", "plosive", "nasal", "approximant")
SYLLABLES_PER_SENTENCE = (6, 14)
UNIT_MS = (150, 230)
SENTENCE_PAUSE_MS = 200
DECLINATION_SPAN = 0.06
PROSODY_SD = 0.045


def _uniform(rng: np.random.Generator, band: tuple[float, float]) -> float:
    return float(rng.uniform(band[0], band[1]))


# --------------------------------------------------------------------------- #
# Speakers
# --------------------------------------------------------------------------- #


def identity_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Distance between two voices in within-speaker sigma (see the constants)."""
    df0 = abs(np.log(a["f0_base_hz"] / b["f0_base_hz"])) * 100.0 * SIGMA_PER_F0_PERCENT
    dvtl = abs(np.log(a["vtl"] / b["vtl"])) * 100.0 * SIGMA_PER_VTL_PERCENT
    dtilt = abs(a["tilt_db_oct"] - b["tilt_db_oct"]) * SIGMA_PER_TILT_DB
    return float(np.linalg.norm([df0, dvtl, dtilt]))


def _sample_params(rng: np.random.Generator, sex: str) -> dict[str, Any]:
    return {
        "f0_base_hz": round(_uniform(rng, F0_BANDS[sex]), 4),
        "vtl": round(_uniform(rng, VTL_BANDS[sex]), 4),
        "tilt_db_oct": round(_uniform(rng, TILT_BAND), 4),
        "f0_sd": round(_uniform(rng, F0_SD_BAND), 5),
        "jitter": round(_uniform(rng, JITTER_BAND), 5),
        "shimmer": round(_uniform(rng, SHIMMER_BAND), 5),
        "breathiness": round(_uniform(rng, BREATHINESS_BAND), 5),
        "bandwidth_scale": round(_uniform(rng, BANDWIDTH_BAND), 4),
        "formant_offsets_hz": [
            round(float(rng.normal(0.0, FORMANT_OFFSET_SD_HZ)), 3) for _ in range(4)
        ],
    }


def _base_speaker(
    rng: np.random.Generator,
    speaker_id: str,
    split: str,
    sex: str,
    taken: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sample a voice at least :data:`MIN_SPEAKER_SEPARATION_SIGMA` from every
    voice already placed (rejection sampling)."""
    for _ in range(MAX_SAMPLING_ATTEMPTS):
        params = _sample_params(rng, sex)
        if all(identity_distance(params, other) >= MIN_SPEAKER_SEPARATION_SIGMA for other in taken):
            break
    else:  # pragma: no cover - would mean the parameter box cannot hold 36 voices
        raise RuntimeError(
            f"could not place {speaker_id} at least "
            f"{MIN_SPEAKER_SEPARATION_SIGMA} sigma from the {len(taken)} voices already sampled"
        )
    taken.append(params)
    return {
        "id": speaker_id,
        "split": split,
        "role": "base",
        "base_id": None,
        "relation": None,
        "axis": None,
        "sex": sex,
        "params": params,
    }


def _sibling(
    base: dict[str, Any],
    suffix: str,
    relation: str,
    axis: str | None,
    *,
    df0: float = 0.0,
    dvtl: float = 0.0,
    dtilt: float = 0.0,
) -> dict[str, Any]:
    """A perturbed copy of ``base`` — every non-perturbed parameter is identical.

    Identity ground truth for the single-axis classes rests on this: the Δvtl-only
    sibling differs from its base on the vocal-tract axis and on *nothing else*,
    so an embedder whose formant dimensions are dead cannot tell them apart
    (EVALS M1b).
    """
    params = dict(base["params"])
    params["f0_base_hz"] = round(params["f0_base_hz"] * (1.0 + df0), 4)
    params["vtl"] = round(params["vtl"] * (1.0 + dvtl), 4)
    params["tilt_db_oct"] = round(float(np.clip(params["tilt_db_oct"] + dtilt, -20.0, -3.0)), 4)
    params["formant_offsets_hz"] = list(base["params"]["formant_offsets_hz"])
    return {
        "id": f"{base['id']}-{suffix}",
        "split": base["split"],
        "role": "impostor",
        "base_id": base["id"],
        "relation": relation,
        "axis": axis,
        "sex": base["sex"],
        "params": params,
    }


def _mimic(rng: np.random.Generator, base: dict[str, Any]) -> dict[str, Any]:
    """A different person who happens to match the target's pitch.

    Pitch is the one identity axis a housemate can consciously imitate, so the
    mimic sits within 1 within-speaker sigma of the target's F0 while its tract
    and source slope are >= 3 sigma away — an embedder that reduces to pitch
    matching accepts it (SCOPE decision 4).
    """
    sign = 1.0 if rng.random() < 0.5 else -1.0
    params = _sample_params(rng, base["sex"])
    params["f0_base_hz"] = round(
        base["params"]["f0_base_hz"]
        * (1.0 + float(rng.uniform(-MIMIC_F0_TOLERANCE, MIMIC_F0_TOLERANCE))),
        4,
    )
    params["vtl"] = round(base["params"]["vtl"] * (1.0 + sign * MIMIC_DVTL), 4)
    params["tilt_db_oct"] = round(
        float(np.clip(base["params"]["tilt_db_oct"] - sign * MIMIC_DTILT, -20.0, -3.0)), 4
    )
    return {
        "id": f"{base['id']}-mimic",
        "split": base["split"],
        "role": "impostor",
        "base_id": base["id"],
        "relation": "mimic",
        "axis": "f0",
        "sex": base["sex"],
        "params": params,
    }


def build_population(rng: np.random.Generator) -> list[dict[str, Any]]:
    """36 base speakers (24 eval + 12 dev) plus their impostors (EVALS table)."""
    bases: list[dict[str, Any]] = []
    for index in range(N_EVAL_SPEAKERS):
        sex = "male" if index % 2 == 0 else "female"
        bases.append(_base_speaker(rng, f"S{index + 1:02d}", "eval", sex))
    for index in range(N_DEV_SPEAKERS):
        sex = "female" if index % 2 == 0 else "male"
        bases.append(_base_speaker(rng, f"D{index + 1:02d}", "dev", sex))

    speakers = list(bases)
    eval_bases = [s for s in bases if s["split"] == "eval"]
    dev_bases = [s for s in bases if s["split"] == "dev"]

    # joint siblings: eval S01-S12 and 6 dev speakers
    for base in eval_bases[:12] + dev_bases[:6]:
        speakers.append(
            _sibling(
                base,
                "sib-joint",
                "joint",
                None,
                df0=JOINT_DF0,
                dvtl=JOINT_DVTL,
                dtilt=JOINT_DTILT,
            )
        )

    # single-axis siblings: 4 eval + 2 dev speakers per axis, alternating sign so
    # the perturbation direction is not itself a cue.
    axis_groups = (
        ("f0", eval_bases[0:4] + dev_bases[0:2]),
        ("vtl", eval_bases[4:8] + dev_bases[2:4]),
        ("tilt", eval_bases[8:12] + dev_bases[4:6]),
    )
    for axis, group in axis_groups:
        for position, base in enumerate(group):
            sign = 1.0 if position % 2 == 0 else -1.0
            kwargs: dict[str, float] = {}
            if axis == "f0":
                kwargs["df0"] = AXIS_DF0 if sign > 0 else -AXIS_DF0 / (1.0 + AXIS_DF0)
            elif axis == "vtl":
                kwargs["dvtl"] = AXIS_DVTL if sign > 0 else -AXIS_DVTL / (1.0 + AXIS_DVTL)
            else:
                kwargs["dtilt"] = sign * AXIS_DTILT
            speakers.append(_sibling(base, f"sib-{axis}", "single_axis", axis, **kwargs))

    # pitch mimics: eval S13-S18
    for base in eval_bases[12:18]:
        speakers.append(_mimic(rng, base))
    return speakers


# --------------------------------------------------------------------------- #
# Utterances
# --------------------------------------------------------------------------- #


def _utterance(
    rng: np.random.Generator,
    speaker: dict[str, Any],
    kind: str,
    index: int,
    duration_s: float,
    channel: str,
    variant: str,
    *,
    gain_db: float | None = None,
    snr_db: float | None = None,
) -> dict[str, Any]:
    role = f"{speaker['id']}/{kind}/{index}"
    return {
        "role": role,
        "speaker_id": speaker["id"],
        "split": speaker["split"],
        "kind": kind,
        "index": index,
        "variant": variant,
        "channel": channel,
        "duration_s": round(duration_s, 4),
        "gain_db": round(_uniform(rng, CLEAN_GAIN_DB) if gain_db is None else gain_db, 4),
        "snr_db": round(_uniform(rng, CLEAN_SNR_DB) if snr_db is None else snr_db, 4),
        "f0_scale": round(float(1.0 + rng.normal(0.0, WITHIN_SPEAKER_F0_SD)), 6),
        "seed": int(rng.integers(1, 2**31 - 1)),
        "path": f"voices/{speaker['id']}/{kind}-{index}.wav",
    }


def build_utterances(
    rng: np.random.Generator, speakers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-speaker takes, with the channel plan EVALS specifies.

    Enrollment is always ``clean`` and probes 2/3 are always channel-filtered, so
    cross-channel genuine trials (clean enrollment vs. filtered probe) exist by
    construction — the product's own intake reality.
    """
    by_id = {s["id"]: s for s in speakers}
    utterances: list[dict[str, Any]] = []
    eval_bases = [s for s in speakers if s["role"] == "base" and s["split"] == "eval"]
    dev_bases = [s for s in speakers if s["role"] == "base" and s["split"] == "dev"]
    harsh_extra = {s["id"] for s in eval_bases[:12]} | {s["id"] for s in dev_bases[:6]}
    channel_extra = {s["id"] for s in eval_bases[12:]} | {s["id"] for s in dev_bases[6:]}

    for speaker in speakers:
        if speaker["role"] == "base":
            for k in range(3):
                utterances.append(
                    _utterance(
                        rng, speaker, "enroll", k, _uniform(rng, ENROLL_DURATION_S), "clean", "clean"
                    )
                )
            for k in range(4):
                channel = ("clean", "clean", "phone", "room")[k]
                utterances.append(
                    _utterance(
                        rng,
                        speaker,
                        "probe",
                        k,
                        _uniform(rng, PROBE_DURATION_S),
                        channel,
                        "clean" if channel == "clean" else "channel",
                    )
                )
            utterances.append(
                _utterance(
                    rng, speaker, "consent", 0, _uniform(rng, CONSENT_DURATION_S), "clean", "clean"
                )
            )
            if speaker["id"] in harsh_extra:
                utterances.append(
                    _utterance(
                        rng,
                        speaker,
                        "consent",
                        1,
                        _uniform(rng, CONSENT_DURATION_S),
                        "clean",
                        "harsh",
                        gain_db=HARSH_GAIN_DB,
                        snr_db=HARSH_SNR_DB,
                    )
                )
            if speaker["id"] in channel_extra:
                channel = "phone" if int(speaker["id"][1:]) % 2 else "room"
                utterances.append(
                    _utterance(
                        rng,
                        speaker,
                        "consent",
                        1,
                        _uniform(rng, CONSENT_DURATION_S),
                        channel,
                        "channel",
                    )
                )
        else:
            n_probes = 4 if speaker["relation"] == "single_axis" else 2
            for k in range(n_probes):
                utterances.append(
                    _utterance(
                        rng, speaker, "probe", k, _uniform(rng, PROBE_DURATION_S), "clean", "clean"
                    )
                )
            utterances.append(
                _utterance(
                    rng, speaker, "consent", 0, _uniform(rng, CONSENT_DURATION_S), "clean", "clean"
                )
            )

    # Support clips for the M4 scenario suite: an unusably short take, which the
    # FR-2 screen must refuse (scenario #20 expects `audio_quality`).
    for speaker_id in ("S01", "S02"):
        utterances.append(
            _utterance(
                rng, by_id[speaker_id], "support", 0, SUPPORT_SHORT_S, "clean", "unusable_short"
            )
        )
    return utterances


def build_labels(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """The committed ground truth: population, relations, generation parameters."""
    rng = np.random.default_rng(seed)
    speakers = build_population(rng)
    utterances = build_utterances(rng, speakers)
    return {
        "seed": seed,
        "sample_rate": TARGET_SAMPLE_RATE,
        "generator": "evals/fixtures/generate_voices.py",
        "margins": {
            "joint": {"df0": JOINT_DF0, "dvtl": JOINT_DVTL, "dtilt": JOINT_DTILT},
            "single_axis": {"df0": AXIS_DF0, "dvtl": AXIS_DVTL, "dtilt": AXIS_DTILT},
            "mimic": {
                "f0_tolerance": MIMIC_F0_TOLERANCE,
                "dvtl": MIMIC_DVTL,
                "dtilt": MIMIC_DTILT,
            },
            "within_speaker_f0_sd": WITHIN_SPEAKER_F0_SD,
        },
        "channels": {
            "clean": {"gain_db": list(CLEAN_GAIN_DB), "snr_db": list(CLEAN_SNR_DB)},
            "phone": {"band_hz": list(PHONE_BAND_HZ), "order": PHONE_ORDER},
            "room": {"taps_ms": list(ROOM_TAPS_MS), "gains": list(ROOM_GAINS)},
            "harsh": {"gain_db": HARSH_GAIN_DB, "snr_db": HARSH_SNR_DB},
        },
        "speakers": speakers,
        "utterances": utterances,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def voicebox_params(speaker: dict[str, Any], f0_scale: float) -> VoiceboxParams:
    """The fixture regime: 4 formants, jitter, shimmer, breathiness, offsets."""
    params = speaker["params"]
    f0 = params["f0_base_hz"] * f0_scale
    return VoiceboxParams(
        f0_base_hz=f0,
        f0_range_hz=f0 * 0.10,
        formant_scale=params["vtl"],
        tilt_db_oct=params["tilt_db_oct"],
        n_formants=4,
        jitter=params["jitter"],
        shimmer=params["shimmer"],
        breathiness=params["breathiness"],
        bandwidth_scale=params["bandwidth_scale"],
        formant_offsets_hz=tuple(params["formant_offsets_hz"]),
    )


def sentence_units(rng: np.random.Generator, target_s: float) -> list[Unit]:
    """Pseudo-sentences: 6-14 syllables over the five vowel targets, with
    sentence-level declination and per-syllable prosody variation."""
    units: list[Unit] = []
    elapsed = 0.0
    vowel_index = int(rng.integers(0, len(VOWELS)))
    while elapsed < target_s:
        n_syllables = int(rng.integers(*SYLLABLES_PER_SENTENCE))
        for position in range(n_syllables):
            duration = int(rng.integers(*UNIT_MS))
            declination = 1.0 + DECLINATION_SPAN * (0.5 - position / max(n_syllables - 1, 1))
            units.append(
                Unit(
                    duration_ms=duration,
                    vowel=VOWELS[vowel_index % len(VOWELS)],
                    onset=str(rng.choice(CONSONANTS)) if rng.random() < 0.7 else None,
                    f0_scale=float(
                        np.clip(declination * (1.0 + rng.normal(0.0, PROSODY_SD)), 0.75, 1.3)
                    ),
                    amplitude=float(0.75 + 0.35 * rng.random()),
                )
            )
            vowel_index += 1
            elapsed += duration / 1000.0
            if elapsed >= target_s:
                break
        if elapsed < target_s:
            units.append(Unit(duration_ms=SENTENCE_PAUSE_MS))
            elapsed += SENTENCE_PAUSE_MS / 1000.0
    return units


def apply_channel(samples: np.ndarray, channel: str, sample_rate: int) -> np.ndarray:
    """One of the three recorded channel conditions (EVALS)."""
    if channel == "clean":
        return samples
    if channel == "phone":
        sos = butter_bandpass_sos(*PHONE_BAND_HZ, sample_rate, PHONE_ORDER)
        return apply_sos(samples, sos)
    if channel == "room":
        out = samples.copy()
        for delay_ms, gain in zip(ROOM_TAPS_MS, ROOM_GAINS, strict=True):
            delay = int(delay_ms * sample_rate / 1000.0)
            if delay < samples.shape[0]:
                out[delay:] += gain * samples[: samples.shape[0] - delay]
        return out
    raise ValueError(f"unknown channel condition {channel!r}")


def render_utterance(speaker: dict[str, Any], utterance: dict[str, Any]) -> AudioClip:
    """Render one labelled take. Pure function of the label record."""
    sample_rate = TARGET_SAMPLE_RATE
    rng = np.random.default_rng(utterance["seed"])
    units = sentence_units(rng, utterance["duration_s"])
    samples = synthesize_units(
        units,
        voicebox_params(speaker, utterance["f0_scale"]),
        sample_rate=sample_rate,
        seed=utterance["seed"],
    )
    samples = apply_channel(samples, utterance["channel"], sample_rate)
    samples = samples * 10.0 ** (utterance["gain_db"] / 20.0)
    power = float(np.mean(samples**2)) if samples.size else 0.0
    if power > 0.0:
        noise_power = power / (10.0 ** (utterance["snr_db"] / 10.0))
        samples = samples + rng.normal(0.0, np.sqrt(noise_power), samples.shape[0])
    samples = np.clip(samples, -0.999, 0.999)
    return AudioClip(samples=samples, sample_rate=sample_rate)


def materialize(
    labels: dict[str, Any], voices_dir: Path = VOICES_DIR, *, force: bool = False
) -> list[Path]:
    """Render every labelled take that is not already cached."""
    speakers = {s["id"]: s for s in labels["speakers"]}
    written: list[Path] = []
    for utterance in labels["utterances"]:
        path = voices_dir.parent / utterance["path"]
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        clip = render_utterance(speakers[utterance["speaker_id"]], utterance)
        path.write_bytes(encode_wav(clip))
        written.append(path)
    return written


def build_manifest(labels: dict[str, Any], voices_dir: Path = VOICES_DIR) -> dict[str, Any]:
    """path -> sha256 of the PCM payload, plus the corpus totals."""
    files: dict[str, str] = {}
    total_seconds = 0.0
    for utterance in labels["utterances"]:
        path = voices_dir.parent / utterance["path"]
        raw = path.read_bytes()
        files[utterance["path"]] = hashlib.sha256(raw).hexdigest()
        total_seconds += utterance["duration_s"]
    return {
        "count": len(files),
        "total_duration_s": round(total_seconds, 3),
        "sha256": dict(sorted(files.items())),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--write", action="store_true", help="re-author labels.json and corpus_manifest.json"
    )
    parser.add_argument("--force", action="store_true", help="re-render cached WAVs")
    args = parser.parse_args(argv)

    labels = build_labels(args.seed) if args.write else json.loads(LABELS_PATH.read_text())
    if args.write:
        _write_json(LABELS_PATH, labels)
    written = materialize(labels, force=args.force)
    manifest = build_manifest(labels)
    if args.write:
        _write_json(MANIFEST_PATH, manifest)
    print(
        f"speakers={len(labels['speakers'])} utterances={len(labels['utterances'])} "
        f"rendered={len(written)} cached={manifest['count'] - len(written)} "
        f"duration={manifest['total_duration_s'] / 60.0:.1f} min"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
