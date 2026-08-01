"""FR-14: the Typer administrative surface — what a human runs with WAVs in hand.

Thin (CONVENTIONS): parse, call the service, print. Exit codes:

* ``0`` — success;
* ``1`` — unknown entity, unknown output, or a failed ``audit verify``;
* ``2`` — a precondition the operator can fix (FR-5: e.g. drafting while a
  consent is effective, granting without a draft, re-initializing);
* ``3`` — the FR-6 gate refused (synthesis or delivery); the refusal is
  persisted and audited before the CLI exits.

The data home resolves ``--data-home`` > ``$VOICEKIN_HOME`` > ``~/.voicekin``.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

import typer

from voicekin import __version__
from voicekin.models import Context, TargetKind
from voicekin.services import NotFoundError, ServiceError, VoiceKinService

EXIT_NOT_FOUND = 1
EXIT_PRECONDITION = 2
EXIT_REFUSED = 3

app = typer.Typer(
    name="voicekin",
    help=(
        "Consent-gated personal voice profiles for smart-home speech. "
        "Enroll a voice from WAV samples, verify a recorded consent statement "
        "by speaker match, and only then let the house speak in that voice — "
        "every synthesis and delivery authorized, audited, and revocable."
    ),
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
profile_app = typer.Typer(help="Create, inspect, disable, enable and purge voice profiles.")
sample_app = typer.Typer(help="Inspect and remove enrollment samples.")
consent_app = typer.Typer(help="Draft, read, grant, revoke and inspect consent records.")
target_app = typer.Typer(help="Manage delivery targets (file sinks, Home Assistant players).")
audit_app = typer.Typer(help="Read and verify the tamper-evident audit chain.")
app.add_typer(profile_app, name="profile")
app.add_typer(sample_app, name="sample")
app.add_typer(consent_app, name="consent")
app.add_typer(target_app, name="target")
app.add_typer(audit_app, name="audit")

_STATE: dict[str, Path | None] = {"data_home": None}

DataHomeOption = Annotated[
    Path | None,
    typer.Option(
        "--data-home",
        help="VoiceKin data home (default: $VOICEKIN_HOME or ~/.voicekin).",
        envvar="VOICEKIN_HOME",
        show_default=False,
    ),
]
NowOption = Annotated[
    str | None,
    typer.Option("--now", help="ISO-8601 timestamp to record (default: current UTC time)."),
]


@app.callback()
def _main_callback(data_home: DataHomeOption = None) -> None:
    if data_home is not None:
        _STATE["data_home"] = data_home


def _service() -> VoiceKinService:
    from voicekin.bootstrap import build_service

    return build_service(_STATE["data_home"])


def _now(value: str | None) -> str:
    if value is not None:
        return value
    from voicekin.bootstrap import now_utc

    return now_utc()


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code)


def _run(operation):
    """Translate service exceptions into the documented exit codes."""
    try:
        return operation()
    except NotFoundError as exc:
        raise _fail(str(exc), EXIT_NOT_FOUND) from exc
    except ServiceError as exc:
        raise _fail(str(exc), EXIT_PRECONDITION) from exc


def _parse_contexts(scope: str) -> list[Context]:
    contexts = []
    for token in scope.split(","):
        cleaned = token.strip()
        if not cleaned:
            continue
        try:
            contexts.append(Context(cleaned))
        except ValueError as exc:
            allowed = ", ".join(c.value for c in Context)
            raise _fail(f"unknown context {cleaned!r} (choose from: {allowed})",
                        EXIT_PRECONDITION) from exc
    if not contexts:
        raise _fail("--scope must name at least one context", EXIT_PRECONDITION)
    return contexts


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #


@app.command()
def init(
    operator: Annotated[str, typer.Option(help="Operator name rendered into every consent statement.")],
    data_home: DataHomeOption = None,
    now: NowOption = None,
) -> None:
    """Create the data home, database, audio directories and instance record (FR-1)."""
    if data_home is not None:
        _STATE["data_home"] = data_home
    service = _service()
    instance = _run(lambda: service.initialize(operator, _now(now)))
    typer.echo(f"initialized {instance.data_home} for operator {instance.operator_name!r}")


@app.command()
def version() -> None:
    """Print the VoiceKin version."""
    typer.echo(f"voicekin {__version__}")


# --------------------------------------------------------------------------- #
# profiles
# --------------------------------------------------------------------------- #


@profile_app.command("add")
def profile_add(
    profile_id: Annotated[str, typer.Argument(help="Profile slug, e.g. 'partner'.")],
    name: Annotated[str, typer.Option("--name", help="Display name (the {owner_name} of the consent statement).")],
    relationship: Annotated[str, typer.Option(help="Free text: self, partner, parent, ...")] = "self",
    now: NowOption = None,
) -> None:
    """Create a voice profile (FR-1)."""
    service = _service()
    profile = _run(lambda: service.create_profile(profile_id, name, relationship, _now(now)))
    typer.echo(f"created profile {profile.id} ({profile.display_name}, {profile.relationship})")


@profile_app.command("list")
def profile_list(now: NowOption = None) -> None:
    """List profiles with enrollment/consent state (FR-1)."""
    service = _service()
    views = _run(lambda: service.list_profiles(_now(now)))
    if not views:
        typer.echo("no profiles yet — `voicekin profile add <id> --name ...`")
        return
    for view in views:
        profile = view.profile
        flags = [str(profile.status)]
        flags.append("enabled" if profile.enabled else "disabled")
        flags.append("enrolled" if profile.is_enrolled else "not-enrolled")
        flags.append(f"consent={view.consent_status or 'none'}")
        if view.awaiting_consent:
            flags.append("awaiting-consent")
        typer.echo(f"{profile.id:<16} {profile.display_name:<20} {' '.join(flags)}")


@profile_app.command("show")
def profile_show(profile_id: str, now: NowOption = None) -> None:
    """Show one profile in detail."""
    service = _service()
    view = _run(lambda: service.profile_view(profile_id, _now(now)))
    profile = view.profile
    typer.echo(f"id:            {profile.id}")
    typer.echo(f"display name:  {profile.display_name}")
    typer.echo(f"relationship:  {profile.relationship}")
    typer.echo(f"status:        {profile.status} ({'enabled' if profile.enabled else 'disabled'})")
    typer.echo(f"enrolled:      {profile.is_enrolled} (since {profile.enrolled_at or '—'})")
    typer.echo(f"fingerprint:   {profile.enrollment_fingerprint or '—'}")
    typer.echo(f"consent:       {view.consent_status or 'none'}")
    if view.awaiting_consent:
        typer.echo("flag:          awaiting-consent — enrolled without a verified consent")


@profile_app.command("disable")
def profile_disable(profile_id: str, now: NowOption = None) -> None:
    """Disable synthesis for a profile without touching consent (FR-1)."""
    service = _service()
    _run(lambda: service.set_enabled(profile_id, False, _now(now)))
    typer.echo(f"disabled {profile_id}")


@profile_app.command("enable")
def profile_enable(profile_id: str, now: NowOption = None) -> None:
    """Re-enable synthesis for a profile (FR-1)."""
    service = _service()
    _run(lambda: service.set_enabled(profile_id, True, _now(now)))
    typer.echo(f"enabled {profile_id}")


@profile_app.command("purge")
def profile_purge(profile_id: str, now: NowOption = None) -> None:
    """Erase a voice completely — audio, embeddings, outputs, delivered copies (FR-7).

    Terminal and immediate: no grace period, no undo (GDPR Art. 7(3)/17).
    """
    service = _service()
    report = _run(lambda: service.purge_profile(profile_id, _now(now)))
    typer.echo(
        f"purged {profile_id}: managed files deleted={report.files_deleted} "
        f"missing={report.files_missing} failed={report.files_failed}; delivered copies "
        f"deleted={report.delivered_deleted} missing={report.delivered_missing} "
        f"failed={report.delivered_failed}"
    )
    typer.echo(
        "note: copies outside VoiceKin's reach (Home Assistant caches, remote media "
        "stores, files copied by hand) are NOT erased — only hashes remain here."
    )


# --------------------------------------------------------------------------- #
# enrollment
# --------------------------------------------------------------------------- #


@app.command()
def enroll(
    profile_id: Annotated[str, typer.Argument(help="Profile to enroll into.")],
    wavs: Annotated[list[Path], typer.Argument(help="WAV files (PCM16; 3+ samples total).")],
    now: NowOption = None,
) -> None:
    """Screen, embed and enroll WAV samples; prints a per-file accept/reject report (FR-2/FR-3)."""
    service = _service()
    results = _run(lambda: service.add_samples(profile_id, list(wavs), _now(now)))
    for result in results:
        if result.accepted:
            typer.echo(
                f"accepted {result.source} -> sample {result.sample.id} "
                f"({result.sample.duration_s:.1f}s, snr {result.sample.snr_db:.1f} dB, "
                f"voiced {result.sample.voiced_ratio:.2f})"
            )
        else:
            typer.echo(f"rejected {result.source}: {result.sample.reject_reason}")
    profile = _run(lambda: service.require_profile(profile_id))
    if profile.is_enrolled:
        typer.echo(f"profile {profile_id} is enrolled (fingerprint {profile.enrollment_fingerprint})")
    else:
        typer.echo(
            f"profile {profile_id} is not yet enrolled-complete "
            "(needs >= 3 accepted samples and >= 10 s of voiced audio)"
        )


@sample_app.command("list")
def sample_list(profile_id: str) -> None:
    """List a profile's enrollment samples."""
    service = _service()
    samples = _run(lambda: service.list_samples(profile_id))
    if not samples:
        typer.echo(f"no samples for {profile_id}")
        return
    for sample in samples:
        state = str(sample.status) if sample.reject_reason is None else f"rejected:{sample.reject_reason}"
        typer.echo(
            f"{sample.id}  #{sample.sample_index}  {state:<28} "
            f"{sample.duration_s:5.1f}s  sha256 {sample.sha256[:16]}…"
        )


