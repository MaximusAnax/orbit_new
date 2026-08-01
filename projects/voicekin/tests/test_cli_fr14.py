"""FR-14: the Typer administrative surface, driven through CliRunner.

Each test runs against a fresh file-backed data home under ``tmp_path`` — the
same SQLite wiring production uses — with WAV fixtures rendered by the shared
synthetic voices. Exit codes are part of the contract: 0 ok, 1 not-found/verify
failure, 2 precondition, 3 gate refusal.
"""

from __future__ import annotations

import pytest
from conftest import ALICE, BOB, CONSENT_SECONDS, ENROLL_SECONDS, write_voice
from typer.testing import CliRunner
from voicekin.cli import app

runner = CliRunner()

T0 = "2026-07-31T10:00:00Z"
T1 = "2026-07-31T11:00:00Z"
T2 = "2026-07-31T12:00:00Z"
T3 = "2026-07-31T13:00:00Z"


@pytest.fixture
def home(tmp_path):
    return tmp_path / "vkhome"


def vk(home, *args):
    return runner.invoke(app, ["--data-home", str(home), *args])


@pytest.fixture
def initialized(home):
    result = vk(home, "init", "--operator", "Abdoul", "--now", T0)
    assert result.exit_code == 0, result.output
    return home


@pytest.fixture
def alice_wavs(tmp_path):
    return [
        write_voice(tmp_path / "wav", f"e{k}", ALICE, ENROLL_SECONDS, 4100 + k)
        for k in range(3)
    ]


@pytest.fixture
def enrolled(initialized, alice_wavs):
    result = vk(initialized, "profile", "add", "partner", "--name", "Amina",
                "--relationship", "partner", "--now", T0)
    assert result.exit_code == 0, result.output
    result = vk(initialized, "enroll", "partner", *[str(p) for p in alice_wavs],
                "--now", T0)
    assert result.exit_code == 0, result.output
    assert "is enrolled" in result.output
    return initialized


@pytest.fixture
def granted(enrolled, tmp_path):
    result = vk(enrolled, "consent", "draft", "partner",
                "--scope", "announcement,reminder",
                "--nonce-seed", "4815162342", "--now", T1)
    assert result.exit_code == 0, result.output
    consent_wav = write_voice(tmp_path / "wav", "consent", ALICE, CONSENT_SECONDS, 7700)
    result = vk(enrolled, "consent", "grant", "partner", str(consent_wav), "--now", T1)
    assert result.exit_code == 0, result.output
    assert "verified" in result.output
    return enrolled


def test_fr14_help_shows_every_command_group():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "enroll", "say", "deliver", "verify-output",
                    "profile", "sample", "consent", "target", "audit"):
        assert command in result.output


def test_fr14_init_is_idempotent_only_once(initialized):
    again = vk(initialized, "init", "--operator", "Else", "--now", T1)
    assert again.exit_code == 2
    assert "already initialized" in again.output


def test_fr14_uninitialized_home_fails_fast(home):
    result = vk(home, "profile", "list")
    assert result.exit_code == 2
    assert "not initialized" in result.output


def test_fr14_enroll_reports_per_file_outcomes(initialized, alice_wavs, tmp_path):
    vk(initialized, "profile", "add", "partner", "--name", "Amina", "--now", T0)
    short = write_voice(tmp_path / "wav", "short", ALICE, 2.0, 900)
    result = vk(initialized, "enroll", "partner", str(short), *[str(p) for p in alice_wavs],
                "--now", T0)
    assert result.exit_code == 0
    assert "rejected" in result.output and "too_short" in result.output
    assert result.output.count("accepted") == 3


def test_fr14_profile_list_and_show(granted):
    listing = vk(granted, "profile", "list", "--now", T2)
    assert listing.exit_code == 0
    assert "partner" in listing.output and "consent=verified" in listing.output
    show = vk(granted, "profile", "show", "partner", "--now", T2)
    assert show.exit_code == 0
    assert "Amina" in show.output


