from controlplane.domain.activity import ActivityOutput
from controlplane.domain.template import TransitionType, WorkflowTemplate


class TransitionResolver:
    def __init__(self, template: WorkflowTemplate) -> None:
        self._template = template

    def resolve_next(self, from_cp: str, condition: str) -> str | None:
        for transition in self._template.transitions:
            if transition.from_cp != from_cp:
                continue

            if transition.transition_type == TransitionType.PARALLEL:
                raise NotImplementedError(
                    "Parallel transition execution not yet implemented. "
                    "Domain model is ready for future extension."
                )

            for branch in transition.branches:
                if branch.condition == condition:
                    return branch.to_cp

            for branch in transition.branches:
                if branch.condition == "default":
                    return branch.to_cp

        return None

    def result_to_condition(self, result: ActivityOutput | None) -> str:
        if result is None:
            return "default"
        return result.status.value
