"""L1 Cognitive Plane -- Decision DTO subpackage.

Re-exports the DecisionOutputContent discriminated union and all
subtypes so that downstream modules can import from
``src.shared.dto_decision`` directly.
"""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field

# ---------------------------------------------------------------------------
# Import all 15 DecisionOutputContent subtypes
# ---------------------------------------------------------------------------

from cognitiveplane.shared.dto_decision.outputs import (  # noqa: F401
    Constraint,
    CoverageArea,
    EscalationTarget,
    EvidenceReference,
    FocusArea,
    ImpactAssessment,
    InspectionStrategy,
    InspectionStrategyRecommendation,
    MarginalRange,
    MarginalRangeRecommendation,
    MitigationSuggestion,
    Optimization,
    ParameterAdjustment,
    ParameterAdjustmentRecommendation,
    ParameterRecommendation,
    ParameterSet,
    ROIRecommendation,
    ROIEstimate,
    RiskFactor,
    RiskFlag,
    RiskImpact,
    RootCauseHypothesis,
    StandardReference,
    WorkflowRecommendation,
)

from cognitiveplane.shared.dto_decision.assessment import (  # noqa: F401
    ConsensusRecommendation,
    RiskAssessment,
    RootCauseHypotheses,
)

from cognitiveplane.shared.dto_decision.recommendation import (  # noqa: F401
    RDAStrategy,
    RDAStrategyRecommendation,
    RoutingRecommendation,
    VDAStrategy,
    VDAStrategyRecommendation,
    OptimizationRecommendation,
)

from cognitiveplane.shared.dto_decision.directive import (  # noqa: F401
    EscalationRecommendation,
    SkipRecommendation,
)

from cognitiveplane.shared.dto_decision.decision import (  # noqa: F401
    BrainDecision,
    DecisionOutput,
)

# ---------------------------------------------------------------------------
# DecisionOutputContent discriminated union
# ---------------------------------------------------------------------------

DecisionOutputContent = Annotated[
    Union[
        WorkflowRecommendation,
        InspectionStrategyRecommendation,
        ParameterRecommendation,
        ROIRecommendation,
        MarginalRangeRecommendation,
        ParameterAdjustmentRecommendation,
        RiskAssessment,
        ConsensusRecommendation,
        SkipRecommendation,
        EscalationRecommendation,
        RDAStrategyRecommendation,
        VDAStrategyRecommendation,
        RootCauseHypotheses,
        RoutingRecommendation,
        OptimizationRecommendation,
    ],
    Field(discriminator="type"),
]

# Patch DecisionOutput.content to use the union now that it is defined
DecisionOutput.model_rebuild()
