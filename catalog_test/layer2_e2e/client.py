"""L2 API 客户端 - 异步 HTTP 对话 WeldEvent 系统.

封装全部 API 端点:
  POST /api/v1/chat/             - 纯文本聊天
  POST /api/v1/chat/upload       - 上传图片 + 聊天
  POST /api/v1/chat/approve      - 确认/拒绝 approval 卡片
  POST /api/v1/chat/cancel       - 取消 workflow
  POST /api/v1/chat/workflow/status - 查 workflow 状态
  GET  /api/v1/chat/workflow/stream/{session_id} - SSE 事件流
  GET  /api/v1/chat/sessions     - 列出 session
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


@dataclass
class ChatTurn:
    """一轮对话的完整记录."""
    role: str                      # "user" | "assistant" | "system"
    content: str                   # 消息内容
    tools_used: list[str] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)
    approval_popup: dict | None = None   # 弹窗信息
    user_choice: str | None = None       # 用户选择
    timestamp: float = 0.0
    error: str | None = None


class WeldEventClient:
    """WeldEvent API 异步客户端."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000",
                 image_path: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        # 自动查找 test.jpg (根目录 / catalog_test / 环境变量)
        if image_path:
            self.image_path = image_path
        elif os.environ.get("WELDEVENT_TEST_IMAGE"):
            self.image_path = os.environ["WELDEVENT_TEST_IMAGE"]
        else:
            # 按优先级查找
            candidates = [
                "/Users/liuyixuan/WeldEvent/test.jpg",
                os.path.join(os.path.dirname(__file__), "..", "test.jpg"),
                os.path.join(os.path.dirname(__file__), "..", "..", "test.jpg"),
            ]
            self.image_path = next(
                (p for p in candidates if os.path.exists(p)),
                candidates[0],  # fallback (可能不存在, upload 时报错)
            )
        self.session_id: str | None = None
        self._client = None

    async def __aenter__(self):
        if httpx is None:
            raise RuntimeError("httpx not installed")
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=120.0, trust_env=False
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def health(self) -> bool:
        """检查 API 是否可达."""
        if not self._client:
            return False
        try:
            r = await self._client.get("/api/v1/health")
            return r.status_code == 200
        except Exception:
            return False

    async def chat(self, message: str, operator_id: str = "tester-001",
                   case_id: str | None = None) -> ChatTurn:
        """纯文本对话 (无图片)."""
        payload: dict[str, Any] = {
            "message": message, "operator_id": operator_id,
        }
        if self.session_id:
            payload["session_id"] = self.session_id
        if case_id:
            payload["case_id"] = case_id
        r = await self._client.post("/api/v1/chat/", json=payload)
        data = r.json()
        self.session_id = data.get("session_id", self.session_id)
        return ChatTurn(
            role="user", content=message, timestamp=asyncio.get_event_loop().time(),
        ), ChatTurn(
            role="assistant", content=data.get("reply", ""),
            tools_used=data.get("tools_used", []),
            workflow_ids=data.get("workflow_ids", []),
            error=data.get("error"),
            timestamp=asyncio.get_event_loop().time(),
        )

    async def upload_and_chat(self, message: str, image_path: str | None = None,
                              operator_id: str = "tester-001") -> tuple[ChatTurn, ChatTurn]:
        """上传图片 + 对话."""
        img = image_path or self.image_path
        if not Path(img).exists():
            raise FileNotFoundError(f"测试图片不存在: {img}")
        with open(img, "rb") as f:
            files = {"files": ("test.jpg", f, "image/jpeg")}
            data = {"message": message, "operator_id": operator_id}
            if self.session_id:
                data["session_id"] = self.session_id
            r = await self._client.post(
                "/api/v1/chat/upload", files=files, data=data
            )
        resp = r.json()
        self.session_id = resp.get("session_id", self.session_id)
        user_turn = ChatTurn(role="user", content=f"[上传图片 {Path(img).name}] {message}")
        assistant_turn = ChatTurn(
            role="assistant", content=resp.get("reply", ""),
            tools_used=resp.get("tools_used", []),
            workflow_ids=resp.get("workflow_ids", []),
            error=resp.get("error"),
        )
        return user_turn, assistant_turn

    async def approve(self, approval_id: str, decision: str = "approved",
                      feedback: str | None = None) -> dict:
        """确认/拒绝 approval 卡片."""
        payload = {"approval_id": approval_id, "decision": decision}
        if feedback:
            payload["feedback"] = feedback
        r = await self._client.post("/api/v1/chat/approve", json=payload)
        return r.json()

    async def cancel_workflow(self, workflow_id: str) -> dict:
        """取消 workflow."""
        r = await self._client.post(
            "/api/v1/chat/cancel", json={"workflow_id": workflow_id}
        )
        return r.json()

    async def query_workflow_status(self, workflow_id: str) -> dict:
        """查 workflow 状态."""
        r = await self._client.post(
            "/api/v1/chat/workflow/status", json={"workflow_id": workflow_id}
        )
        return r.json()

    async def list_sessions(self) -> list[dict]:
        """列出所有 session."""
        r = await self._client.get("/api/v1/chat/sessions")
        return r.json()

    async def get_trajectory(self, session_id: str) -> dict:
        """导出 session 轨迹."""
        r = await self._client.get(
            f"/api/v1/chat/sessions/{session_id}/trajectory"
        )
        return r.json()