def test_fr14_consent_statement_prints_the_exact_text(enrolled):
    vk(enrolled, "consent", "draft", "partner", "--scope", "announcement",
       "--nonce-seed", "1", "--now", T1)
    result = vk(enrolled, "consent", "statement", "partner")
    assert result.exit_code == 0
    assert "I, Amina, consent to the VoiceKin system operated by Abdoul" in result.output
    assert "Verification code:" in result.output


def test_fr14_impostor_grant_exits_3_and_discards_audio(enrolled, tmp_path):
    vk(enrolled, "consent", "draft", "partner", "--scope", "announcement",
       "--nonce-seed", "2", "--now", T1)
    impostor = write_voice(tmp_path / "wav", "impostor", BOB, CONSENT_SECONDS, 7700)
    result = vk(enrolled, "consent", "grant", "partner", str(impostor), "--now", T1)
    assert result.exit_code == 3
    assert "speaker_mismatch" in result.output
    assert "discarded" in result.output


def test_fr14_say_renders_and_delivers(granted, tmp_path):
    sink = tmp_path / "sink"
    result = vk(granted, "target", "add", "living-room", "--kind", "file_sink",
                "--dir", str(sink), "--now", T2)
    assert result.exit_code == 0, result.output
    result = vk(granted, "say", "partner", "Dinner is ready",
                "--context", "announcement", "--target", "living-room",
                "--seed", "7", "--now", T2)
    assert result.exit_code == 0, result.output
    assert "rendered utterance" in result.output
    assert "delivered to living-room: succeeded" in result.output
    assert list(sink.glob("*.wav")) and list(sink.glob("*.json"))


def test_fr14_revocation_stops_synthesis_with_exit_3(granted):
    result = vk(granted, "consent", "revoke", "partner", "--reason", "done", "--now", T2)
    assert result.exit_code == 0
    refused = vk(granted, "say", "partner", "hello", "--context", "announcement",
                 "--now", T3)
    assert refused.exit_code == 3
    assert "consent_revoked" in refused.output


def test_fr14_verify_output_round_trip(granted, tmp_path):
    result = vk(granted, "say", "partner", "Dinner is ready",
                "--context", "announcement", "--seed", "7", "--now", T2)
    assert result.exit_code == 0
    out_dir = granted / "audio" / "out"
    [wav] = list(out_dir.glob("*.wav"))
    verified = vk(granted, "verify-output", str(wav))
    assert verified.exit_code == 0
    assert "partner (Amina)" in verified.output
    assert "'dinner is ready'" in verified.output

    foreign = write_voice(tmp_path / "wav", "foreign", ALICE, 4.0, 12345)
    unknown = vk(granted, "verify-output", str(foreign))
    assert unknown.exit_code == 1
    assert "unknown_output" in unknown.output


def test_fr14_audit_list_and_verify(granted):
    listing = vk(granted, "audit", "list")
    assert listing.exit_code == 0
    assert "consent_verified" in listing.output
    verify = vk(granted, "audit", "verify")
    assert verify.exit_code == 0
    assert "audit chain OK" in verify.output and "head_hash=" in verify.output


def test_fr14_purge_prints_counts_and_the_residual_warning(granted):
    result = vk(granted, "profile", "purge", "partner", "--now", T2)
    assert result.exit_code == 0
    assert "purged partner" in result.output
    assert "outside VoiceKin's reach" in result.output
    refused = vk(granted, "say", "partner", "hello", "--context", "announcement",
                 "--now", T3)
    assert refused.exit_code == 3
    assert "profile_purged" in refused.output


def test_fr14_unknown_profile_exits_1(initialized):
    result = vk(initialized, "profile", "show", "nobody")
    assert result.exit_code == 1


def test_fr14_second_draft_while_effective_exits_2(granted):
    result = vk(granted, "consent", "draft", "partner", "--scope", "announcement",
                "--nonce-seed", "3", "--now", T2)
    assert result.exit_code == 2
    assert "already effective" in result.output


def test_fr14_bad_scope_context_exits_2(enrolled):
    result = vk(enrolled, "consent", "draft", "partner", "--scope", "sermon",
                "--nonce-seed", "4", "--now", T1)
    assert result.exit_code == 2
    assert "unknown context" in result.output
