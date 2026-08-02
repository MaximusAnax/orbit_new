"""Provider interfaces and their offline / live implementations.

Every external capability sits behind a ``Protocol``.  The offline
implementation is the default and is what tests and evals exercise; live
implementations live in ``*_live.py`` modules so importing the offline path
never touches optional dependencies or credentials.
"""

from .award_source import AwardSource, CommittedAwardSource
from .fare_reference import CommittedFareReference, FareReference
from .goal_parser import GoalParser, RuleBasedGoalParser
from .world_provider import CommittedWorldProvider, WorldProvider, build_world

__all__ = [
    "AwardSource",
    "CommittedAwardSource",
    "CommittedFareReference",
    "CommittedWorldProvider",
    "FareReference",
    "GoalParser",
    "RuleBasedGoalParser",
    "WorldProvider",
    "build_world",
]
