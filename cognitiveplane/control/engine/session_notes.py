"""Session notes derivation — per-iteration context notes for LLM.

Extracted from react.py. Contains:
  - TOOL_TIMEOUT_SECONDS constant
  - derive_note (was ReActEngine._derive_note)
  - derive_failure_reflection (was ReActEngine._derive_failure_reflection)
  - build_progress_note (was ReActEngine._build_progress_note)
"""

from typing import Any

from cognitiveplane.control.tools import ToolResult


# Plan §4.2.2 line 819-822: ratchet trigger thresholds
TOOL_TIMEOUT_SECONDS = 180.0  # vision API 可能 60s+重试，留足空间避免误杀


def derive_note(
    tool_name: str,
    result: ToolResult,
    arguments: dict[str, Any],
) -> str | None:
    """从工具执行结果推断一行简短笔记（增强 A：Session Notes）。

    Was ReActEngine._derive_note.

    Anthropic Context Engineering 借鉴：每轮 agent 执行后生成简短笔记，
    存入 session["notes"]，下一轮注入 system prompt。解决 LLM 跨轮"失忆"
    ——不知道自己 3 轮前分析过什么、得出了什么结论。

    设计原则：
    - 规则化推断，零额外 LLM 调用（成本敏感）
    - 失败优先标注（⚠️ 前缀），让 LLM 注意到问题
    - 笔记简短（≤80 字），避免污染上下文
    - 与 current_plan 互补：current_plan 只存 design_workflow 结果，
      notes 存所有轮的简短总结

    返回 None 表示该工具不生成笔记（如 archive_memory 等无状态工具）。
    """
    if result.error:
        # 不截断错误信息 — LLM 需要完整错误才能正确判断原因
        # (如 version_id 缺失 vs 权限不足 vs 网络错误 处理方式完全不同)
        return f"⚠️ {tool_name} 失败：{result.error}"

    output = result.output or {}

    if tool_name == "analyze_image":
        summary = output.get("summary") or output.get("description") or ""
        defects = (
            output.get("defects")
            or output.get("defect_types")
            or output.get("issues")
            or []
        )
        if isinstance(defects, list) and defects:
            return f"已分析图片：发现 {', '.join(str(d)[:20] for d in defects[:3])}"
        if summary:
            return f"已分析图片：{str(summary)[:60]}"
        return "已分析图片（无明显缺陷）"

    if tool_name == "design_workflow":
        wf_id = output.get("workflow_id", "?")
        spec = output.get("workflow_spec", {}) or {}
        nodes = spec.get("nodes", []) or []
        return f"已设计方案（{len(nodes)}节点, id={wf_id}）→ 待 launch_workflow 启动"

    if tool_name == "launch_workflow":
        wf_id = output.get("workflow_id", "?")
        status = output.get("status", "?")
        node_results = output.get("node_results", []) or []
        failed = [n for n in node_results
                  if isinstance(n, dict) and n.get("status") in ("failed", "FAILED")]
        if failed:
            failed_names = [n.get("node_id", "?") for n in failed[:3]]
            return f"⚠️ 工作流 {wf_id} 部分失败：{', '.join(failed_names)}"
        return f"工作流 {wf_id} 已执行（{status}）"

    if tool_name in ("search_standards", "search_cases", "search_process"):
        items = (
            output.get("standards")
            or output.get("cases")
            or output.get("results")
            or output.get("processes")
            or []
        )
        count = len(items) if isinstance(items, list) else "?"
        return f"已查询 {tool_name}：{count} 条结果"

    if tool_name == "read_weldmap":
        progress = output.get("progress") or output.get("status", "")
        return f"已读 WeldMap：{str(progress)[:60]}"

    if tool_name == "escalate":
        reason = output.get("reason", "")
        return f"已升级人工：{str(reason)[:60]}"

    if tool_name == "explain_decision":
        return f"已解释决策：{str(output.get('summary', ''))[:60]}"

    if tool_name == "web_search":
        results = output.get("results", []) or []
        return f"已网络搜索：{len(results) if isinstance(results, list) else '?'} 条结果"

    if tool_name == "request_confirmation":
        # 持久化用户选择 — 避免下一轮 LLM 失忆反复问同样问题。
        # 用户选的值在 output["user_selection"]，问题在 output["question"]。
        user_sel = output.get("user_selection", "")
        question = output.get("question", "")
        status = output.get("status", "")
        if status == "timeout":
            return f"⚠️ 用户未响应确认：{question[:50]}"
        if user_sel:
            return f"✅ 用户确认：{question[:40]} → {user_sel}"
        return None

    if tool_name in ("list_datasets", "get_dataset", "list_jobs", "get_job", "list_tasks"):
        # 查询类工具记结果摘要,避免 LLM 下一轮重复查同样信息。
        if tool_name == "list_datasets":
            records = output.get("records", []) or output.get("datasets", []) or []
            return f"已查数据集列表：{len(records) if isinstance(records, list) else '?'} 个"
        if tool_name == "get_dataset":
            name = output.get("name", output.get("datasetName", ""))
            ver = output.get("latestVersionId", output.get("versionId", ""))
            ds_id = output.get("id", output.get("datasetId", ""))
            return f"已查数据集详情：{name} dataset_id={ds_id} version_id={ver}"
        if tool_name == "list_jobs":
            jobs = output.get("jobs", []) or output.get("records", []) or []
            # 记第一个 job 的 id，方便 LLM 下轮查详情
            first_job_id = ""
            if isinstance(jobs, list) and jobs:
                first_job_id = jobs[0].get("id", jobs[0].get("jobId", ""))
            return f"已查作业列表：{len(jobs) if isinstance(jobs, list) else '?'} 个, first_job_id={first_job_id}"
        if tool_name == "get_job":
            job_id = output.get("id", output.get("jobId", output.get("job_id", "")))
            status = output.get("status", "")
            total = output.get("totalCount", output.get("total", "?"))
            completed = output.get("completedCount", output.get("completed", "?"))
            return f"已查作业详情：job_id={job_id} status={status} 进度={completed}/{total}"
        if tool_name == "list_tasks":
            tasks = output.get("tasks", []) or output.get("records", []) or []
            first_task_id = ""
            if isinstance(tasks, list) and tasks:
                first_task_id = tasks[0].get("id", tasks[0].get("taskId", ""))
            return f"已查任务列表：{len(tasks) if isinstance(tasks, list) else '?'} 个, first_task_id={first_task_id}"
        return f"已查询 {tool_name}"

    # 写入类工具 — 必须记下产出的 ID（job_id / task_id 等），
    # 否则下一轮 LLM 不知道 job_id 是什么，可能用 task_id 或 dataset_id
    # 当 job_id 调 list_tasks/get_job，触发 404: 标注作业不存在。
    if tool_name == "create_job":
        job_id = output.get("job_id", output.get("id", output.get("jobId", "")))
        name = output.get("name", output.get("job_name", ""))
        return f"✅ 已创建作业：{name} job_id={job_id}"
    if tool_name == "create_task":
        task_id = output.get("task_id", output.get("id", output.get("taskId", "")))
        job_id = output.get("job_id", output.get("jobId", ""))
        return f"✅ 已创建任务：task_id={task_id} (job_id={job_id})"
    if tool_name == "upload_image_to_dataset":
        ver = output.get("version_id", "")
        fname = output.get("filename", "")
        return f"✅ 已上传图片：{fname} → version_id={ver}"
    if tool_name == "upload_images":
        ver = output.get("version_id", "")
        return f"✅ 已上传图片 → version_id={ver}"
    if tool_name == "assign_task":
        task_id = output.get("task_id", output.get("taskId", ""))
        assignee = output.get("assignee", output.get("user_id", output.get("userId", "")))
        return f"✅ 已分配任务：task_id={task_id} → {assignee}"
    if tool_name == "trigger_ai":
        task_id = output.get("task_id", output.get("taskId", ""))
        status = output.get("status", "triggered")
        return f"✅ 已触发AI预标注：task_id={task_id} status={status}"

    # archive_memory 等无状态工具不生成笔记
    return None


