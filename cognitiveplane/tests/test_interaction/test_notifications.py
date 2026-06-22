"""Tests for notifications API router."""

import pytest
from fastapi.testclient import TestClient

from cognitiveplane.interaction.api.notifications import create_notifications_router
from cognitiveplane.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestNotificationsAPI:
    def test_get_notifications(self, client: TestClient):
        response = client.get("/api/v1/notifications/?operator_id=op-001")
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert data["operator_id"] == "op-001"

    def test_get_notifications_default_operator(self, client: TestClient):
        response = client.get("/api/v1/notifications/")
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
