"""FastAPI chat endpoint tests (spec §7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cognitiveplane.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_health_endpoint(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.2.0"


def test_chat_endpoint_returns_response(client):
    resp = client.post("/api/v1/chat/", json={
        "message": "查询预热温度",
        "operator_id": "test-op",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "reply" in data
    assert "session_id" in data
    assert "tier" in data


def test_chat_endpoint_with_case_id(client):
    resp = client.post("/api/v1/chat/", json={
        "message": "查看进度",
        "operator_id": "test-op",
        "case_id": "CASE-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
