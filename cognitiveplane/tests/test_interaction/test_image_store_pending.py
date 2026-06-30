"""Tests for ImageStore PENDING:{session_id}:{index} format + AnalyzeImageTool image_ref.

Plan §2.3 line 345-348:
  - 工具层引用格式: {"image_ref": "PENDING:session_id:0"}
  - SessionStore 解析 PENDING:xxx → 取回真正的图片数据

Covers B3 (2026-06-24): image_id UUID → PENDING:{session_id}:{index} 对齐方案.
"""

from __future__ import annotations

import pytest

from cognitiveplane.control.tools.analyze_image import AnalyzeImageTool
from cognitiveplane.interaction.image_store import ImageStore, PENDING_PREFIX


# Minimal 1x1 PNG (transparent) for thumbnail generation tests
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db4000000004945e441ae426082"
)


class TestPENDINGFormat:
    """ImageStore generates PENDING:{session_id}:{index} per plan §2.3."""

    def test_single_image_gets_index_0(self) -> None:
        store = ImageStore()
        stored = store.store(_PNG_BYTES, "image/png", session_id="s001")
        assert stored.image_id == "PENDING:s001:0"
        assert stored.session_id == "s001"
        assert stored.index == 0

    def test_multiple_images_increment_per_session(self) -> None:
        store = ImageStore()
        s1 = store.store(_PNG_BYTES, "image/png", session_id="s001")
        s2 = store.store(_PNG_BYTES, "image/png", session_id="s001")
        s3 = store.store(_PNG_BYTES, "image/png", session_id="s001")
        assert s1.image_id == "PENDING:s001:0"
        assert s2.image_id == "PENDING:s001:1"
        assert s3.image_id == "PENDING:s001:2"

    def test_different_sessions_independent_counters(self) -> None:
        store = ImageStore()
        a1 = store.store(_PNG_BYTES, "image/png", session_id="sA")
        b1 = store.store(_PNG_BYTES, "image/png", session_id="sB")
        a2 = store.store(_PNG_BYTES, "image/png", session_id="sA")
        assert a1.image_id == "PENDING:sA:0"
        assert b1.image_id == "PENDING:sB:0"
        assert a2.image_id == "PENDING:sA:1"

    def test_default_session_when_not_specified(self) -> None:
        store = ImageStore()
        stored = store.store(_PNG_BYTES, "image/png")
        assert stored.image_id == "PENDING:default:0"

    def test_get_original_resolves_pending_ref(self) -> None:
        """plan §2.3 line 348: SessionStore 解析 PENDING:xxx → 取回真正的图片数据."""
        store = ImageStore()
        stored = store.store(_PNG_BYTES, "image/png", session_id="s001")
        result = store.get_original(stored.image_id)
        assert result is not None
        bytes_out, mime = result
        assert bytes_out == _PNG_BYTES
        assert mime == "image/png"

    def test_get_original_returns_none_for_unknown_ref(self) -> None:
        store = ImageStore()
        assert store.get_original("PENDING:unknown:99") is None


class TestAnalyzeImageToolImageRef:
    """AnalyzeImageTool accepts image_ref (PENDING format) per plan §2.3."""

    @pytest.mark.asyncio
    async def test_missing_image_ref_returns_error(self) -> None:
        tool = AnalyzeImageTool(llm_provider=object(), image_store=ImageStore())
        result = await tool.execute(question="test")
        assert result.error is not None
        assert "image_ref" in result.error

    @pytest.mark.asyncio
    async def test_unknown_image_ref_returns_invalid_image_error(self) -> None:
        class _FakeVisionLLM:
            def vision_complete(self, *a, **kw): ...  # noqa: E704 — existence check only

        tool = AnalyzeImageTool(llm_provider=_FakeVisionLLM(), image_store=ImageStore())
        result = await tool.execute(image_ref="PENDING:ghost:0")
        assert result.error is not None
        assert result.error_type == "invalid_image"

    @pytest.mark.asyncio
    async def test_parameters_schema_uses_image_ref(self) -> None:
        tool = AnalyzeImageTool()
        schema = tool.parameters_schema
        assert "image_ref" in schema["properties"]
        assert "image_id" not in schema["properties"]
        assert schema["required"] == ["image_ref"]