@sample_app.command("remove")
def sample_remove(profile_id: str, sample_id: str, now: NowOption = None) -> None:
    """Remove a sample and its audio; the enrollment fingerprint changes (FR-3/FR-6)."""
    service = _service()
    profile = _run(lambda: service.remove_sample(profile_id, sample_id, _now(now)))
    typer.echo(f"removed {sample_id}; fingerprint is now {profile.enrollment_fingerprint or '—'}")


# --------------------------------------------------------------------------- #
# consent
# --------------------------------------------------------------------------- #


@consent_app.command("draft")
def consent_draft(
    profile_id: str,
    scope: Annotated[str, typer.Option(help="Comma-separated contexts, e.g. announcement,reminder.")],
    expires: Annotated[str | None, typer.Option(help="Optional ISO expiry, e.g. 2027-06-30T00:00:00Z.")] = None,
    nonce_seed: Annotated[int | None, typer.Option(help="63-bit nonce seed (drawn once if omitted; FR-15).")] = None,
    now: NowOption = None,
) -> None:
    """Draft a consent record and print the statement the voice owner must read (FR-5a)."""
    service = _service()
    contexts = _parse_contexts(scope)
    seed = nonce_seed if nonce_seed is not None else secrets.randbits(63)
    record = _run(lambda: service.draft_consent(profile_id, contexts, expires, seed, _now(now)))
    typer.echo(f"drafted consent {record.id} (nonce {record.nonce})")
    typer.echo("have the voice owner read this aloud, then run `voicekin consent grant`:")
    typer.echo("")
    typer.echo(record.statement_text)


