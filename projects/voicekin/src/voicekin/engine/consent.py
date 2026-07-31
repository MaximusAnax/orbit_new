"""FR-5 consent statement rendering and the FR-6 authorization gate.

``authorize()`` is the **only** constructor of :class:`Authorization`, and both
the renderer and the deliverer require one — complete mediation through a single
choke point (Saltzer & Schroeder 1975). Everything here is pure: ``now`` is an
argument, never a clock read.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from voicekin.models import (
    ConsentRecord,
    ConsentStatus,
    Context,
    ProfileStatus,
    RefusalReason,
    VoiceProfile,
)

CONSENT_STATEMENT_PLACEHOLDERS = (
    "{owner_name}",
    "{operator}",
    "{contexts}",
    "{expiry_clause}",
    "{nonce}",
    "{date}",
)


# --------------------------------------------------------------------------- #
# Timestamps (callers supply them; the engine only interprets them)
# --------------------------------------------------------------------------- #


def parse_ts(value: str) -> datetime:
    """Parse a caller-supplied ISO-8601 timestamp; naive values are read as UTC."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_date(value: str) -> str:
    """The date part of a timestamp, for the consent statement's ``{date}``."""
    return parse_ts(value).date().isoformat()


def days_between(earlier: str, later: str) -> float:
    return (parse_ts(later) - parse_ts(earlier)) / timedelta(days=1)


# --------------------------------------------------------------------------- #
# FR-5(a) statement rendering
# --------------------------------------------------------------------------- #


def pluralize_context(context: Context | str) -> str:
    word = str(context)
    return f"{word}es" if word.endswith(("s", "x", "ch", "sh")) else f"{word}s"


def format_contexts(contexts: Sequence[Context | str]) -> str:
    if not contexts:
        raise ValueError("a consent statement must name at least one context")
    return ", ".join(pluralize_context(c) for c in contexts)


def expiry_clause(expires_at: str | None) -> str:
    if expires_at is None:
        return "does not expire"
    return f"expires on {iso_date(expires_at)}"


def render_statement(
    template: str,
    *,
    owner_name: str,
    operator: str,
    contexts: Sequence[Context | str],
    expires_at: str | None,
    nonce: str,
    now: str,
) -> str:
    """Render the committed consent statement template (FR-5(a))."""
    missing = [p for p in CONSENT_STATEMENT_PLACEHOLDERS if p not in template]
    if missing:
        raise ValueError(f"consent statement template is missing {', '.join(missing)}")
    return template.format(
        owner_name=owner_name,
        operator=operator,
        contexts=format_contexts(contexts),
        expiry_clause=expiry_clause(expires_at),
        nonce=nonce,
        date=iso_date(now),
    )


# --------------------------------------------------------------------------- #
# FR-6 gate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SynthesisRequest:
    """What the gate is being asked to authorize."""

    profile_id: str
    context: Context


_GRANT_TOKEN = object()


class UnauthorizedConstruction(RuntimeError):
    """Raised when something other than :func:`authorize` builds an Authorization."""


@dataclass(frozen=True)
class Authorization:
    """A capability value: proof that FR-6 said yes, at ``issued_at``.

    Never stored (DATA_MODEL "derived values"); its evidence lands in the
    utterance, delivery and audit rows instead.
    """

    profile_id: str
    consent_id: str
    context: Context
    issued_at: str
    enrollment_fingerprint: str
    grant_token: object = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if self.grant_token is not _GRANT_TOKEN:
            raise UnauthorizedConstruction(
                "Authorization values may only be produced by engine.consent.authorize()"
            )


@dataclass(frozen=True)
class Refusal:
    """The gate said no, and why."""

    profile_id: str
    context: Context
    reason: RefusalReason
    refused_at: str


def governing_consent(consents: Iterable[ConsentRecord]) -> ConsentRecord | None:
    """The record with the greatest ``(drafted_at, draft_index)`` (FR-6).

    Only this record can supply a refusal reason or an authorization; older
    records are never consulted, which is what keeps refusal reasons describing
    the operator's latest attempt.
    """
    records = list(consents)
    if not records:
        return None
    return max(records, key=lambda c: (parse_ts(c.drafted_at), c.draft_index))


