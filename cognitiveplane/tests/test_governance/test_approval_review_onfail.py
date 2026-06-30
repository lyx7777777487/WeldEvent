"""Tests for governance/{on_fail,review,approval}.py."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.governance.approval import (
    ApprovalRequest,
    ApprovalService,
)
from cognitiveplane.governance.collaboration.repositories.in_memory import (
    InMemoryHumanReviewRequestRepository,
)
from cognitiveplane.governance.on_fail import (
    OnFailAction,
    default_action_for_severity,
)
from cognitiveplane.governance.review import (
    HumanReviewWorkflow,
    ReviewNotFoundError,
)
from cognitiveplane.shared.dto.collaboration import (
    ResolutionContent,
    ReviewContent,
)
from cognitiveplane.shared.enums import (
    CollaborationLayer,
    ReviewStatus,
    ReviewType,
    UrgencyLevel,
)
from cognitiveplane.shared.types import DecisionId, ReviewRequestId


def _content() -> ReviewContent:
    return ReviewContent(
        decision_id=DecisionId(value=uuid4()),
        summary="Decision summary",
        reasoning_summary="Reasoning summary",
        risk_summary="Low risk",
        recommendation="Approve",
        confidence=0.85,
    )


def _resolution(text: str = "ok") -> ResolutionContent:
    return ResolutionContent(resolution=text, conditions=[])


class TestOnFailAction:
    def test_severity_mapping(self):
        assert default_action_for_severity(0.95) == OnFailAction.ESCALATE
        assert default_action_for_severity(0.7) == OnFailAction.REFRAIN
        assert default_action_for_severity(0.5) == OnFailAction.FIX
        assert default_action_for_severity(0.1) == OnFailAction.REASK

    def test_enum_values(self):
        assert OnFailAction.REASK.value == "reask"
        assert OnFailAction.ESCALATE.value == "escalate"


class TestHumanReviewWorkflow:
    @pytest.mark.asyncio
    async def test_create_and_find_pending(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        content = _content()
        decision_id = content.decision_id
        req = await wf.create_review(
            decision_id=decision_id,
            layer=CollaborationLayer.L1,
            review_type=ReviewType.APPROVAL,
            content=content,
        )
        assert req.status == ReviewStatus.PENDING
        pending = await wf.find_pending(CollaborationLayer.L1)
        assert any(p.request_id.value == req.request_id.value for p in pending)

    @pytest.mark.asyncio
    async def test_approve_transitions(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        req = await wf.create_review(
            decision_id=DecisionId(value=uuid4()),
            layer=CollaborationLayer.L1,
            review_type=ReviewType.APPROVAL,
            content=_content(),
        )
        await wf.mark_in_progress(req.request_id, "alice")
        approved = await wf.approve(
            req.request_id, "alice", _resolution("looks good")
        )
        assert approved.status == ReviewStatus.APPROVED
        assert approved.reviewer == "alice"
        assert approved.resolution is not None
        assert approved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_deny(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        req = await wf.create_review(
            decision_id=DecisionId(value=uuid4()),
            layer=CollaborationLayer.L2,
            review_type=ReviewType.APPROVAL,
            content=_content(),
        )
        denied = await wf.deny(req.request_id, "bob", _resolution("nope"))
        assert denied.status == ReviewStatus.DENIED

    @pytest.mark.asyncio
    async def test_missing_raises(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        with pytest.raises(ReviewNotFoundError):
            await wf.get(ReviewRequestId(value=uuid4()))


class TestApprovalService:
    @pytest.mark.asyncio
    async def test_routine_returns_async_pending(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        svc = ApprovalService(wf)
        result = await svc.request_approval(
            ApprovalRequest(
                decision_id=DecisionId(value=uuid4()),
                urgency=UrgencyLevel.ROUTINE,
                layer=CollaborationLayer.L1,
                review_type=ReviewType.APPROVAL,
                content=_content(),
            )
        )
        assert result.is_sync is False
        assert result.status == ReviewStatus.PENDING

    @pytest.mark.asyncio
    async def test_critical_blocks_until_approved(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        svc = ApprovalService(wf)

        async def approve_after_delay(svc: ApprovalService) -> None:
            # Wait briefly, then find the pending review and approve it
            await asyncio.sleep(0.05)
            pending = await svc._workflow.find_pending(CollaborationLayer.L2)
            assert pending, "expected at least one pending review"
            await svc._workflow.approve(
                pending[0].request_id, "alice", _resolution("ok")
            )

        approve_task = asyncio.create_task(approve_after_delay(svc))
        result = await svc.request_approval(
            ApprovalRequest(
                decision_id=DecisionId(value=uuid4()),
                urgency=UrgencyLevel.CRITICAL,
                layer=CollaborationLayer.L2,
                review_type=ReviewType.APPROVAL,
                content=_content(),
                timeout_seconds=2.0,
                poll_interval_seconds=0.02,
            )
        )
        await approve_task
        assert result.is_sync is True
        assert result.status == ReviewStatus.APPROVED

    @pytest.mark.asyncio
    async def test_sync_timeout_marks_expired(self):
        repo = InMemoryHumanReviewRequestRepository()
        wf = HumanReviewWorkflow(repo)
        svc = ApprovalService(wf)
        result = await svc.request_approval(
            ApprovalRequest(
                decision_id=DecisionId(value=uuid4()),
                urgency=UrgencyLevel.URGENT,
                layer=CollaborationLayer.L1,
                review_type=ReviewType.APPROVAL,
                content=_content(),
                timeout_seconds=0.1,
                poll_interval_seconds=0.02,
            )
        )
        assert result.is_sync is True
        assert result.status == ReviewStatus.EXPIRED
