"""Tests for database adapter stubs — workflow_repo and audit_repo."""

from datetime import datetime, timezone

import pytest

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine, DatabaseConfig
from cognitiveplane.adapters.database.workflow_repo import PostgresWorkflowStateRepository
from cognitiveplane.adapters.database.audit_repo import PostgresAuditRepository
from cognitiveplane.shared.dto_gateway import WorkflowState, AuditEntry
from cognitiveplane.shared.types import CaseId


def _make_engine() -> AsyncDatabaseEngine:
    return AsyncDatabaseEngine(DatabaseConfig())


def _make_workflow_state(case_id: str = "case-001") -> WorkflowState:
    return WorkflowState(
        case_id=CaseId(value=case_id),
        status="active",
        current_activity="inspection",
        parameters={"current": "180A"},
        updated_at=datetime.now(timezone.utc),
    )


def _make_audit_entry(case_id: str = "case-001", action: str = "decision") -> AuditEntry:
    return AuditEntry(
        entry_id="entry-001",
        case_id=CaseId(value=case_id),
        action=action,
        actor="brain",
        timestamp=datetime.now(timezone.utc),
        details={},
    )


class TestPostgresWorkflowStateRepository:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self):
        repo = PostgresWorkflowStateRepository(_make_engine())
        state = _make_workflow_state()
        await repo.upsert(state)
        result = await repo.get(CaseId(value="case-001"))
        assert result is not None
        assert result.status == "active"
        assert result.parameters["current"] == "180A"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        repo = PostgresWorkflowStateRepository(_make_engine())
        result = await repo.get(CaseId(value="nonexistent"))
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self):
        repo = PostgresWorkflowStateRepository(_make_engine())
        await repo.upsert(_make_workflow_state())
        updated = _make_workflow_state()
        updated.status = "completed"
        await repo.upsert(updated)
        result = await repo.get(CaseId(value="case-001"))
        assert result is not None
        assert result.status == "completed"


class TestPostgresAuditRepository:
    @pytest.mark.asyncio
    async def test_append_and_find_by_case(self):
        repo = PostgresAuditRepository(_make_engine())
        entry = _make_audit_entry()
        await repo.append(entry)
        results = await repo.find_by_case(CaseId(value="case-001"))
        assert len(results) == 1
        assert results[0].action == "decision"

    @pytest.mark.asyncio
    async def test_find_by_case_empty(self):
        repo = PostgresAuditRepository(_make_engine())
        results = await repo.find_by_case(CaseId(value="nonexistent"))
        assert results == []

    @pytest.mark.asyncio
    async def test_find_all(self):
        repo = PostgresAuditRepository(_make_engine())
        await repo.append(_make_audit_entry(case_id="case-001", action="action1"))
        await repo.append(_make_audit_entry(case_id="case-002", action="action2"))
        results = await repo.find_all()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_find_all_limit(self):
        repo = PostgresAuditRepository(_make_engine())
        for i in range(5):
            await repo.append(_make_audit_entry(case_id=f"case-{i}"))
        results = await repo.find_all(limit=3)
        assert len(results) == 3