def is_expired(consent: ConsentRecord, now: str) -> bool:
    """Expiry refuses **at** the boundary (FR-6 step 7)."""
    if consent.expires_at is None:
        return False
    return parse_ts(now) >= parse_ts(consent.expires_at)


def is_effective(consent: ConsentRecord, profile: VoiceProfile, now: str) -> bool:
    """The DATA_MODEL "effective consent" predicate, used by the FR-5(a) precondition."""
    return (
        consent.status is ConsentStatus.VERIFIED
        and consent.revoked_at is None
        and not is_expired(consent, now)
        and consent.enrollment_fingerprint == profile.enrollment_fingerprint
    )


def authorize(
    profile: VoiceProfile,
    consents: Iterable[ConsentRecord],
    request: SynthesisRequest,
    now: str,
) -> Authorization | Refusal:
    """The complete-mediation gate (FR-6).

    Steps 1-3 are profile-level; steps 4-9 read the governing consent record and
    nothing else. The evaluation order is the ``RefusalReason`` declaration order.
    """
    if request.profile_id != profile.id:
        raise ValueError("request.profile_id does not match the profile being authorized")

    def refuse(reason: RefusalReason) -> Refusal:
        return Refusal(
            profile_id=profile.id, context=request.context, reason=reason, refused_at=now
        )

    if profile.status is ProfileStatus.PURGED:
        return refuse(RefusalReason.PROFILE_PURGED)
    if not profile.enabled:
        return refuse(RefusalReason.PROFILE_DISABLED)
    if profile.enrollment_fingerprint is None:
        return refuse(RefusalReason.NO_ENROLLMENT)

    governing = governing_consent(consents)
    if governing is None or governing.status is ConsentStatus.DRAFT:
        return refuse(RefusalReason.NO_CONSENT)
    if governing.status is ConsentStatus.REJECTED:
        return refuse(RefusalReason.CONSENT_REJECTED)
    if governing.revoked_at is not None:
        return refuse(RefusalReason.CONSENT_REVOKED)
    if is_expired(governing, now):
        return refuse(RefusalReason.CONSENT_EXPIRED)
    if governing.enrollment_fingerprint != profile.enrollment_fingerprint:
        return refuse(RefusalReason.ENROLLMENT_CHANGED)
    if request.context not in governing.scope_contexts:
        return refuse(RefusalReason.SCOPE_MISMATCH)

    return Authorization(
        profile_id=profile.id,
        consent_id=governing.id,
        context=request.context,
        issued_at=now,
        enrollment_fingerprint=profile.enrollment_fingerprint,
        grant_token=_GRANT_TOKEN,
    )


def awaiting_consent(
    profile: VoiceProfile,
    consents: Iterable[ConsentRecord],
    now: str,
    grace_days: int,
) -> bool:
    """FR-1 display flag: enrolled long ago, never once consented.

    A flag, not an auto-purge — destroying a voice owner's data on a timer is its
    own hazard (REVIEW finding 13). Never an authorization input.
    """
    if profile.enrolled_at is None or profile.status is ProfileStatus.PURGED:
        return False
    ever_verified = any(
        c.status in (ConsentStatus.VERIFIED, ConsentStatus.REVOKED) for c in consents
    )
    if ever_verified:
        return False
    return days_between(profile.enrolled_at, now) > grace_days


__all__ = [
    "CONSENT_STATEMENT_PLACEHOLDERS",
    "Authorization",
    "Refusal",
    "SynthesisRequest",
    "UnauthorizedConstruction",
    "authorize",
    "awaiting_consent",
    "days_between",
    "expiry_clause",
    "format_contexts",
    "governing_consent",
    "is_effective",
    "is_expired",
    "iso_date",
    "parse_ts",
    "pluralize_context",
    "render_statement",
]
