import asyncio
from datetime import timedelta

import pytest
import temporalio.worker
from temporalio import activity
from temporalio.testing import WorkflowEnvironment

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
from controlplane.domain.human_gate import GateAction
from controlplane.domain.policy import ExecutionPolicy, RetryPolicy
from controlplane.domain.template import (
    ActivityBinding, BranchDefinition, ControlPointDefinition,
    TransitionDefinition, WorkflowTemplate,
)
from controlplane.runtime.template_workflow import TemplateWorkflow

TEMPLATES: dict[str, WorkflowTemplate] = {}


def _make_json_safe(obj):
    """Recursively convert non-JSON-serializable objects (timedelta, Enum) for Temporal serialization."""
    from enum import Enum
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_safe(v) for v in obj]
    return obj


def _activity_output_to_dict(output: ActivityOutput) -> dict:
    return {
        "status": output.status.value,
        "data": output.data,
        "error": output.error,
    }


@activity.defn(name="load_template")
async def load_template(template_ref: dict) -> dict:
    key = f"{template_ref['template_id']}@{template_ref['template_version']}"
    template = TEMPLATES.get(key)
    if template is None:
        raise ValueError(f"Template not found: {key}")
    from dataclasses import asdict
    return _make_json_safe(asdict(template))

@activity.defn(name="iqa_activity")
async def iqa_activity(input: ActivityInput) -> dict:
    return _activity_output_to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "iqa"}))

@activity.defn(name="vda_activity_ok")
async def vda_activity_ok(input: ActivityInput) -> dict:
    return _activity_output_to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "vda_ok"}))

@activity.defn(name="vda_activity_ng")
async def vda_activity_ng(input: ActivityInput) -> dict:
    return _activity_output_to_dict(ActivityOutput(status=ActivityStatus.NG, data={"mock": "vda_ng"}))

@activity.defn(name="error_activity")
async def error_activity(input: ActivityInput) -> dict:
    return _activity_output_to_dict(ActivityOutput(status=ActivityStatus.ERROR, error="simulated failure"))


@pytest.fixture(autouse=True)
def clear_templates():
    TEMPLATES.clear()
    yield
    TEMPLATES.clear()


@pytest.mark.asyncio
async def test_simple_sequential_workflow():
    template = WorkflowTemplate(
        id="simple", version="1.0",
        control_points=[
            ControlPointDefinition(id="CP0", name="IQA", activity_binding=ActivityBinding(activity_name="iqa_activity"), execution_policy=ExecutionPolicy()),
            ControlPointDefinition(id="CP1", name="Final", activity_binding=None, execution_policy=ExecutionPolicy()),
        ],
        transitions=[
            TransitionDefinition(from_cp="CP0", branches=[BranchDefinition(condition="OK", to_cp="CP1")]),
            TransitionDefinition(from_cp="CP1", branches=[BranchDefinition(condition="default", to_cp=None)]),
        ],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, iqa_activity])
        async with w:
            result = await client.execute_workflow(TemplateWorkflow, arg={"template_id": "simple", "template_version": "1.0"}, id="test-simple", task_queue="test-queue")
            assert result["status"] == "COMPLETED"

@pytest.mark.asyncio
async def test_branching_workflow_ok():
    template = WorkflowTemplate(
        id="branching-ok", version="1.0",
        control_points=[
            ControlPointDefinition(id="CP0", name="VDA", activity_binding=ActivityBinding(activity_name="vda_activity_ok"), execution_policy=ExecutionPolicy()),
            ControlPointDefinition(id="CP1_OK", name="OK", activity_binding=None, execution_policy=ExecutionPolicy()),
            ControlPointDefinition(id="CP1_NG", name="NG", activity_binding=None, execution_policy=ExecutionPolicy()),
        ],
        transitions=[TransitionDefinition(from_cp="CP0", branches=[BranchDefinition(condition="OK", to_cp="CP1_OK"), BranchDefinition(condition="NG", to_cp="CP1_NG")])],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, vda_activity_ok])
        async with w:
            result = await client.execute_workflow(TemplateWorkflow, arg={"template_id": "branching-ok", "template_version": "1.0"}, id="test-branching-ok", task_queue="test-queue")
            assert result["status"] == "COMPLETED"
            assert any(t["to"] == "CP1_OK" for t in result["transitions"])

@pytest.mark.asyncio
async def test_branching_workflow_ng():
    template = WorkflowTemplate(
        id="branching-ng", version="1.0",
        control_points=[
            ControlPointDefinition(id="CP0", name="VDA", activity_binding=ActivityBinding(activity_name="vda_activity_ng"), execution_policy=ExecutionPolicy()),
            ControlPointDefinition(id="CP1_NG", name="NG", activity_binding=None, execution_policy=ExecutionPolicy()),
        ],
        transitions=[TransitionDefinition(from_cp="CP0", branches=[BranchDefinition(condition="OK", to_cp="CP1_OK"), BranchDefinition(condition="NG", to_cp="CP1_NG")])],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, vda_activity_ng])
        async with w:
            result = await client.execute_workflow(TemplateWorkflow, arg={"template_id": "branching-ng", "template_version": "1.0"}, id="test-branching-ng", task_queue="test-queue")
            assert result["status"] == "COMPLETED"
            assert any(t["to"] == "CP1_NG" for t in result["transitions"])

