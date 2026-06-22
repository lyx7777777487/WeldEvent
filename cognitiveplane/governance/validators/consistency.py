"""ConsistencyValidator — real consistency check for the validation pipeline.

Compares decision against prior memories and WeldMap state:
- Factual consistency: decision outputs vs WeldMap measurements/state
- Historical consistency: decision vs prior approved decisions in memory

INCONSISTENT if severe contradictions found, otherwise CONSISTENT.
"""

from datetime import datetime, timezone

from cognitiveplane.shared.dto_validation import InconsistencyDetail
from cognitiveplane.shared.ports.validation import (
    ConsistencyValidatorInput,
    ConsistencyValidatorOutput,
    ConsistencyValidatorPort,
)
from cognitiveplane.shared.enums import ConsistencyStatus

_SEVERITY_THRESHOLD = 0.7


class ConsistencyValidator(ConsistencyValidatorPort):
    async def check(
        self, input_data: ConsistencyValidatorInput
    ) -> ConsistencyValidatorOutput:
        decision = input_data.decision
        prior_memories = input_data.prior_memories
        weldmap_state = input_data.weldmap_state

        inconsistencies: list[InconsistencyDetail] = []

        # Factual consistency: check if decision case_data aligns with WeldMap
        if weldmap_state and weldmap_state.workflow_state:
            for output in decision.outputs:
                params = getattr(output.content, "parameters", None)
                if params and hasattr(params, "parameters"):
                    for key, value in params.parameters.items():
                        weldmap_val = weldmap_state.workflow_state.get(key)
                        if weldmap_val is not None and str(value) != str(weldmap_val):
                            inconsistencies.append(
                                InconsistencyDetail(
                                    check_type="factual",
                                    description=f"Parameter '{key}' decision={value} vs weldmap={weldmap_val}",
                                    severity=0.5,
                                    reference="weldmap_state",
                                )
                            )

        factual = (
            ConsistencyStatus.INCONSISTENT
            if any(i.check_type == "factual" and i.severity >= _SEVERITY_THRESHOLD for i in inconsistencies)
            else ConsistencyStatus.CONSISTENT
        )

        # Historical consistency: compare against prior approved decisions
        if prior_memories:
            for mem in prior_memories[:5]:
                mem_score = getattr(mem, "similarity_score", 0.0)
                if mem_score > 0.8 and decision.confidence < 0.3:
                    inconsistencies.append(
                        InconsistencyDetail(
                            check_type="historical",
                            description=f"Low confidence ({decision.confidence:.2f}) contradicts high-similarity historical memory ({mem_score:.2f})",
                            severity=0.8,
                            reference="prior_memory",
                        )
                    )
                    break

        historical = (
            ConsistencyStatus.INCONSISTENT
            if any(i.check_type == "historical" for i in inconsistencies)
            else ConsistencyStatus.CONSISTENT
        )

        overall = ConsistencyStatus.INCONSISTENT if inconsistencies else ConsistencyStatus.CONSISTENT

        return ConsistencyValidatorOutput(
            result=overall,
            factual_consistency=factual,
            historical_consistency=historical,
            inconsistencies=inconsistencies,
            timestamp=datetime.now(timezone.utc),
        )


# Keep stub as alias for backward compat
StubConsistencyValidator = ConsistencyValidator
