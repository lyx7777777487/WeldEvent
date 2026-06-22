"""workflow_designer — 调 LLM function calling 产出 WorkflowTemplate dict。

LLM 通过 OpenAI 兼容接口接入（默认火山豆包方舟平台，也可换 DeepSeek/OpenAI 等）。
只暴露 1 个工具: emit_workflow_template。强制 tool_choice 让 LLM 必调。
LLM 产出的 dict 不直接信任，由 template_validator 三重验证 + human_reviewer 人审。
"""

import json
import logging
from typing import Any, Protocol

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是工业视觉标注系统的工作流设计助手。

用户会给你一个标注需求和数据集元数据，你需要自主设计一个工作流模板（WorkflowTemplate），
编排多个控制点（control_points）和转移（transitions）来完成这个需求。

## 可用的 Activity

当前只有 1 个 activity 可用：
  - annotation(dataset_id, job_name, labels) → 调外部标注平台（作业级），创建作业并触发 AI 标注
    参数:
      dataset_id: str          — 数据集 ID（用户会提供）
      job_name: str            — 作业名称（你根据需求起名）
      labels: list[str]        — 标签列表，JSON 数组，如 ["气孔","裂纹","夹渣"]

## 设计原则

1. 根据需求复杂度决定 CP 数量：
   - 简单需求（单一标注任务）→ 1 个 CP 即可
   - 复杂需求（多类标注、分阶段、需复审）→ 多个 CP + transitions 串联
2. 每个 CP 的 params 会原样传给绑定的 activity，只放该 activity 需要的参数
3. transitions 描述 CP 之间的流转：
   - 简单串联: from_cp=cp1, branches=[{condition:"default", to_cp:cp2}]
   - 条件分支: from_cp=cp1, branches=[{condition:"ok", to_cp:cp2}, {condition:"ng", to_cp:cp3}]
4. labels 必须是 JSON 数组，不要用逗号分隔字符串

## WorkflowTemplate 结构

{
  "id": "<有意义的名称，如 weld_annotation_v1>",
  "version": "1",
  "entry_point": "<必须是 control_points 里某个 id>",
  "control_points": [
    {
      "id": "cp1",
      "name": "<人类可读的名字>",
      "activity_binding": {"activity_name": "annotation"},
      "execution_policy": {},
      "params": {
        "dataset_id": "<数据集 ID>",
        "job_name": "<作业名称>",
        "labels": ["<标签1>", "<标签2>"]
      }
    }
  ],
  "transitions": [
    {"from_cp": "cp1", "branches": [{"condition": "default", "to_cp": "cp2"}]}
  ]
}

## 示例

需求："对焊缝图做气孔和裂纹检测，如果检出缺陷要复审"
设计：
  - cp1: 标注作业（labels=["气孔","裂纹"]）
  - cp2: 复审作业（labels=["确认","误报"]）
  - transition: cp1 →(default)→ cp2

必须调用 emit_workflow_template 工具提交你的设计。"""

EMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_workflow_template",
        "description": "提交你设计的 workflow template。schema 见 system prompt，不要改字段名。",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "version": {"type": "string"},
                "entry_point": {"type": "string"},
                "control_points": {"type": "array"},
                "transitions": {"type": "array"},
            },
            "required": ["id", "version", "entry_point", "control_points"],
        },
    },
}


class LlmClient(Protocol):
    """LLM 抽象 — 测试时可注入 mock。"""

    async def chat_with_tool(
        self, system: str, user: str, tools: list[dict], tool_choice: dict
    ) -> dict: ...


class OpenAICompatibleLlmClient:
    """OpenAI 兼容接口的 LLM 实现（火山豆包 / DeepSeek / OpenAI 等通用）。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def chat_with_tool(
        self, system: str, user: str, tools: list[dict], tool_choice: dict
    ) -> dict:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=tools,
            tool_choice=tool_choice,
        )
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            return {"error": "LLM 未调用工具", "content": msg.content or ""}
        args = tool_calls[0].function.arguments
        try:
            return {"template": json.loads(args)}
        except json.JSONDecodeError as e:
            return {"error": f"LLM 工具参数不是合法 JSON: {e}", "raw": args}


async def design(
    llm: LlmClient,
    requirement: str,
    dataset_summary: dict,
    history: list[dict] | None = None,
) -> dict:
    """调 LLM 产出 template dict。history 是前几轮的反馈，用于修正。

    返回 LLM 原始产出（未验证）。调用方应过 template_validator。
    """
    user_msg = _build_user_message(requirement, dataset_summary, history or [])
    tool_choice = {"type": "function", "function": {"name": "emit_workflow_template"}}

    result = await llm.chat_with_tool(SYSTEM_PROMPT, user_msg, [EMIT_TOOL], tool_choice)

    if "error" in result:
        logger.warning("LLM 设计失败: %s", result["error"])
        raise LlmDesignError(result["error"])

    template = result["template"]
    logger.info("LLM 产出 template: id=%s", template.get("id"))
    return template


def _build_user_message(
    requirement: str, dataset_summary: dict, history: list[dict]
) -> str:
    parts = [f"用户需求: {requirement}", f"数据集: {json.dumps(dataset_summary, ensure_ascii=False)}"]
    if history:
        parts.append("前几轮反馈（请据此修正）:")
        for h in history:
            role = h.get("role", "user")
            content = h.get("content", "")
            parts.append(f"  [{role}] {content}")
    return "\n".join(parts)


class LlmDesignError(Exception):
    """LLM 调用或产出失败。"""