def derive_failure_reflection(
    tool_name: str,
    result: ToolResult,
    arguments: dict[str, Any],
) -> str | None:
    """失败反思（增强 B）—— 借鉴 Anthropic "give Claude a way to verify"。

    Was ReActEngine._derive_failure_reflection.

    工具失败时生成"verification gate"——下一轮 LLM 必须满足的条件。
    如 IQA 失败（图像模糊）→ "后续 PPA 必须启用锐化，标注节点应降低置信度"。

    与 derive_note 的区别：derive_note 生成事实性笔记（"已分析图片"），
    derive_failure_reflection 生成指导性建议（"下次应该 X"）。
    两者都注入 session["notes"]，但 reflection 只在失败 + 卡住时生成。
    """
    if not result.error:
        return None

    error = (result.error or "").lower()
    error_type = (result.error_type or "").lower()

    # 按工具 + 错误类型生成具体建议
    if tool_name == "analyze_image":
        if "blur" in error or "模糊" in error or "laplacian" in error:
            return ("图像模糊（Laplacian<50），后续 PPA 必须启用锐化，"
                    "标注节点应降低置信度或跳过")
        if "timeout" in error or "超时" in error:
            return "视觉 API 超时，建议重试或换图，不要继续 design_workflow"
        if "limit" in error or "限流" in error:
            return "视觉 API 限流，建议等待或换工具，不要立即重试"

    if "ppa" in tool_name.lower() or "preprocess" in tool_name.lower():
        if "iqa" in error or "report" in error:
            return ("PPA 依赖 IQA 报告但未获取，IQA 失败导致 PPA 无法调整策略。"
                    "应先解决 IQA 或在 design_workflow 中跳过 PPA 节点")

    if "annotation" in tool_name.lower() or "label" in tool_name.lower():
        if "version_id" in error:
            return ("标注需 version_id，应在 design_workflow 时指定，"
                    "或回退补设 version_id 后重新创建标注作业")

    if "schema" in error_type or "schema" in error:
        return f"参数校验失败，请检查 {tool_name} 的参数 schema 后重试"

    if "timeout" in error_type or "timeout" in error:
        return (f"{tool_name} 超时（>{int(TOOL_TIMEOUT_SECONDS)}s），"
                "建议简化任务或换工具")

    # 通用失败反思
    return f"{tool_name} 失败（{result.error_type or 'unknown'}），建议检查参数或换策略"


def build_progress_note(iteration: int, tools_used: list[str], max_iterations: int) -> str:
    """生成进度 system note（增强 E）。

    Was ReActEngine._build_progress_note. Now takes max_iterations as a
    parameter instead of reading self._max_iterations.

    Anthropic "course-correct early and often" + context management 借鉴。
    让 LLM 知道当前迭代进度 + 已用工具，剩余轮次少时警告优先完成核心任务。
    防止 LLM 在最后一轮还开始复杂工作流导致中途截断。

    每轮追加一条简短 system note（<100 字），膨胀由 ContextCompaction 处理。
    """
    remaining = max_iterations - iteration - 1
    used = ", ".join(tools_used[-3:]) if tools_used else "无"
    note = f"[进度] 第{iteration+1}/{max_iterations}轮，最近工具: {used}"
    if remaining <= 3:
        note += f" ⚠️ 仅剩{remaining}轮，优先完成核心任务，不要开始新工作流设计"
    return note