@pytest.mark.asyncio
async def test_error_activity_fails_workflow():
    template = WorkflowTemplate(
        id="error-test", version="1.0",
        control_points=[
            ControlPointDefinition(id="CP0", name="Fail", activity_binding=ActivityBinding(activity_name="error_activity"), execution_policy=ExecutionPolicy(retry_policy=RetryPolicy(max_attempts=1))),
        ],
        transitions=[],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, error_activity])
        async with w:
            result = await client.execute_workflow(TemplateWorkflow, arg={"template_id": "error-test", "template_version": "1.0"}, id="test-error", task_queue="test-queue")
            assert result["status"] == "FAILED"

@pytest.mark.asyncio
async def test_human_gate_continue():
    """Test Human Gate with CONTINUE action: workflow pauses, receives update, resumes."""
    from controlplane.domain.human_gate import HumanGateDefinition

    template = WorkflowTemplate(
        id="gate-continue", version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0", name="Review",
                activity_binding=ActivityBinding(activity_name="iqa_activity"),
                execution_policy=ExecutionPolicy(),
                human_gate=HumanGateDefinition(),
            ),
            ControlPointDefinition(id="CP1", name="Done", activity_binding=None, execution_policy=ExecutionPolicy()),
        ],
        transitions=[
            TransitionDefinition(from_cp="CP0", branches=[BranchDefinition(condition="OK", to_cp="CP1")]),
            TransitionDefinition(from_cp="CP1", branches=[BranchDefinition(condition="default", to_cp=None)]),
        ],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, iqa_activity])
        async with w:
            handle = await client.start_workflow(TemplateWorkflow, arg={"template_id": "gate-continue", "template_version": "1.0"}, id="test-gate-continue", task_queue="test-queue")

            # Wait for the workflow to reach the gate
            await asyncio.sleep(0.5)

            # Submit gate action to continue
            await handle.execute_update("submit_gate_action", GateAction.CONTINUE)

            result = await handle.result()
            assert result["status"] == "COMPLETED"

@pytest.mark.asyncio
async def test_human_gate_terminate():
    """Test Human Gate with TERMINATE action: workflow ends with TERMINATED status."""
    from controlplane.domain.human_gate import HumanGateDefinition

    template = WorkflowTemplate(
        id="gate-terminate", version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0", name="Review",
                activity_binding=ActivityBinding(activity_name="iqa_activity"),
                execution_policy=ExecutionPolicy(),
                human_gate=HumanGateDefinition(),
            ),
        ],
        transitions=[
            TransitionDefinition(from_cp="CP0", branches=[BranchDefinition(condition="OK", to_cp=None)]),
        ],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, iqa_activity])
        async with w:
            handle = await client.start_workflow(TemplateWorkflow, arg={"template_id": "gate-terminate", "template_version": "1.0"}, id="test-gate-terminate", task_queue="test-queue")

            await asyncio.sleep(0.5)
            await handle.execute_update("submit_gate_action", GateAction.TERMINATE)

            result = await handle.result()
            assert result["status"] == "TERMINATED"

@pytest.mark.asyncio
async def test_human_gate_redirect():
    """Test Human Gate with REDIRECT action: workflow jumps to redirect target CP."""
    from controlplane.domain.human_gate import HumanGateDefinition

    template = WorkflowTemplate(
        id="gate-redirect", version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0", name="Review",
                activity_binding=ActivityBinding(activity_name="iqa_activity"),
                execution_policy=ExecutionPolicy(),
                human_gate=HumanGateDefinition(redirect_target="CP2"),
            ),
            ControlPointDefinition(id="CP1", name="Skip", activity_binding=None, execution_policy=ExecutionPolicy()),
            ControlPointDefinition(id="CP2", name="Target", activity_binding=None, execution_policy=ExecutionPolicy()),
        ],
        transitions=[
            TransitionDefinition(from_cp="CP0", branches=[BranchDefinition(condition="OK", to_cp="CP1")]),
            TransitionDefinition(from_cp="CP1", branches=[BranchDefinition(condition="default", to_cp=None)]),
            TransitionDefinition(from_cp="CP2", branches=[BranchDefinition(condition="default", to_cp=None)]),
        ],
        entry_point="CP0",
    )
    TEMPLATES[f"{template.id}@{template.version}"] = template
    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        w = temporalio.worker.Worker(client, task_queue="test-queue", workflows=[TemplateWorkflow], activities=[load_template, iqa_activity])
        async with w:
            handle = await client.start_workflow(TemplateWorkflow, arg={"template_id": "gate-redirect", "template_version": "1.0"}, id="test-gate-redirect", task_queue="test-queue")

            await asyncio.sleep(0.5)
            await handle.execute_update("submit_gate_action", GateAction.REDIRECT)

            result = await handle.result()
            assert result["status"] == "COMPLETED"
            # Should have REDIRECT transition to CP2, not OK transition to CP1
            assert any(t["condition"] == "REDIRECT" and t["to"] == "CP2" for t in result["transitions"])
