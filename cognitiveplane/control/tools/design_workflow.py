"""DesignWorkflowTool — produce a WorkflowSpec draft.

Brain decides the workflow graph; execution layers decide how to run it.
This tool must not launch Temporal or call industrial MCP directly.

编排模式（推荐）:
    LLM 通过 `nodes` 参数直接传入编排结果（capability + depends_on + input_data
    可选），工具只做能力校验和 WorkflowSpec 构建。LLM 应参考 activity_catalog
    选择合适的 activity，用户用业务语言描述需求即可，不必指定 capability 名。

Fallback 模式（兼容）:
    若 LLM 未传 `nodes`，工具按 objective/requirements 文本关键词匹配生成
    默认节点（旧逻辑，过渡期保留）。
"""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.tools.activity_catalog import (
    find_descriptor,
    get_catalog_text,
    get_supported_capabilities,
)


class DesignWorkflowTool(BrainTool):
    """Design an inspection workflow as a protocol-neutral WorkflowSpec."""

    phase = 3

    def __init__(self, deps) -> None:
        self._deps = deps

    @property
    def name(self) -> str:
        return "design_workflow"

    @property
    def description(self) -> str:
        return (
            "Design a WorkflowSpec DAG for an inspection case. "
            "Returns a draft only: it does not start Temporal, write labels, "
            "or call industrial MCP directly. Requires a reason.\n\n"
            "RECOMMENDED: pass `nodes` array to explicitly orchestrate the "
            "workflow. Choose capabilities from the L3 Activity Catalog below. "
            "Each node should specify capability + depends_on (referencing "
            "prior node_ids). image_refs will be auto-injected into each "
            "tool_task node's input — do NOT repeat them per-node.\n\n"
            + get_catalog_text()
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": (
                        "High-level goal (e.g. '检查这张焊缝图的质量并预处理'). "
                        "用业务语言描述即可，LLM 负责选择 activity。"
                    ),
                },
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of quality requirements or constraints",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for invoking workflow design (required by policy)",
                },
                "case_id": {
                    "type": "string",
                    "description": (
                        "Case ID this workflow applies to. If the user mentioned "
                        "a case_id or it appeared in read_weldmap output, pass it "
                        "here. Falls back to 'default' if unspecified."
                    ),
                },
                "image_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Image references (PENDING:session_id:index) from user uploads. "
                        "Pass the image_refs that appeared in the system prompt after user "
                        "uploaded images. They will be attached to each tool_task node so "
                        "L3 activities can resolve them to actual image files at launch time."
                    ),
                },
                "nodes": {
                    "type": "array",
                    "description": (
                        "RECOMMENDED: explicit workflow node list for LLM-driven "
                        "orchestration. Each node is an object with: "
                        "node_id (str, unique), capability (str, from catalog), "
                        "depends_on (list[str] of prior node_ids, can be empty), "
                        "input_data (dict, optional — image_refs auto-injected), "
                        "on_failure (str, optional: 'retry'|'escalate'|'skip', default 'retry'), "
                        "condition (str, optional). "
                        "If omitted, tool falls back to keyword-based default workflow."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "node_id": {"type": "string"},
                            "capability": {"type": "string"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "input_data": {"type": "object"},
                            "on_failure": {
                                "type": "string",
                                "enum": ["retry", "escalate", "skip"],
                            },
                            "condition": {"type": "string"},
                        },
                        "required": ["node_id", "capability"],
                    },
                },
            },
            "required": ["objective", "reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        objective = kwargs["objective"]
        requirements = kwargs.get("requirements", [])
        # P3-3 fix: case_id 兜底 "default"，避免 None 导致 EventConnector 用 "unknown"
        case_id = kwargs.get("case_id") or "default"
        image_refs = kwargs.get("image_refs") or []
        nodes_param = kwargs.get("nodes")
        try:
            if nodes_param:
                # LLM 自主编排路径：校验 + 构建 spec
                spec = _build_spec_from_nodes(
                    nodes=nodes_param,
                    objective=objective,
                    requirements=requirements,
                    case_id=case_id,
                    reason=kwargs["reason"],
                    image_refs=image_refs,
                )
            else:
                # Fallback: 关键词匹配（过渡期保留）
                spec = _build_workflow_spec_fallback(
                    objective=objective,
                    requirements=requirements,
                    case_id=case_id,
                    reason=kwargs["reason"],
                    image_refs=image_refs,
                )
            # P3-6 fix: 把 spec 存入 registry，LLM 只需传 workflow_id 给 launch_workflow
            from cognitiveplane.control.tools.spec_registry import get_default_registry
            workflow_id = get_default_registry().put(spec)
            return ToolResult(output={
                "status": "draft",
                "workflow_id": workflow_id,
                "workflow_spec": spec.model_dump(),
                "orchestration_mode": "llm_nodes" if nodes_param else "keyword_fallback",
            })
        except ValueError as e:
            # 校验错误：明确返回，让 LLM 修正 nodes 后重试
            return ToolResult(error=f"Workflow validation failed: {e}")
        except Exception as e:
            return ToolResult(error=str(e))


# ── LLM 自主编排路径 ──────────────────────────────────────────────────
def _build_spec_from_nodes(
    *,
    nodes: list[dict],
    objective: str,
    requirements: list[str],
    case_id: str,
    reason: str,
    image_refs: list[str] | None = None,
):
    """从 LLM 提供的 nodes 列表构建 WorkflowSpec。

    校验规则:
        1. node_id 唯一
        2. capability 必须在 activity_catalog 中（含别名）
        3. depends_on 必须引用已声明的 node_id
        4. image_refs 自动注入到每个 tool_task 节点的 input_data

    Raises:
        ValueError: 校验失败时抛出，由 execute() 转为 ToolResult error。
    """
    from cognitiveplane.shared.dto_workflow import (
        CallerContext, WorkflowNode, WorkflowSpec,
    )

    refs = list(image_refs or [])
    supported = set(get_supported_capabilities())

    # 1. node_id 唯一性 + capability 校验
    seen_ids: set[str] = set()
    workflow_nodes: list[WorkflowNode] = []
    # 记录每个 capability 对应的实现状态，供 metadata 反馈给 LLM
    impl_status: dict[str, str] = {}

    for raw in nodes:
        node_id = raw.get("node_id") or ""
        capability = raw.get("capability") or ""

        if not node_id:
            raise ValueError("Each node must have a non-empty node_id")
        if node_id in seen_ids:
            raise ValueError(f"Duplicate node_id: {node_id}")
        seen_ids.add(node_id)

        if capability not in supported:
            raise ValueError(
                f"Unknown capability '{capability}' in node '{node_id}'. "
                f"Supported: {sorted(get_supported_capabilities())}"
            )

        descriptor = find_descriptor(capability)
        impl_status[node_id] = descriptor.implementation if descriptor else "unknown"

        # depends_on 校验延后到第二轮（先收集所有 node_id）
        depends_on = list(raw.get("depends_on") or [])

        # 构造 input_data：合并 LLM 传入的 + 自动注入 image_refs/objective/requirements
        input_data: dict = dict(raw.get("input_data") or {})
        input_data.setdefault("objective", objective)
        input_data.setdefault("requirements", requirements)
        # tool_task 类型自动注入 image_refs（human_task / brain_task 通常不需要图片）
        node_type = _infer_node_type(capability, descriptor)
        if node_type == "tool_task" and refs:
            input_data.setdefault("image_refs", refs)

        on_failure = raw.get("on_failure") or "retry"
        condition = raw.get("condition")

        workflow_nodes.append(WorkflowNode(
            node_id=node_id,
            type=node_type,
            capability=capability,
            depends_on=depends_on,
            input=input_data,
            condition=condition,
            on_failure=on_failure,
            caller_context=CallerContext(
                caller_type="brain_direct",
                case_id=case_id,
                node_id=node_id,
            ),
        ))

    if not workflow_nodes:
        raise ValueError("nodes list is empty")

    # 2. depends_on 引用完整性校验
    for node in workflow_nodes:
        for dep in node.depends_on:
            if dep not in seen_ids:
                raise ValueError(
                    f"Node '{node.node_id}' depends_on unknown node_id '{dep}'. "
                    f"Available: {sorted(seen_ids)}"
                )
            if dep == node.node_id:
                raise ValueError(f"Node '{node.node_id}' cannot depend on itself")

    # 3. 简单环路检测（DAG 校验）
    _check_no_cycle(workflow_nodes)

    return WorkflowSpec(
        objective=objective,
        requirements=requirements,
        nodes=workflow_nodes,
        metadata={
            "status": "draft",
            "reason": reason,
            "case_id": case_id,
            "generated_by": "design_workflow.llm_nodes",
            "image_refs": refs,
            "implementation_status": impl_status,
        },
    )


def _infer_node_type(capability: str, descriptor) -> str:
    """根据 capability 推断节点类型。"""
    # 人工审核类 → human_task
    if capability in ("hca", "human_review", "annotation_write"):
        return "human_task"
    # Brain 自评估（理论上 LLM 不会主动选这个，但兜底）
    if capability in ("brain_assessment",):
        return "brain_task"
    # 其余都是 tool_task
    return "tool_task"


def _check_no_cycle(nodes: list) -> None:
    """拓扑排序检测环路。"""
    node_map = {n.node_id: n for n in nodes}
    color: dict[str, int] = {nid: 0 for nid in node_map}  # 0=white, 1=gray, 2=black

    def visit(nid: str, path: list[str]) -> None:
        if color[nid] == 1:
            cycle = " → ".join(path + [nid])
            raise ValueError(f"Cycle detected in workflow DAG: {cycle}")
        if color[nid] == 2:
            return
        color[nid] = 1
        for dep in node_map[nid].depends_on:
            if dep in node_map:
                visit(dep, path + [nid])
        color[nid] = 2

    for nid in node_map:
        if color[nid] == 0:
            visit(nid, [])


# ── Fallback 路径（关键词匹配，过渡期保留） ──────────────────────────
def _build_workflow_spec_fallback(
    *,
    objective: str,
    requirements: list[str],
    case_id: str | None,
    reason: str,
    image_refs: list[str] | None = None,
):
    """关键词匹配生成默认 workflow（LLM 未传 nodes 时使用）。"""
    from cognitiveplane.shared.dto_workflow import CallerContext, WorkflowNode, WorkflowSpec

    text = " ".join([objective, *requirements]).lower()
    nodes: list[WorkflowNode] = []
    refs = list(image_refs or [])

    def ctx(node_id: str) -> CallerContext:
        return CallerContext(caller_type="brain_direct", case_id=case_id, node_id=node_id)

    def add_node(
        node_id: str,
        node_type: str,
        capability: str | None = None,
        depends_on: list[str] | None = None,
        input_data: dict | None = None,
        condition: str | None = None,
        on_failure: str = "escalate",
    ) -> None:
        nodes.append(WorkflowNode(
            node_id=node_id,
            type=node_type,
            capability=capability,
            depends_on=depends_on or [],
            input=input_data or {},
            condition=condition,
            on_failure=on_failure,
            caller_context=ctx(node_id),
        ))

    previous: list[str] = []

    if any(k in text for k in ("detect", "defect", "缺陷", "检测", "气孔", "裂纹")):
        iqa_input: dict = {"objective": objective, "requirements": requirements}
        if refs:
            iqa_input["image_refs"] = refs
        add_node(
            "defect_detection",
            "tool_task",
            capability="defect_detection",
            input_data=iqa_input,
            on_failure="retry",
        )
        previous = ["defect_detection"]

    if any(k in text for k in (
        "preprocess", "preprocessing", "enhance", "denoise", "sharpen",
        "预处理", "去噪", "增强", "校正", "图像处理",
    )):
        ppa_input: dict = {"objective": objective, "requirements": requirements}
        if refs:
            ppa_input["image_refs"] = refs
        add_node(
            "image_preprocess",
            "tool_task",
            capability="preprocess",
            depends_on=previous,
            input_data=ppa_input,
            on_failure="retry",
        )
        previous = ["image_preprocess"]

    if any(k in text for k in ("review", "human", "人工", "复核", "确认")):
        add_node(
            "human_review",
            "human_task",
            capability="human_review",
            depends_on=previous,
            condition="low_confidence_or_user_required",
        )
        previous = ["human_review"]

    label_studio_keywords = (
        "label studio", "label_studio", "labeling", "标注任务", "标注平台",
        "预标注", "拉取标注", "获取标注", "导出数据集", "annotation task",
        "push prediction", "fetch annotation", "export dataset",
    )
    if any(k in text for k in label_studio_keywords):
        if any(k in text for k in ("预标注", "push prediction", "推送预标注", "prediction")):
            anno_action = "push_prediction"
        elif any(k in text for k in ("拉取标注", "获取标注", "fetch annotation")):
            anno_action = "fetch_annotations"
        elif any(k in text for k in ("导出数据集", "export dataset")):
            anno_action = "export_dataset"
        else:
            anno_action = "create_task"

        ls_input: dict = {
            "objective": objective,
            "requirements": requirements,
            "action": anno_action,
        }
        if refs:
            ls_input["image_refs"] = refs
        add_node(
            "label_studio_annotation",
            "tool_task",
            capability="annotation",
            depends_on=previous,
            input_data=ls_input,
            on_failure="retry",
        )
        previous = ["label_studio_annotation"]

    elif any(k in text for k in ("annotat", "label", "标注", "写入")):
        anno_input: dict = {"objective": objective, "requirements": requirements}
        if refs:
            anno_input["image_refs"] = refs
        add_node(
            "annotation_write",
            "tool_task",
            capability="annotation_write",
            depends_on=previous,
            input_data=anno_input,
        )
        previous = ["annotation_write"]

    if not nodes:
        add_node(
            "brain_assessment",
            "brain_task",
            capability="brain_assessment",
            input_data={"objective": objective, "requirements": requirements},
        )

    return WorkflowSpec(
        objective=objective,
        requirements=requirements,
        nodes=nodes,
        metadata={
            "status": "draft",
            "reason": reason,
            "case_id": case_id,
            "generated_by": "design_workflow.keyword_fallback",
            "image_refs": refs,
        },
    )