@consent_app.command("statement")
def consent_statement(profile_id: str) -> None:
    """Print the exact text of the pending consent draft (FR-5a)."""
    service = _service()
    typer.echo(_run(lambda: service.consent_statement(profile_id)))


@consent_app.command("grant")
def consent_grant(
    profile_id: str,
    recording: Annotated[Path, typer.Argument(help="WAV of the owner reading the statement.")],
    now: NowOption = None,
) -> None:
    """Verify the recording is the enrolled voice and activate consent (FR-5b)."""
    service = _service()
    record = _run(lambda: service.grant_consent(profile_id, recording, _now(now)))
    if record.status.value == "verified":
        typer.echo(
            f"consent {record.id} verified (similarity {record.similarity:.3f} "
            f">= threshold {record.threshold:.3f})"
        )
    else:
        detail = ""
        if record.similarity is not None and record.threshold is not None:
            detail = f" (similarity {record.similarity:.3f} < threshold {record.threshold:.3f})"
        typer.echo(f"consent rejected: {record.reject_reason}{detail}", err=True)
        typer.echo("the recording was hashed and discarded, not stored", err=True)
        raise typer.Exit(EXIT_REFUSED)


@consent_app.command("revoke")
def consent_revoke(
    profile_id: str,
    reason: Annotated[str | None, typer.Option(help="Optional reason, recorded and audited.")] = None,
    now: NowOption = None,
) -> None:
    """Withdraw consent, effective immediately — replays included (FR-7, GDPR Art. 7(3))."""
    service = _service()
    record = _run(lambda: service.revoke_consent(profile_id, reason, _now(now)))
    typer.echo(f"consent {record.id} revoked at {record.revoked_at}")


