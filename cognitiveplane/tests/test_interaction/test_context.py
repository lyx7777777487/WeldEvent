"""Tests for ActiveContext, StreamingContext, ContextResolver."""

from uuid import uuid4

from cognitiveplane.interaction.context import (
    ActiveContext,
    ContextResolver,
    StreamingContext,
    StrategySnapshot,
)
from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.enums import InstructionType
from cognitiveplane.shared.types import CaseId, InstructionId, SessionId


class TestActiveContext:
    def test_create(self):
        ctx = ActiveContext(operator_id="zhangsan")
        assert ctx.operator_id == "zhangsan"
        assert ctx.case_id is None

    def test_update_case(self):
        ctx = ActiveContext(operator_id="zhangsan")
        ctx.update_case(CaseId(value="CASE-001"))
        assert ctx.case_id == CaseId(value="CASE-001")

    def test_update_session(self):
        ctx = ActiveContext(operator_id="zhangsan")
        sid = SessionId(value=uuid4())
        ctx.update_session("knowledge_query", sid)
        assert ctx.active_session_type == "knowledge_query"


class TestStreamingContext:
    def test_create_with_instructions(self):
        ctx = StreamingContext(
            case_id=CaseId(value="CASE-001"),
            current_image_index=5,
            total_images=20,
            active_instructions=[
                LiveInstruction(
                    instruction_id=InstructionId(value=uuid4()),
                    instruction_type=InstructionType.SKIP_IMAGE,
                    target_image_index=6,
                    reason="blur",
                )
            ],
        )
        assert len(ctx.active_instructions) == 1
        assert ctx.current_image_index == 5


class TestContextResolver:
    def test_resolve_creates_new(self):
        resolver = ContextResolver()
        ctx = resolver.resolve("zhangsan")
        assert ctx.operator_id == "zhangsan"

    def test_resolve_returns_existing(self):
        resolver = ContextResolver()
        ctx1 = resolver.resolve("zhangsan")
        ctx1.update_case(CaseId(value="CASE-001"))
        ctx2 = resolver.resolve("zhangsan")
        assert ctx2.case_id == CaseId(value="CASE-001")

    def test_isolation(self):
        resolver = ContextResolver()
        resolver.resolve("zhangsan")
        assert resolver.is_isolated("zhangsan")
        assert not resolver.is_isolated("lisi")
