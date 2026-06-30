"""LaunchWorkflowTool — 提交 WorkflowSpec 到 L2 Temporal。

boundary-pinning §6.2:
  1. Brain 调 design_workflow 产出 WorkflowSpec（草案）
  2. 上层确认后，通过 launch_workflow 提交到 L2 Temporal

本工具接收 design_workflow 产出的 WorkflowSpec，调 EventConnector
提交到 controlplane 的 RunWorkflowSpec workflow。

与 design_workflow 的边界:
  - design_workflow: 只产出草案，不启动 Temporal
  - launch_workflow:  接收草案，提交到 Temporal（真正的 L1→L2 触发点）

P2-5 fix: launch_workflow 是 LLM 可见工具，但触发真实 Temporal workflow。
当前 Phase 2 简化为直接提交（LLM 已通过 design_workflow 产出草案，
用户在 chat 中看到方案后说"启动"才触发）。Phase 4+ 应插入正式的人工
确认门禁（RequestConfirmationTool 或 Temporal HumanGateSignal）。

image_ref 解析 (P4 fix): design_workflow 在每个 tool_task node 的 input
里注入 image_refs（来自用户上传的 PENDING:session_id:index 引用）。
launch_workflow 在提交前把它们解析成磁盘文件路径（image_path），
让 L3 IqaActivity 能通过 preprocessor.DecodeStep 读取真实图像字节。
ImageStore 是内存的，L3 activity 跨进程无法直接访问——必须落盘。

Source: boundary-pinning §6.2
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.interaction.image_store import ImageStore

logger = logging.getLogger(__name__)

# 临时图片目录 — Temporal activity 可能重试，文件需在工作流生命周期内存在
_IMAGE_TMP_DIR = os.path.join(tempfile.gettempdir(), "weldevent_upload_images")
os.makedirs(_IMAGE_TMP_DIR, exist_ok=True)

# P2-3 fix: 临时文件保留时长。Temporal execution_timeout=10min + retry 3 次
# 约 30 分钟覆盖完，2 小时给人工延迟留余量。超过此年龄的文件会被清理。
_IMAGE_FILE_TTL_SECONDS = 2 * 3600


def _cleanup_old_image_files() -> int:
    """P2-3 fix: 扫描 _IMAGE_TMP_DIR，删除超过 TTL 的旧文件。

    幂等：可重复调用。只在 _resolve_image_refs_to_paths 时触发（低频路径），
    避免每次 store 都扫盘。返回删除的文件数。
    """
    now = time.time()
    removed = 0
    try:
        for name in os.listdir(_IMAGE_TMP_DIR):
            path = os.path.join(_IMAGE_TMP_DIR, name)
            try:
                mtime = os.path.getmtime(path)
                if (now - mtime) > _IMAGE_FILE_TTL_SECONDS:
                    os.remove(path)
                    removed += 1
            except OSError:
                # 文件可能被并发删除 — 跳过
                continue
    except OSError:
        pass
    return removed


def _write_bytes_to_file(path: str, data: bytes) -> None:
    """P2-4 fix: 同步写文件的辅助函数，供 asyncio.to_thread 调用。

    分离出来是为了让磁盘 I/O 在线程池执行，不阻塞 asyncio 事件循环。
    """
    with open(path, "wb") as f:
        f.write(data)


class LaunchWorkflowTool(BrainTool):
    """Submit a WorkflowSpec to L2 Temporal Control Plane."""

    phase = 3
    # P2-8 fix: 限流改为按 workflow_id 去重 + 全局上限提高
    # 原 _launch_count 是 app 级全局计数器，10 次后所有用户被锁
    # 改为 _launched_ids set 按 workflow_id 去重（同一 workflow 不重复启动）
    # _MAX_TOTAL_LAUNCHES 是 app 生命周期全局上限（防内存泄漏）
    # P3-2 fix: 改用 OrderedDict 实现 LRU 淘汰，达到上限时移除最老条目
    # （而非硬性拒绝），避免长期运行后新用户无法 launch。
    _MAX_TOTAL_LAUNCHES = 100
    _MAX_LAUNCHES = 10  # 保留向后兼容（测试引用）

    def __init__(self, deps, image_store: "ImageStore | None" = None) -> None:
        self._deps = deps
        self._image_store = image_store
        self._launch_count = 0  # 保留向后兼容
        # P3-2 fix: OrderedDict 按 workflow_id LRU 淘汰
        self._launched_ids: "OrderedDict[str, None]" = OrderedDict()

    @property
    def name(self) -> str:
        return "launch_workflow"

    @property
    def description(self) -> str:
        return (
            "Launch a previously designed WorkflowSpec to the L2 Temporal "
            "Control Plane. Pass the workflow_spec dict from design_workflow's "
            "output, or pass workflow_id to reuse a previously designed spec. "
            "Returns Temporal workflow_id and run_id on success. "
            "Requires explicit reason. Call this when the user has confirmed "
            "they want to launch — if the user's message explicitly says to "
            "start/launch/启动/确认 the workflow, treat that as confirmation "
            "and call launch_workflow directly WITHOUT calling "
            "request_confirmation first."
        )

    @property
    def parameters_schema(self) -> dict:
        # P2-6 fix: 给 workflow_spec 补嵌套 schema 约束
        # P3-6 fix: 新增 workflow_id 参数 — 优先用此参数从 registry 取 spec，
        # 避免传整个 spec dict（省 token）。workflow_spec 改为可选（向后兼容）。
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": (
                        "P3-6: preferred — workflow_id from design_workflow output. "
                        "If provided, spec is fetched from server-side registry "
                        "(smaller payload, recommended)."
                    ),
                },
                "workflow_spec": {
                    "type": "object",
                    "description": (
                        "The WorkflowSpec dict from design_workflow output. "
                        "Required if workflow_id is not provided or registry lookup fails. "
                        "Must contain 'objective' (non-empty) and 'nodes' (non-empty array)."
                    ),
                    "properties": {
                        "workflow_id": {"type": "string"},
                        "objective": {"type": "string", "minLength": 1},
                        "requirements": {"type": "array", "items": {"type": "string"}},
                        "nodes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "node_id": {"type": "string", "minLength": 1},
                                    "type": {
                                        "type": "string",
                                        "enum": ["brain_task", "tool_task", "human_task", "wait_task"],
                                    },
                                    "capability": {"type": "string"},
                                    "depends_on": {"type": "array", "items": {"type": "string"}},
                                    "input": {"type": "object"},
                                    "on_failure": {
                                        "type": "string",
                                        "enum": ["abort", "continue", "escalate", "retry"],
                                    },
                                },
                                "required": ["node_id", "type"],
                            },
                        },
                        "metadata": {"type": "object"},
                    },
                    "required": ["objective", "nodes"],
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Reason for launching (required by policy)",
                },
            },
            "required": ["reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        reason = kwargs["reason"]
        workflow_id_param = kwargs.get("workflow_id")
        spec_dict = kwargs.get("workflow_spec")

        # P3-6 fix: 优先用 workflow_id 从 registry 取 spec（省 token）
        # 取不到或未提供 workflow_id 时，回退到 workflow_spec 参数（向后兼容）
        spec = None
        if workflow_id_param:
            from cognitiveplane.control.tools.spec_registry import get_default_registry
            spec = get_default_registry().get(workflow_id_param)
            if spec is None:
                # registry 未命中（过期或未注册）— 若同时传了 spec 则回退，否则报错
                if spec_dict is None:
                    return ToolResult(error=(
                        f"workflow_id '{workflow_id_param}' not found in registry "
                        "(expired or never designed). Re-run design_workflow or pass "
                        "workflow_spec explicitly."
                    ))
                logger.warning(
                    "launch_workflow: workflow_id=%s not in registry, falling back to workflow_spec",
                    workflow_id_param,
                )

        if spec is None:
            if spec_dict is None:
                return ToolResult(error=(
                    "Either workflow_id or workflow_spec must be provided."
                ))
            # P2-6 fix: 用 Pydantic 校验（而非手工逐字段重建）
            try:
                from cognitiveplane.shared.dto_workflow import WorkflowSpec
                spec = WorkflowSpec.model_validate(spec_dict)
            except Exception as e:
                return ToolResult(error=f"Invalid workflow_spec (validation failed): {e}")

        # P2-8 fix: 限流改为按 workflow_id 去重 + 全局上限
        # 同一 workflow_id 不重复启动（防止 LLM 重试已启动的 workflow）
        if spec.workflow_id in self._launched_ids:
            return ToolResult(error=(
                f"Workflow {spec.workflow_id} already launched. "
                "Use query_status to check progress instead of re-launching."
            ))
        # P3-2 fix: 达到上限时 LRU 淘汰最老条目，而非拒绝新 launch
        if len(self._launched_ids) >= self._MAX_TOTAL_LAUNCHES:
            evicted, _ = self._launched_ids.popitem(last=False)
            logger.info(
                "launch_workflow: LRU evicted oldest workflow_id=%s (limit=%d)",
                evicted, self._MAX_TOTAL_LAUNCHES,
            )

        # P2-5 fix: 检查 nodes 非空（schema 已约束 minItems=1，但双重保险）
        if not spec.nodes:
            return ToolResult(error="WorkflowSpec must have at least one node")

        # P4 fix: 解析 image_refs → 落盘 → 注入 image_path
        # design_workflow 把 PENDING:session_id:index 引用塞进每个 tool_task
        # node 的 input.image_refs。L3 IQA/PPA 需要磁盘路径（image_path）才能
        # 通过 preprocessor.DecodeStep 读取图像。ImageStore 是内存的，跨进程
        # 访问必须落盘。
        # P2-3 fix: 解析前先清理过期临时文件（低频路径，每次 launch 最多扫一次盘）
        if self._image_store is not None:
            removed = _cleanup_old_image_files()
            if removed > 0:
                logger.info("launch_workflow: cleaned up %d expired image files", removed)
        resolved_count = 0
        if self._image_store is not None:
            for node in spec.nodes:
                if node.type != "tool_task":
                    continue
                refs = node.input.get("image_refs") if node.input else None
                if not refs:
                    continue
                paths = await self._resolve_image_refs_to_paths(refs)
                if paths:
                    # P3-1 fix: 多图场景处理策略
                    # - image_path: 第一张图（向后兼容 IQA/PPA 单图接口）
                    # - image_paths: 完整列表（L3 activity 如需多图遍历可用此字段）
                    # 当前 IQA/PPA 实现只读 image_path，多图时其余图被忽略。
                    # design_workflow 应为多图场景拆成多个 tool_task node（每图一 node），
                    # 或等 L3 IQA 支持批量输入后切换到 image_paths 遍历。
                    node.input["image_path"] = paths[0]
                    node.input["image_paths"] = paths
                    resolved_count += len(paths)
                    if len(paths) > 1:
                        logger.warning(
                            "launch_workflow: node=%s got %d image_refs but L3 IQA/PPA "
                            "only processes image_path (paths[0]). Multiple images should "
                            "be split into separate tool_task nodes by design_workflow.",
                            node.node_id, len(paths),
                        )
                    logger.info(
                        "launch_workflow: resolved %d image_refs for node=%s → image_path=%s",
                        len(paths), node.node_id, paths[0],
                    )
                else:
                    logger.warning(
                        "launch_workflow: failed to resolve any image_refs for node=%s refs=%s",
                        node.node_id, refs,
                    )

        # 从 deps 取 EventConnector
        event_connector = None
        if self._deps and hasattr(self._deps, "bridge"):
            event_connector = self._deps.bridge.event_connector if self._deps.bridge else None

        if event_connector is None:
            return ToolResult(error=(
                "Bridge not configured: EventConnector unavailable. "
                "Set TemporalWorkflowLaunchPort in app.py to enable L1→L2 trigger."
            ))

        # 提交到 L2 Temporal
        try:
            result = await event_connector.on_workflow_spec(spec)
            if result.accepted:
                # P2-8 fix: 记录已启动的 workflow_id（去重）
                # P3-2 fix: OrderedDict 记录顺序，重复 launch 时 move_to_end 刷新 LRU
                self._launched_ids[spec.workflow_id] = None
                self._launch_count += 1  # 保留向后兼容
            return ToolResult(output={
                "status": "launched" if result.accepted else "failed",
                "workflow_id": result.workflow_id,
                "run_id": result.run_id,
                "adapter": result.adapter,
                "accepted": result.accepted,
                "error": result.error,
                "objective": spec.objective,
                "node_count": len(spec.nodes),
                "reason": reason,
                "images_resolved": resolved_count,
            })
        except Exception as e:
            return ToolResult(error=f"Launch failed: {e}")

    async def _resolve_image_refs_to_paths(self, refs: list[str]) -> list[str]:
        """把 image_ref (PENDING:session_id:index) 解析成磁盘文件路径。

        ImageStore.get_original(image_ref) 返回 (bytes, mime_type)；
        我们把 bytes 写到 _IMAGE_TMP_DIR 下，文件名从 image_ref 派生
        （替换 ":" 为 "_"，扩展名按 mime 推断）。

        幂等：同一 image_ref 多次调用会覆盖同一文件（同内容）。

        P2-4 fix: 磁盘写入用 asyncio.to_thread 包装，避免阻塞事件循环。
        ImageStore.get_original 是内存 dict 查找（μs 级）保持同步。
        """
        if self._image_store is None:
            return []
        paths: list[str] = []
        for ref in refs:
            try:
                result = self._image_store.get_original(ref)
            except Exception as e:
                logger.warning("image_store.get_original(%s) failed: %s", ref, e)
                continue
            if result is None:
                logger.warning("image_ref %s not found in ImageStore", ref)
                continue
            raw_bytes, mime = result
            ext = _mime_to_ext(mime)
            # PENDING:session_id:0 → PENDING_session_id_0
            safe_name = ref.replace(":", "_").replace("/", "_")
            filename = f"{safe_name}{ext}"
            file_path = os.path.join(_IMAGE_TMP_DIR, filename)
            # P2-4 fix: 同步文件 I/O 移到线程池，不阻塞 asyncio 事件循环
            try:
                await asyncio.to_thread(_write_bytes_to_file, file_path, raw_bytes)
                paths.append(file_path)
            except OSError as e:
                logger.error("Failed to write image file %s: %s", file_path, e)
        return paths


def _mime_to_ext(mime: str) -> str:
    """MIME 类型 → 文件扩展名（用于 image_path 落盘）。"""
    if not mime:
        return ".png"
    mime_lower = mime.lower()
    if "jpeg" in mime_lower or "jpg" in mime_lower:
        return ".jpg"
    if "png" in mime_lower:
        return ".png"
    if "tiff" in mime_lower or "tif" in mime_lower:
        return ".tiff"
    if "bmp" in mime_lower:
        return ".bmp"
    if "webp" in mime_lower:
        return ".webp"
    return ".png"  # 默认 PNG（IQA preprocessor 通过 cv2.imread 自动识别）


__all__ = ["LaunchWorkflowTool"]