@consent_app.command("status")
def consent_status(profile_id: str) -> None:
    """Show every consent record for a profile, governing record last."""
    service = _service()
    records = _run(lambda: service.list_consents(profile_id))
    if not records:
        typer.echo(f"no consent records for {profile_id}")
        return
    for record in sorted(records, key=lambda r: (r.drafted_at, r.draft_index)):
        line = f"{record.id}  {record.status:<9} scope={','.join(record.scope_contexts)}"
        if record.expires_at:
            line += f" expires={record.expires_at}"
        if record.reject_reason:
            line += f" reject={record.reject_reason}"
        if record.revoked_at:
            line += f" revoked={record.revoked_at}"
        typer.echo(line)


# --------------------------------------------------------------------------- #
# synthesis & delivery
# --------------------------------------------------------------------------- #


@app.command()
def say(
    profile_id: Annotated[str, typer.Argument(help="Voice profile to speak in.")],
    text: Annotated[str, typer.Argument(help="What to say (<= 500 chars after normalization).")],
    context: Annotated[Context, typer.Option(help="Consent scope context of this utterance.")] = Context.ANNOUNCEMENT,
    target: Annotated[str | None, typer.Option(help="Deliver to this target after rendering.")] = None,
    seed: Annotated[int, typer.Option(help="Synthesis seed (identical seed => identical bytes).")] = 0,
    now: NowOption = None,
) -> None:
    """Authorize, render and optionally deliver an utterance (FR-6/FR-9/FR-11)."""
    service = _service()
    at = _now(now)
    result = _run(lambda: service.synthesize(profile_id, text, context, seed, at))
    if result.refused:
        assert result.refusal is not None
        typer.echo(f"refused: {result.refusal.reason} (utterance {result.utterance.id})", err=True)
        raise typer.Exit(EXIT_REFUSED)
    utterance = result.utterance
    typer.echo(f"rendered utterance {utterance.id}")
    typer.echo(f"  output:   {utterance.output_path} ({utterance.duration_s:.2f}s)")
    typer.echo(f"  sha256:   {utterance.output_sha256}")
    typer.echo(f"  consent:  {utterance.consent_id}")
    if target is not None:
        delivery = _run(lambda: service.deliver(utterance.id, target, at))
        if delivery.refusal is not None:
            typer.echo(f"delivery refused: {delivery.refusal.reason}", err=True)
            raise typer.Exit(EXIT_REFUSED)
        typer.echo(f"delivered to {target}: {delivery.delivery.status} {delivery.delivery.detail}")


@app.command()
def deliver(
    utterance_id: Annotated[str, typer.Argument(help="A rendered utterance id.")],
    target: Annotated[str, typer.Option(help="Delivery target id.")],
    now: NowOption = None,
) -> None:
    """Deliver an already-rendered utterance; re-authorizes against current consent (FR-11)."""
    service = _service()
    result = _run(lambda: service.deliver(utterance_id, target, _now(now)))
    if result.refusal is not None:
        typer.echo(f"delivery refused: {result.refusal.reason}", err=True)
        raise typer.Exit(EXIT_REFUSED)
    typer.echo(f"delivered {utterance_id} to {target}: {result.delivery.status}")
    for key, value in result.delivery.detail.items():
        typer.echo(f"  {key}: {value}")


# --------------------------------------------------------------------------- #
# targets
# --------------------------------------------------------------------------- #


