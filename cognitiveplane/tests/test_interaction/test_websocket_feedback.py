"""E2E test — WebSocket 双向 + feedback + interrupt.

Spec §11.4 验收 3.1 + 3.2 + 3.5 (认知平面侧).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from cognitiveplane.app import create_app
from cognitiveplane.capability import get_llm as _original_get_llm
import cognitiveplane.capability as capability_module
from cognitiveplane.capability.mock import MockLLMProvider


@pytest.fixture
def app_with_mock_llm(monkeypatch):
    """构造带 MockLLMProvider 的 app — 不依赖真实 DeepSeek/Volc."""
    monkeypatch.setattr(
        capability_module, "get_llm",
        lambda: MockLLMProvider(default_response="测试答复")
    )
    # _bootstrap_llm 会探测真实 API, 直接跳过
    monkeypatch.setattr("cognitiveplane.app._bootstrap_llm", lambda: True)
    app = create_app()
    return app


def test_websocket_chat_streams_events(app_with_mock_llm):
    """3.1 server→client — chat 消息收到 final 事件."""
    client = TestClient(app_with_mock_llm)
    with client.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({
            "type": "chat",
            "message": "你好",
            "session_id": "sess-e2e",
        })
        events: list[dict] = []
        for _ in range(20):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "final":
                break
        assert any(e.get("type") == "final" for e in events), f"no final in {events}"


def test_websocket_feedback_writes_memory(app_with_mock_llm):
    """3.5 feedback → feedback_received 回执."""
    client = TestClient(app_with_mock_llm)
    with client.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({
            "type": "feedback",
            "correction": "缺陷位置标错了",
            "session_id": "sess-e2e",
            "category": "wrong_result",
        })
        events: list[dict] = []
        for _ in range(10):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "feedback_received":
                break
        acks = [e for e in events if e.get("type") == "feedback_received"]
        assert len(acks) == 1, f"expected feedback_received, got {events}"
        assert "memory_id" in acks[0]


def test_websocket_interrupt_cancels_react(app_with_mock_llm, monkeypatch):
    """3.1 interrupt → interrupted 回执."""
    class SlowLLM(MockLLMProvider):
        @property
        def supports_function_calling(self) -> bool:
            return True  # Force Tier-1 so complete() is called
        async def complete(self, request):
            await asyncio.sleep(2.0)
            return await super().complete(request)

    monkeypatch.setattr(capability_module, "get_llm", lambda: SlowLLM())
    monkeypatch.setattr("cognitiveplane.app._bootstrap_llm", lambda: True)
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({"type": "chat", "message": "你好", "session_id": "sess-e2e"})
        import time
        time.sleep(0.2)
        ws.send_json({"type": "interrupt", "session_id": "sess-e2e", "reason": "停"})

        events: list[dict] = []
        for _ in range(20):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "interrupted":
                break
        interrupted = [e for e in events if e.get("type") == "interrupted"]
        assert len(interrupted) == 1, f"expected interrupted, got {events}"
