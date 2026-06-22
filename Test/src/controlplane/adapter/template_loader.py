from dataclasses import asdict
from datetime import timedelta
from enum import Enum

from temporalio import activity

from controlplane.domain.template import WorkflowTemplate


def _make_json_safe(obj):
    """Recursively convert timedelta and Enum to JSON-serializable types."""
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_safe(v) for v in obj]
    return obj


def create_load_template_activity(repository=None):
    @activity.defn(name="load_template")
    async def load_template(template_ref: dict) -> dict:
        if repository:
            template = await repository.load(
                template_ref["template_id"],
                template_ref["template_version"],
            )
        else:
            template = None

        if template is None:
            raise ValueError(
                f"Template not found: {template_ref['template_id']}@{template_ref['template_version']}"
            )
        return _make_json_safe(asdict(template))

    return load_template
