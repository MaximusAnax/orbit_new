"""The pure bundle of committed data the engine reads.

``datasets.py`` (the only module that touches the filesystem) validates the five
committed datasets and hands the engine this immutable container. Engine
functions take it as an argument; they never load it themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..models import (
    AdviceTemplateCatalog,
    AdvisorConfig,
    AdvisorSettings,
    Brand,
    CalibrationConfig,
    ConditionTable,
    ImpactPrior,
    IndexConfig,
)
from .conditions import ConditionMapper
from .strata import Gazetteer

__all__ = ["EngineContext"]


@dataclass(frozen=True)
class EngineContext:
    """Gazetteer + priors + conditions + config + templates, validated and cached."""

    gazetteer: Gazetteer
    mapper: ConditionMapper
    priors: Mapping[str, ImpactPrior]
    config: AdvisorConfig
    templates: AdviceTemplateCatalog

    @classmethod
    def build(
        cls,
        *,
        brands: tuple[Brand, ...],
        priors: tuple[ImpactPrior, ...],
        conditions: ConditionTable,
        templates: AdviceTemplateCatalog,
        config: AdvisorConfig,
    ) -> EngineContext:
        return cls(
            gazetteer=Gazetteer(brands),
            mapper=ConditionMapper(conditions),
            priors=MappingProxyType({prior.key: prior for prior in priors}),
            config=config,
            templates=templates,
        )

    @property
    def index_config(self) -> IndexConfig:
        return self.config.index

    @property
    def settings(self) -> AdvisorSettings:
        return self.config.advisor

    @property
    def calibration(self) -> CalibrationConfig:
        return self.config.calibration
