"""Tests for interaction/base.py domain models."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.interaction.base import (
    BaseSessionData,
    ContextRequirement,
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserAction,
    UserMessage,
)
from src.shared.enums import MatchStrategy, SessionStatus
from src.shared.types import CaseId, DecisionId, SessionId


class TestUserMessage:
    def test_create_minimal(self):
        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        assert msg.raw_text == "Q345R预热温度多少"
        assert msg.intent_label == ""
        assert msg.intent_confidence == 0.0
        assert msg.session_id is None

    def test_create_with_context(self):
        msg = UserMessage(
            raw_text="从图4起加强检测",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            intent_label="cognitive.intervention",
            intent_confidence=0.85,
            extracted_entities={"from_image": 4},
            session_id=SessionId(value=uuid4()),
            case_id=CaseId(value="CASE-001"),
        )
        assert msg.intent_label == "cognitive.intervention"
        assert msg.extracted_entities["from_image"] == 4


class TestModeResponse:
    def test_text_reply(self):
        resp = ModeResponse(
            mode_id="cognitive.knowledge_query",
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply="Q345R 22mm 预热温度要求 ≥100°C",
        )
        assert resp.response_type == ResponseType.TEXT_REPLY
        assert resp.structured_output is None
        assert resp.action_required is None

    def test_clarification_request(self):
        resp = ModeResponse(
            mode_id="cognitive.knowledge_query",
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.CLARIFICATION_REQUEST,
            text_reply="您指的是哪种材料的预热温度？",
            follow_up_suggestions=["Q345R", "Q235B"],
        )
        assert resp.follow_up_suggestions == ["Q345R", "Q235B"]


class TestIntentPattern:
    def test_create_keyword_pattern(self):
        pattern = IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["多少", "温度", "参数"],
            priority=50,
            confidence_threshold=0.3,
        )
        assert pattern.match_strategy == MatchStrategy.KEYWORD
        assert pattern.required_context is None

    def test_create_composite_pattern(self):
        ctx_req = ContextRequirement(needs_active_case=True)
        pattern = IntentPattern(
            match_strategy=MatchStrategy.COMPOSITE,
            patterns=["加强", "检测"],
            required_context=ctx_req,
            priority=60,
            confidence_threshold=0.5,
        )
        assert pattern.required_context.needs_active_case is True


class TestBaseSessionData:
    def test_create(self):
        session = BaseSessionData(
            mode_id="cognitive.knowledge_query",
            session_id=SessionId(value=uuid4()),
            operator_id="zhangsan",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert session.status == SessionStatus.ACTIVE
        assert session.case_id is None
        assert session.conversation_turns == 0


class TestModeProtocolAbstract:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            ModeProtocol()
