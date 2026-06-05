"""Mode C: Execution Monitor — stateless, read-only progress monitoring.

Source: L1-Interaction-Layer-Business-Requirements.md §3.4.
"""

from uuid import uuid4

from src.interaction.base import (
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserMessage,
)
from src.interaction.dependencies import ModeDependencies
from src.shared.enums import MatchStrategy
from src.shared.types import CaseId, SessionId


class ExecutionMonitorMode(ModeProtocol):
    """Stateless execution monitor — read-only progress reporting.

    Reads WeldMap snapshot via GatewayReadPort and formats a human-readable
    progress report. Never writes, never triggers validation.
    """

    mode_id = "cognitive.execution_monitor"
    display_name = "Execution Monitor"
    description = "Monitor production line progress, check current image status and workflow state"

    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["进度", "状态", "怎样", "走到哪", "完成多少", "当前", "进展"],
            priority=50,
            confidence_threshold=0.3,
        ),
    ]

    required_ports = ["GatewayReadPort"]
    creates_session = False
    session_data_schema = None

    async def handle(
        self, message: UserMessage, deps: ModeDependencies
    ) -> ModeResponse:
        """Read workflow state and return progress report."""
        gateway = deps.get("GatewayReadPort")

        # Resolve case_id from message or context
        case_id = message.case_id
        if case_id is None:
            return ModeResponse(
                mode_id=self.mode_id,
                session_id=SessionId(value=uuid4()),
                response_type=ResponseType.CLARIFICATION_REQUEST,
                text_reply="请问您想查看哪个案例的进度？请提供案例 ID。",
                follow_up_suggestions=["CASE-001", "CASE-002"],
            )

        # Read workflow state
        workflow = await gateway.read_workflow_state(case_id)
        case = await gateway.read_case(case_id)

        # Format progress report
        params_str = ", ".join(f"{k}={v}" for k, v in workflow.parameters.items()) if workflow.parameters else "无"
        activity = workflow.current_activity if workflow.current_activity else "空闲"

        reply = (
            f"CASE-{case_id.value} 进度报告:\n"
            f"  状态: {workflow.status}\n"
            f"  当前活动: {activity}\n"
            f"  参数: {params_str}\n"
            f"  最后更新: {workflow.updated_at}\n"
            f"  案例: {case.case_type} | 创建于 {case.creation_date}"
        )

        return ModeResponse(
            mode_id=self.mode_id,
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply=reply,
            ports_accessed=["GatewayReadPort"],
        )
