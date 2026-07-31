"""``AwardSource``: where award-availability snapshots come from (Non-goal 3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import AwardOffer, World


@runtime_checkable
class AwardSource(Protocol):
    """Supplies the award offers the planner may consider."""

    def offers(self) -> list[AwardOffer]:
        """Every currently known award offer."""
        ...


class CommittedAwardSource:
    """Offline default: the offers committed in ``data/world/awards.json``."""

    def __init__(self, world: World) -> None:
        self._world = world

    def offers(self) -> list[AwardOffer]:
        return sorted(self._world.offers, key=lambda offer: offer.id)

    def for_program(self, program_id: str) -> list[AwardOffer]:
        return [offer for offer in self.offers() if offer.program_id == program_id]
