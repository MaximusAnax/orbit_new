"""FR-11 device delivery capability interface.

Delivery is gated: :meth:`DeviceDeliverer.deliver` requires an
:class:`~voicekin.engine.consent.Authorization`, which only FR-6's ``authorize()``
can produce. That is the difference between "revocation stops new renders" and
"revocation stops the house speaking in your voice" — an already-rendered
utterance must re-authorize before it can be played again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from voicekin.engine.consent import Authorization
from voicekin.models import DeliveryStatus, DeviceTarget, TargetKind


@dataclass(frozen=True)
class DeliveryPayload:
    """A rendered utterance plus the facts a delivered copy carries with it."""

    utterance_id: str
    wav_bytes: bytes
    output_sha256: str
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryReceipt:
    """What happened, in the shape the ``delivery.detail`` column stores."""

    status: DeliveryStatus
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeviceDeliverer(Protocol):
    """Sends a rendered utterance to one device target."""

    @property
    def kind(self) -> TargetKind: ...

    def deliver(
        self,
        payload: DeliveryPayload,
        target: DeviceTarget,
        *,
        authorization: Authorization,
    ) -> DeliveryReceipt: ...


__all__ = ["DeliveryPayload", "DeliveryReceipt", "DeviceDeliverer"]