@target_app.command("add")
def target_add(
    target_id: Annotated[str, typer.Argument(help="Target slug, e.g. living-room.")],
    kind: Annotated[TargetKind, typer.Option(help="file_sink or home_assistant.")],
    directory: Annotated[Path | None, typer.Option("--dir", help="file_sink: directory to write into.")] = None,
    entity: Annotated[str | None, typer.Option(help="home_assistant: media_player entity id.")] = None,
    media_dir: Annotated[Path | None, typer.Option(help="home_assistant: shared media directory.")] = None,
    now: NowOption = None,
) -> None:
    """Register a delivery target (FR-11). HA credentials come from the environment."""
    service = _service()
    if kind is TargetKind.FILE_SINK:
        if directory is None:
            raise _fail("file_sink targets need --dir", EXIT_PRECONDITION)
        config = {"dir": str(directory)}
    else:
        if entity is None or media_dir is None:
            raise _fail("home_assistant targets need --entity and --media-dir", EXIT_PRECONDITION)
        config = {"entity_id": entity, "media_dir": str(media_dir)}
    target = _run(lambda: service.add_target(target_id, kind, config, _now(now)))
    typer.echo(f"added {target.kind} target {target.id}")


@target_app.command("list")
def target_list() -> None:
    """List delivery targets."""
    service = _service()
    targets = _run(service.list_targets)
    if not targets:
        typer.echo("no targets yet — `voicekin target add <id> --kind file_sink --dir ...`")
        return
    for target in targets:
        state = "enabled" if target.enabled else "disabled"
        typer.echo(f"{target.id:<16} {target.kind:<15} {state}  {target.config}")


# --------------------------------------------------------------------------- #
# provenance & audit
# --------------------------------------------------------------------------- #


@app.command("verify-output")
def verify_output(
    wav: Annotated[Path, typer.Argument(help="A WAV file to attribute.")],
) -> None:
    """Resolve a WAV to the utterance that produced it by PCM-payload hash (FR-10)."""
    service = _service()
    provenance = _run(lambda: service.verify_output(wav.read_bytes()))
    if provenance is None:
        typer.echo("unknown_output: no rendered utterance matches this payload", err=True)
        raise typer.Exit(EXIT_NOT_FOUND)
    utterance = provenance.utterance
    typer.echo(f"utterance: {utterance.id}")
    typer.echo(f"  profile:   {provenance.profile.id} ({provenance.profile.display_name})")
    typer.echo(f"  text:      {utterance.text!r}")
    typer.echo(f"  context:   {utterance.context}")
    typer.echo(f"  consent:   {utterance.consent_id}")
    typer.echo(f"  rendered:  {utterance.requested_at} (seed {utterance.seed}, {utterance.synth_id})")
    typer.echo(f"  sha256:    {utterance.output_sha256}")
    seqs = ", ".join(str(record.seq) for record in provenance.audit_records)
    typer.echo(f"  audit seq: {seqs or '—'}")


@audit_app.command("list")
def audit_list(
    since_seq: Annotated[int, typer.Option(help="Only records with seq > this.")] = 0,
) -> None:
    """Print audit records (FR-12)."""
    service = _service()
    records = _run(lambda: service.list_audit(since_seq))
    for record in records:
        subject = record.profile_id or record.utterance_id or record.consent_id or ""
        typer.echo(f"{record.seq:>5}  {record.ts}  {record.event:<22} {subject}")
    if not records:
        typer.echo("no audit records")


@audit_app.command("verify")
def audit_verify() -> None:
    """Recompute the hash chain from genesis; anchor the head out of band (FR-12)."""
    service = _service()
    outcome = _run(service.verify_audit)
    if outcome.ok:
        typer.echo(f"audit chain OK: head_seq={outcome.head_seq} head_hash={outcome.head_hash}")
        typer.echo("record the head hash somewhere VoiceKin cannot touch — that anchor is")
        typer.echo("the only defence against a truncate-and-recompute of the whole chain.")
    else:
        typer.echo(
            f"audit chain BROKEN at seq {outcome.bad_seq}: {outcome.problem} "
            f"(last good seq {outcome.head_seq})",
            err=True,
        )
        raise typer.Exit(EXIT_NOT_FOUND)


def main() -> None:
    """Entry point for the ``voicekin`` console script."""
    app()


__all__ = ["EXIT_NOT_FOUND", "EXIT_PRECONDITION", "EXIT_REFUSED", "app", "main"]
