"""Activity Catalog — L3 执行层能力清单（供 LLM 自主编排工作流）。

设计目的:
  用户是业务人员，不懂技术，不会明确说"我要 IQA + PPA"。
  LLM 应该根据用户的业务需求（如"这张焊缝图有没有问题"），
  参考本 catalog 自动选择合适的 activity 编排成工作流。

每个 activity 标注:
  - capability: L2/L3 分派用的能力标识（design_workflow 生成 node 时用）
  - name: 业务名称（中文，给用户看）
  - description: 业务能力描述（给 LLM 判断该不该用）
  - implementation: "real" 真实执行 / "mock" 虚拟执行（占位返回）
  - inputs: 需要的输入参数
  - outputs: 产出什么数据
  - depends_on_hint: 典型依赖（如 PPA 依赖 IQA 报告）
  - aliases: capability 别名（design_workflow 可能用不同名）

Source: boundary-pinning §1.3/§1.5 — Activity Pool + 工业 MCP
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ActivityDescriptor:
    """单个 activity 的能力描述。"""
    capability: str                    # 主 capability 标识
    name: str                          # 业务名称（中文）
    description: str                   # 业务能力描述（给 LLM 判断用）
    implementation: str                # "real" | "mock"
    inputs: list[str] = field(default_factory=list)      # 输入参数名
    outputs: list[str] = field(default_factory=list)     # 产出数据字段
    depends_on_hint: list[str] = field(default_factory=list)  # 典型依赖的 capability
    aliases: list[str] = field(default_factory=list)     # capability 别名

    @property
    def status_label(self) -> str:
        """给 LLM 看的实现状态标签。"""
        return "✅ 已实现（真实执行）" if self.implementation == "real" else "⚠️ 未实现（虚拟执行占位）"


# ── 全部 activity 能力清单 ──────────────────────────────────────────
# 顺序按典型工作流编排顺序：检测 → 预处理 → 测量 → 识别 → 评审 → 标注
ACTIVITY_CATALOG: list[ActivityDescriptor] = [
    ActivityDescriptor(
        capability="defect_detection",
        name="图像质量评估 (IQA)",
        description=(
            "对焊缝图像进行质量检测：分辨率、曝光、对焦、完整性四项规则检查，"
            "可选触发多模态大模型深度视觉分析（识别反光/遮挡/镜头污染等）。"
            "产出路由决策：AUTO_PASS(自动通过) / SUGGEST_REVIEW(建议复核) / "
            "MANDATORY_REVIEW(必须复核) / REJECT(拒绝)。"
            "适用场景：用户问'这张图能不能用'/'质量行不行'/'有没有问题'。"
        ),
        implementation="real",
        inputs=["image_path"],
        outputs=["route_decision", "confidence", "issues", "checks"],
        depends_on_hint=[],
        aliases=["iqa", "image_quality"],
    ),
    ActivityDescriptor(
        capability="preprocess",
        name="图像预处理 (PPA)",
        description=(
            "根据 IQA 检测到的问题自动调整预处理策略：曝光异常→亮度/对比度校正，"
            "对焦模糊→降噪+锐化，裁切→边界填充，反光→去反光。"
            "适用场景：用户说'图片不清楚'/'预处理一下'/'增强图像'/'去噪'。"
            "注意：PPA 依赖 IQA 报告决定策略，必须先跑 IQA。"
        ),
        implementation="real",
        inputs=["image_path"],
        outputs=["strategies_applied", "issues_detected", "iqa_confidence"],
        depends_on_hint=["defect_detection"],
        aliases=["ppa", "image_preprocess", "enhance", "denoise"],
    ),
    ActivityDescriptor(
        capability="mea",
        name="几何测量 (MEA)",
        description=(
            "测量焊缝几何尺寸：焊缝宽度、余高、咬边深度、错边量等。"
            "适用场景：用户问'焊缝多宽'/'余高多少'/'尺寸合格吗'。"
        ),
        implementation="mock",
        inputs=["image_path"],
        outputs=["measurements", "dimensions"],
        depends_on_hint=["defect_detection"],
    ),
    ActivityDescriptor(
        capability="rda",
        name="缺陷识别 (RDA)",
        description=(
            "识别具体缺陷类型并定位：气孔、夹渣、裂纹、未熔合、咬边等。"
            "适用场景：用户问'有什么缺陷'/'哪里有问题'/'缺陷类型'。"
        ),
        implementation="mock",
        inputs=["image_path"],
        outputs=["defects", "locations", "defect_types"],
        depends_on_hint=["defect_detection"],
    ),
    ActivityDescriptor(
        capability="vda",
        name="视觉深度分析 (VDA)",
        description=(
            "基于视觉大模型的深度语义分析：识别非典型异常、上下文判断。"
            "比 IQA 内置的 MLLM 触发更全面，可独立调用。"
            "适用场景：用户问'这张图有什么异常'/'帮我仔细看看'。"
        ),
        implementation="mock",
        inputs=["image_path"],
        outputs=["anomalies", "findings", "semantic_analysis"],
        depends_on_hint=["defect_detection"],
    ),
    ActivityDescriptor(
        capability="rva",
        name="风险评估 (RVA)",
        description=(
            "综合缺陷检测结果评估焊缝风险等级：高/中/低风险，给出处置建议。"
            "适用场景：用户问'这个焊缝风险大吗'/'要不要返修'/'严重吗'。"
        ),
        implementation="mock",
        inputs=["defect_detection_result"],
        outputs=["risk_level", "recommendations", "severity"],
        depends_on_hint=["rda", "defect_detection"],
    ),
    ActivityDescriptor(
        capability="annotation",
        name="标注任务 (Label Studio)",
        description=(
            "通过 MCP 协议连接 Label Studio 标注平台（v2.0），支持 11 个 action：\n"
            "  复合 action（推荐）:\n"
            "    auto_annotate — 一键完成全链路：自动 list_datasets → get_dataset → create_job → "
            "upload_images(上传当前图片) → create_task → trigger_ai。适合'标注这张图'的场景。\n"
            "  单一 action（10 个 MCP tool，细粒度控制）:\n"
            "    list_datasets(列出数据集) / get_dataset(数据集详情) / "
            "create_job(创建作业，version_id/dataset_id 均可自动解析) / "
            "list_jobs(列出作业) / get_job(查看作业) / "
            "list_tasks(列出子任务) / create_task(创建标注任务) / "
            "trigger_ai(触发 AI 标注) / upload_images(上传图片) / "
            "assign_task(分配标注员)。\n"
            "input_data 中指定 action 字段选择执行哪个 action。"
            "适用场景：用户说'标注'/'打标'/'Label Studio'。"
        ),
        implementation="real",
        inputs=["action", "version_id|dataset_id", "job_id", "task_id", "images", "assignee_id", "name", "labels", "image_b64(auto_annotate)"],
        outputs=["task_id", "annotations", "download_url", "datasets", "jobs", "latest_version_id", "steps(auto_annotate)"],
        depends_on_hint=["defect_detection", "preprocess"],
        aliases=["label_studio", "annotate_label"],
    ),
    ActivityDescriptor(
        capability="mta",
        name="多模态评估 (MTA)",
        description=(
            "综合多源数据（图像+工艺参数+标准）做多维度评估。"
            "适用场景：用户问'综合评估一下'/'整体怎么样'。"
        ),
        implementation="mock",
        inputs=["image_path", "process_params"],
        outputs=["assessment", "score", "multi_modal_findings"],
        depends_on_hint=["defect_detection"],
    ),
    ActivityDescriptor(
        capability="hca",
        name="人工审核 (HCA)",
        description=(
            "人工审核节点：将结果推送给操作员复核，等待人工判定。"
            "适用场景：用户说'人工看看'/'复核'/'让人确认'。"
        ),
        implementation="mock",
        inputs=["review_request"],
        outputs=["review_decision", "reviewer_notes"],
        depends_on_hint=["defect_detection"],
        aliases=["human_review", "annotation_write"],
    ),
    ActivityDescriptor(
        capability="dsa_stats",
        name="数据集统计 (DSStats)",
        description=(
            "数据集统计与 CV 特征分析（轻量快照）。仅跑确定性算子，不调视觉大模型。"
            "覆盖：尺寸分布/质量分布/异常检测/重复检测/视角来源分布。"
            "有标注数据额外做标签统计和前景背景分析。"
            "适用场景：用户问'有多少张图'/'多少张模糊'/'尺寸多大'/'分布怎么样'。"
        ),
        implementation="real",
        inputs=["image_dir", "data_kind", "labels"],
        outputs=["cv_report", "label_stats", "summary"],
        depends_on_hint=[],
        aliases=["dataset_stats_snapshot", "dataset_quick_stats"],
    ),
    ActivityDescriptor(
        capability="dsa",
        name="数据集理解 (DSA)",
        description=(
            "数据集级统计分析与多模态语义理解。"
            "两类能力：(1) 统计与CV特征分析（尺寸分布/质量分布/异常检测/重复检测）；"
            "(2) 多模态语义理解（对象识别/缺陷类别/场景语义/数据偏差/预标注建议）。"
            "有标注数据额外做标签统计分析。"
            "适用场景：用户说'分析数据集'/'数据质量怎么样'/'数据分布'/'数据探查'。"
        ),
        implementation="real",
        inputs=["image_dir", "data_kind", "depth", "labels"],
        outputs=["cv_report", "label_stats", "semantic_understanding", "summary", "recommendations"],
        depends_on_hint=[],
        aliases=["dataset_analysis", "data_understanding", "dataset_stats"],
    ),
]


def get_catalog_text() -> str:
    """生成给 LLM 看的 catalog 文本（嵌入 system prompt 或 tool description）。

    格式：
      capability | 名称 | 状态 | 描述 | 输入 | 输出 | 典型依赖
    """
    lines = [
        "## L3 执行层能力清单（共 %d 个 activity）" % len(ACTIVITY_CATALOG),
        "",
        "LLM 在调用 design_workflow 时，应从下表选择合适的 activity 编排成工作流。",
        "已实现的 activity 会真实执行；未实现的会虚拟执行（返回占位结果，不报错）。",
        "用户是业务人员，不会明确指定 capability 名——LLM 需根据业务需求自动选择。",
        "",
    ]
    for desc in ACTIVITY_CATALOG:
        lines.append(f"### {desc.name}")
        lines.append(f"- capability: `{desc.capability}`" + (f" (别名: {', '.join(desc.aliases)})" if desc.aliases else ""))
        lines.append(f"- 状态: {desc.status_label}")
        lines.append(f"- 能力: {desc.description}")
        lines.append(f"- 输入: {', '.join(desc.inputs) if desc.inputs else '无'}")
        lines.append(f"- 输出: {', '.join(desc.outputs) if desc.outputs else '无'}")
        if desc.depends_on_hint:
            lines.append(f"- 典型依赖: {', '.join(desc.depends_on_hint)}")
        lines.append("")
    return "\n".join(lines)


def get_supported_capabilities() -> list[str]:
    """所有支持的 capability 名（含别名），供 design_workflow 校验。"""
    caps = []
    for desc in ACTIVITY_CATALOG:
        caps.append(desc.capability)
        caps.extend(desc.aliases)
    return caps


def find_descriptor(capability: str) -> ActivityDescriptor | None:
    """按 capability 名或别名查找描述符。"""
    for desc in ACTIVITY_CATALOG:
        if capability == desc.capability or capability in desc.aliases:
            return desc
    return None


__all__ = [
    "ActivityDescriptor",
    "ACTIVITY_CATALOG",
    "get_catalog_text",
    "get_supported_capabilities",
    "find_descriptor",
]
