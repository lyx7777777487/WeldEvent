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
import base64
import io
import json
import logging
import os
import tempfile
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.dto_workflow import (
    ON_FAILURE_DEFAULT,
    node_type_enum_values,
    on_failure_enum_values,
)

if TYPE_CHECKING:
    from cognitiveplane.interaction.image_store import ImageStore

logger = logging.getLogger(__name__)

# 临时图片目录 — Temporal activity 可能重试，文件需在工作流生命周期内存在
_IMAGE_TMP_DIR = os.path.join(tempfile.gettempdir(), "weldevent_upload_images")

# Workflow 嵌入图片压缩参数 — 原图 base64 可能 8MB+，Temporal gRPC 限制 ~2MB
_WF_IMAGE_MAX_DIM = 1024  # 长边最大像素
_WF_IMAGE_JPEG_QUALITY = 75  # JPEG 压缩质量


def _compress_image_for_workflow(raw_bytes: bytes) -> bytes:
    """压缩图片到适合 WorkflowSpec 嵌入的尺寸。

    通常 20MP 原图 (5-15MB) → 1024px JPEG (80-150KB)，足够 IQA/标注使用。
    """
    try:
        from PIL import Image
    except ImportError:
        return raw_bytes  # 无 PIL 时原样返回（server 可能没装 Pillow）

    buf = io.BytesIO(raw_bytes)
    try:
        img = Image.open(buf)
        fmt = img.format
        w, h = img.size
        if max(w, h) <= _WF_IMAGE_MAX_DIM and fmt == "JPEG":
            return raw_bytes  # 已足够小，无需压缩

        # 缩放
        if max(w, h) > _WF_IMAGE_MAX_DIM:
            ratio = _WF_IMAGE_MAX_DIM / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        # 转 RGB（JPEG 不支持 alpha）
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=_WF_IMAGE_JPEG_QUALITY)
        compressed = out.getvalue()
        logger.debug(
            "_compress_image: %dKB → %dKB (fmt=%s, dims=%dx%d)",
            len(raw_bytes) // 1024, len(compressed) // 1024, fmt, w, h,
        )
        return compressed
    except Exception:
        return raw_bytes
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
            except OSError as e:
                # 文件可能被并发删除 — 跳过
                logger.debug("cleanup skipped %s: %s", path, e)
                continue
    except OSError as e:
        logger.debug("cleanup listdir failed: %s", e)
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
            "启动已设计的工作流到 L2 执行层。\n"
            "**何时使用**：design_workflow 返回方案后，立即调用本工具启动执行。\n"
            "**用法**：传 workflow_spec（design_workflow 的输出）或 workflow_id（复用已有方案）。\n"
            "**约束**：本工具走架构级 human-in-the-loop gate，调用后前端会弹确认卡片，"
            "你只需直接调用，无需在调用前再弹窗确认。"
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
                                        # P0-1 fix: enum 从 SSOT 派生
                                        "enum": node_type_enum_values(),
                                    },
                                    "capability": {"type": "string"},
                                    "depends_on": {"type": "array", "items": {"type": "string"}},
                                    "input": {"type": "object"},
                                    "on_failure": {
                                        "type": "string",
                                        # P0-1 fix: enum 从 SSOT 派生,禁止手写
                                        "enum": on_failure_enum_values(),
                                        "default": ON_FAILURE_DEFAULT,
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
                "wait_for_completion": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "P1-3 fix: 默认 false — 立即返回 workflow_id,用 query_status 异步查询进度. "
                        "true 时同步等待完成(最多 wait_timeout 秒),仅用于短 workflow 调试. "
                        "生产/长 workflow 必须 false,避免阻塞 ReAct 主循环."
                    ),
                },
                "wait_timeout": {
                    "type": "number",
                    "default": 30.0,
                    "description": "Max seconds to wait when wait_for_completion=true. Default 30s.",
                },
            },
            "required": ["reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        reason = kwargs["reason"]
        workflow_id_param = kwargs.get("workflow_id")
        spec_dict = kwargs.get("workflow_spec")
        # P1-3 fix: 默认立即返回,不阻塞 ReAct 主循环
        wait_for_completion = bool(kwargs.get("wait_for_completion", False))
        wait_timeout = float(kwargs.get("wait_timeout", 30.0))
        # P2-4 fix: 去重键改用 (session_id, objective_hash)
        # 原 workflow_id 每次 uuid4 生成,去重永不命中。session_id 从 ReAct context 注入
        session_id = str(kwargs.get("session_id", "default"))

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

        # P2-4 fix: 去重键改用 (session_id, objective_hash) 而非 workflow_id
        # 原 workflow_id 每次 uuid4 生成,去重永不命中,LRU 形同虚设。
        # 新键:同 session 同 objective 短时间重复 launch 被拒(防 LLM 重试已启动 workflow),
        # 不同 session 或不同 objective 不冲突。workflow_id 仍记录用于 query_status。
        import hashlib
        objective_hash = hashlib.sha1(spec.objective.encode("utf-8")).hexdigest()[:12]
        dedup_key = f"{session_id}:{objective_hash}"
        if dedup_key in self._launched_ids:
            existing_wf_id = self._launched_ids[dedup_key]
            return ToolResult(error=(
                f"Workflow for objective (hash={objective_hash}) already launched in this session "
                f"(workflow_id={existing_wf_id}). Use query_status to check progress instead of re-launching."
            ))
        # P3-2 fix: 达到上限时 LRU 淘汰最老条目，而非拒绝新 launch
        if len(self._launched_ids) >= self._MAX_TOTAL_LAUNCHES:
            evicted, _ = self._launched_ids.popitem(last=False)
            logger.info(
                "launch_workflow: LRU evicted oldest dedup_key=%s (limit=%d)",
                evicted, self._MAX_TOTAL_LAUNCHES,
            )

        # P2-5 fix: 检查 nodes 非空（schema 已约束 minItems=1，但双重保险）
        if not spec.nodes:
            return ToolResult(error="WorkflowSpec must have at least one node")

        # P4 fix: 解析 image_refs → base64 嵌入 WorkflowSpec + 落盘（向后兼容）
        # design_workflow 把 PENDING:session_id:index 引用塞进每个 tool_task
        # node 的 input.image_refs。ImageStore 是内存的，跨进程必须嵌入数据。
        # 主路径: image_b64 (base64 raw bytes) → L3 DecodeStep 直接解码，无需共享文件系统
        # 副路径: image_path (磁盘落盘) → 向后兼容，L3 同机时可走文件路径
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
                # 主路径：PENDING → base64 嵌入（跨进程安全）
                b64_list = await self._resolve_image_refs_to_b64(refs)
                if b64_list:
                    node.input["image_b64"] = b64_list[0]
                    node.input["image_mime"] = "image/jpeg"  # 压缩后固定 JPEG
                    resolved_count += len(b64_list)
                    logger.info(
                        "launch_workflow: embedded %d image_b64 for node=%s",
                        len(b64_list), node.node_id,
                    )
                # 副路径：磁盘落盘（向后兼容）
                paths = await self._resolve_image_refs_to_paths(refs)
                if paths:
                    node.input["image_path"] = paths[0]
                    node.input["image_paths"] = paths
                    if len(paths) > 1:
                        logger.warning(
                            "launch_workflow: node=%s got %d image_refs but L3 IQA/PPA "
                            "only processes image_path (paths[0]). Multiple images should "
                            "be split into separate tool_task nodes by design_workflow.",
                            node.node_id, len(paths),
                        )
                if not b64_list and not paths:
                    logger.warning(
                        "launch_workflow: failed to resolve any image_refs for node=%s refs=%s",
                        node.node_id, refs,
                    )

        # P2-5 fix: payload size 预检 — Temporal gRPC payload 默认 ~2MB 限制
        # 大图 base64 后可能超限,workflow 启动失败。预检 + 超限拒绝(给清晰错误)
        # 阈值 1.5MB 留余量给其他字段 + gRPC overhead
        MAX_PAYLOAD_BYTES = 1_500_000
        try:
            spec_payload_size = len(json.dumps(spec.model_dump(), default=str).encode("utf-8"))
        except Exception:
            spec_payload_size = 0
        if spec_payload_size > MAX_PAYLOAD_BYTES:
            # 找出最大的 image_b64 占比
            big_nodes = []
            for n in spec.nodes:
                if n.input and n.input.get("image_b64"):
                    b64_val = n.input["image_b64"]
                    b64_size = len(b64_val) if isinstance(b64_val, str) else (
                        len(b64_val[0]) if isinstance(b64_val, tuple) and b64_val else 0
                    )
                    if b64_size > 100_000:
                        big_nodes.append(f"{n.node_id}({b64_size // 1024}KB)")
            return ToolResult(error=(
                f"WorkflowSpec payload too large: {spec_payload_size // 1024}KB > "
                f"{MAX_PAYLOAD_BYTES // 1024}KB limit. Large image_b64 nodes: {big_nodes}. "
                f"Use smaller images or image_path (disk-based) instead of image_b64. "
                f"Temporal gRPC payload limit ~2MB will reject this workflow."
            ))

        # 从 deps 取 EventConnector
        event_connector = None
        if self._deps and hasattr(self._deps, "bridge"):
            event_connector = self._deps.bridge.event_connector if self._deps.bridge else None

        if event_connector is None:
            return ToolResult(error=(
                "Bridge not configured: EventConnector unavailable. "
                "Set TemporalWorkflowLaunchPort in app.py to enable L1→L2 trigger."
            ))

        # P1-3: 注入 session_id + callback_url 到 spec.metadata,
        # 让 L2 workflow 在节点 start/end/error 时能 emit HTTP 事件到 L1。
        # L1 收到后按 session_id 路由到 WorkflowEventBus,广播给前端 SSE 订阅者。
        import os
        l1_base_url = os.environ.get("L1_CALLBACK_URL", "http://localhost:8000")
        spec.metadata = dict(spec.metadata)  # 防止 frozen 影响其他引用
        spec.metadata.setdefault("session_id", session_id)
        spec.metadata.setdefault("callback_url", f"{l1_base_url}/api/v1/chat/workflow/events")

        # 提交到 L2 Temporal
        try:
            result = await event_connector.on_workflow_spec(spec)
        except Exception as e:
            return ToolResult(error=f"Launch failed: {e}")

        if not result.accepted:
            return ToolResult(output={
                "status": "failed",
                "workflow_id": result.workflow_id,
                "run_id": result.run_id,
                "adapter": result.adapter,
                "accepted": False,
                "error": result.error,
                "objective": spec.objective,
                "node_count": len(spec.nodes),
                "reason": reason,
                "images_resolved": resolved_count,
            })

        # P2-4 fix: 记录 dedup_key → workflow_id 映射(去重 + 供错误信息引用)
        self._launched_ids[dedup_key] = spec.workflow_id
        self._launch_count += 1

        # P1-3 fix: 默认立即返回 workflow_id,不阻塞 ReAct 主循环
        # LLM 用 query_status 异步查询进度;wait_for_completion=true 才同步轮询(调试用)
        node_labels = _build_node_labels(spec)
        progress_events: list[dict] = []

        if not wait_for_completion:
            # 立即返回 — 推荐路径
            return ToolResult(
                output={
                    "status": "RUNNING",
                    "workflow_id": result.workflow_id,
                    "run_id": result.run_id,
                    "adapter": result.adapter,
                    "accepted": True,
                    "objective": spec.objective,
                    "node_count": len(spec.nodes),
                    "node_labels": node_labels,
                    "completed_nodes": [],
                    "failed_nodes": [],
                    "mocked_nodes": [],
                    "node_results": {},
                    "reason": reason,
                    "images_resolved": resolved_count,
                    "next_action": (
                        "Workflow launched asynchronously. Use query_status with "
                        f"workflow_id={result.workflow_id} to poll progress."
                    ),
                },
                progress_events=progress_events,
            )

        # wait_for_completion=true — 同步轮询(仅调试/短 workflow 用)
        node_results = await self._poll_workflow(
            event_connector, spec.workflow_id, spec.nodes, progress_events,
            objective=spec.objective,
            max_wait=wait_timeout,
        )

        # P0-2 fix: 暴露 mocked_nodes,让 LLM/用户识别"哪些节点没真执行"
        mocked_nodes = node_results.get("mocked_nodes", [])
        mocked_warning = ""
        if mocked_nodes:
            mocked_warning = (
                f" ⚠️ MOCK WARNING: {len(mocked_nodes)} node(s) {mocked_nodes} executed as "
                f"MOCK (no real L3 activity). Their results MUST NOT be treated as real "
                f"pass. Investigate L3 ActivityPool registration before trusting this workflow."
            )

        # Op 8.2/29: Store episode in TrajectoryMemoryStore for learning
        try:
            from cognitiveplane.control.tools.design_workflow import get_trajectory_memory
            exec_record = node_results.get("execution_record")
            trajectory_mem = get_trajectory_memory()
            node_outcomes = [
                {"node_id": nid, "status": node_results.get("node_results", {}).get(nid, {}).get("status", "unknown")}
                for nid in node_results.get("completed_nodes", []) + node_results.get("failed_nodes", [])
            ]
            wf_status = node_results.get("status", "unknown")
            await trajectory_mem.store_episode(
                goal=spec.objective,
                spec_summary={"node_count": len(spec.nodes), "capabilities": [n.capability for n in spec.nodes]},
                node_outcomes=node_outcomes,
                outcome=wf_status,
                quality_score=exec_record.get("quality_score", 0.0) if exec_record else 0.0,
                session_id=session_id,
            )
            # Op 32: Extract Reflexion notes from failures
            failed = node_results.get("failed_nodes", [])
            if failed:
                trajectory_mem.add_reflexion_note(
                    task=spec.objective,
                    failure=f"Nodes failed: {failed}",
                    lesson=f"Check L3 activity registration and node parameters for {failed}",
                )
        except Exception as e:
            logger.warning("Failed to store episode: %s", e)

        # Op 8.3: Zero-cost self-evaluation of workflow execution quality
        try:
            from cognitiveplane.capability.evaluation import ZeroCostEvaluator
            evaluator = ZeroCostEvaluator(
                llm_provider=self._deps.capability.llm_provider
                if self._deps and hasattr(self._deps, 'capability') else None,
            )
            node_outcomes_eval = [
                {"node_id": nid, "status": node_results.get("node_results", {}).get(nid, {}).get("status", "unknown")}
                for nid in node_results.get("completed_nodes", []) + node_results.get("failed_nodes", [])
            ]
            quality_eval = await evaluator.evaluate(
                goal=spec.objective,
                node_outcomes=node_outcomes_eval,
            )
            # Store quality score in trajectory memory
            trajectory_mem = get_trajectory_memory()
            if trajectory_mem._episodic:
                trajectory_mem._episodic[-1].quality_score = quality_eval.quality_score
        except Exception as e:
            logger.warning("Zero-cost evaluation failed: %s", e)

        return ToolResult(
            output={
                "status": node_results.get("status", "unknown"),
                "workflow_id": result.workflow_id,
                "run_id": result.run_id,
                "adapter": result.adapter,
                "accepted": True,
                "objective": spec.objective,
                "node_count": len(spec.nodes),
                "node_labels": node_labels,
                "completed_nodes": node_results.get("completed_nodes", []),
                "failed_nodes": node_results.get("failed_nodes", []),
                # P0-2 fix: mock 节点透明化
                "mocked_nodes": mocked_nodes,
                "mock_warning": mocked_warning or None,
                "node_results": node_results.get("node_results", {}),
                "reason": reason,
                "images_resolved": resolved_count,
            },
            progress_events=progress_events,
        )

    async def _poll_workflow(
        self,
        connector,
        workflow_id: str,
        nodes: list,
        progress_events: list[dict],
        objective: str = "",
        poll_interval: float = 1.0,
        max_wait: float = 60.0,
    ) -> dict:
        """轮询 Temporal 等待工作流完成，推送每步进度。

        每 poll_interval 秒查询一次 query_status，检测新增的完成/失败节点，
        追加 progress_event。工作流 COMPLETED/FAILED 或超时后返回最终结果。

        Args:
            connector: EventConnector 实例
            workflow_id: Temporal workflow ID
            nodes: spec.nodes 列表（用于映射 capability → 显示名）
            progress_events: 追加进度事件的列表（引用传递）
            objective: 工作流目标描述（用于 started 事件的 label）
            poll_interval: 轮询间隔（秒）
            max_wait: 最长等待时间（秒）

        Returns:
            query_status 返回的 dict（含 status, completed_nodes, node_results 等）
        """
        import time as time_module
        started = time_module.monotonic()
        seen_completed: set[str] = set()
        seen_failed: set[str] = set()
        last_result: dict = {}

        node_label_map = _build_node_labels_for_nodes(nodes)

        # 推送 started 事件，让前端执行面板初始化完整节点列表
        progress_events.append({
            "status": "started",
            "workflow_id": workflow_id,
            "label": objective,
            "nodes": [
                {
                    "node_id": n.node_id if hasattr(n, "node_id") else n.get("node_id", ""),
                    "label": node_label_map.get(
                        n.node_id if hasattr(n, "node_id") else n.get("node_id", ""),
                        n.node_id if hasattr(n, "node_id") else n.get("node_id", ""),
                    ),
                }
                for n in nodes
            ],
        })

        while (time_module.monotonic() - started) < max_wait:
            try:
                status = await connector.query_status(workflow_id)
            except Exception as e:
                logger.warning("query_status failed for %s: %s", workflow_id, e)
                await asyncio.sleep(poll_interval)
                continue

            last_result = status
            wf_status = status.get("status", "UNKNOWN")
            completed = set(status.get("completed_nodes", []))
            failed = set(status.get("failed_nodes", []))
            node_results = status.get("node_results", {})

            # 检查新完成的节点
            new_done = (completed | failed) - seen_completed - seen_failed
            for node_id in sorted(new_done):
                label = node_label_map.get(node_id, node_id)
                nr = node_results.get(node_id, {})
                if node_id in failed:
                    seen_failed.add(node_id)
                    progress_events.append({
                        "node_id": node_id,
                        "label": label,
                        "status": "failed",
                        "summary": nr.get("error", "执行失败"),
                    })
                else:
                    seen_completed.add(node_id)
                    progress_events.append({
                        "node_id": node_id,
                        "label": label,
                        "status": "completed",
                        "summary": _summarize_node_result(node_id, nr),
                    })

            if wf_status in ("COMPLETED", "FAILED"):
                break

            await asyncio.sleep(poll_interval)
        else:
            # 超时 — 返回最新状态
            last_result.setdefault("status", "RUNNING")

        return last_result

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
                # 防御：LLM 可能传了模板字符串 "PENDING:session_id:N" 而非真实 session_id
                # 尝试用 ImageStore 中已有的键进行模糊匹配
                if "session_id" in ref:
                    logger.warning(
                        "image_ref %s looks like a template literal. "
                        "Trying fuzzy match in ImageStore...", ref
                    )
                    result = self._image_store_fuzzy_match(ref)
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

    async def _resolve_image_refs_to_b64(self, refs: list[str]) -> list[str]:
        """把 image_ref (PENDING:session_id:index) 解析成 base64 字符串。

        图片先经 _compress_image_for_workflow 压缩（1024px JPEG），
        确保 WorkflowSpec 不超 Temporal gRPC payload 限制。

        Returns:
            base64 编码的图片 raw bytes（无 data: 前缀），失败返回空列表
        """
        if self._image_store is None:
            return []
        b64_list: list[str] = []
        for ref in refs:
            try:
                result = self._image_store.get_original(ref)
            except Exception as e:
                logger.warning("image_store.get_original(%s) for b64 failed: %s", ref, e)
                continue
            if result is None:
                if "session_id" in ref:
                    result = self._image_store_fuzzy_match(ref)
                if result is None:
                    logger.warning("image_ref %s not found (b64 path)", ref)
                    continue
            raw_bytes, _mime = result
            compressed = _compress_image_for_workflow(raw_bytes)
            b64_list.append(base64.b64encode(compressed).decode("ascii"))
        return b64_list

    def _image_store_fuzzy_match(self, template_ref: str) -> tuple[bytes, str] | None:
        """防御：LLM 可能传模板字面量 "PENDING:session_id:0" 而非真实 ref。

        尝试从 template_ref 提取 index，在 ImageStore 中查找匹配该 index 的键。
        ImageStore 键格式为 PENDING:{session_id}:{index}。
        """
        # 提取 index（如 "PENDING:session_id:0" → "0"）
        try:
            index_str = template_ref.rsplit(":", 1)[-1]
            target_index = int(index_str)
        except (ValueError, IndexError):
            return None

        # 遍历 ImageStore,找 index 匹配的键
        # P3-3 fix: 用 public keys() 而非 _store 私有属性
        for key in self._image_store.keys():
            try:
                key_index = int(key.rsplit(":", 1)[-1])
                if key_index == target_index:
                    result = self._image_store.get_original(key)
                    if result is not None:
                        logger.info(
                            "fuzzy match: %s → %s (index=%d matched)",
                            template_ref, key, target_index,
                        )
                        return result
            except (ValueError, IndexError):
                continue
        return None


# ── 轮询辅助 ──────────────────────────────────────────────────────────

_NODE_DISPLAY_NAMES: dict[str, str] = {
    "iqa": "图像质量评估 (IQA)",
    "defect_detection": "图像质量评估 (IQA)",
    "ppa": "图像预处理 (PPA)",
    "preprocess": "图像预处理 (PPA)",
    "rda": "缺陷识别 (RDA)",
    "mea": "几何测量 (MEA)",
    "rva": "风险评估 (RVA)",
    "annotation": "标注任务 (Label Studio)",
    "hca": "人工复核 (HCA)",
}


def _build_node_labels(spec) -> dict[str, str]:
    """从 WorkflowSpec 构建 node_id → 业务可读名称 映射。"""
    return _build_node_labels_for_nodes(spec.nodes)


def _build_node_labels_for_nodes(nodes: list) -> dict[str, str]:
    """从节点列表构建 node_id → 业务可读名称。"""
    labels: dict[str, str] = {}
    for node in nodes:
        cap = getattr(node, "capability", None) or node.get("capability", "")
        labels[node.node_id if hasattr(node, "node_id") else node["node_id"]] = (
            _NODE_DISPLAY_NAMES.get(cap, cap or "未知步骤")
        )
    return labels


def _summarize_node_result(node_id: str, node_result: dict) -> str:
    """将节点执行结果压缩为一行可读摘要（给前端进度气泡用）。"""
    if not node_result:
        return "完成"
    data = node_result.get("data", {})
    if not data:
        err = node_result.get("error", "")
        return err or "完成"

    # IQA: 提取检查项
    checks = data.get("checks", {})
    if checks:
        parts = []
        for name, info in checks.items():
            passed = info.get("passed", False)
            icon = "✅" if passed else "⚠️"
            label_map = {
                "completeness": "焊缝区域",
                "exposure": "曝光",
                "focus": "清晰度",
            }
            parts.append(f"{icon}{label_map.get(name, name)}")
        return " ".join(parts)

    # PPA: 提取处理策略
    strategies = data.get("strategies_applied", [])
    if strategies:
        return f"处理: {', '.join(strategies)}"

    # 通用：截取 data 的前几个 key
    keys = list(data.keys())[:3]
    return f"完成 ({', '.join(keys)})"


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
