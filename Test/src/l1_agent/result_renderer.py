"""result_renderer — summarize 类 LLM 调用，把 workflow 结果 dict 讲给 user 听。

整个流程固定 1 次（见 Test/docs/03-l1-agent-design.md）。
"""

import json
import logging
from typing import Protocol

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是工业视觉标注系统的结果汇报助手。
用户给你一个 workflow 的执行结果 dict，你用 1-3 句话告诉用户：
  - 标注了什么
  - 结果如何（成功/失败）
  - 关键标签或异常
不要复述 JSON。"""


class SummarizeLlm(Protocol):
    async def summarize(self, system: str, user: str) -> str: ...


class OpenAICompatibleSummarizer:
    """OpenAI 兼容接口的 summarize 实现（火山豆包 / DeepSeek / OpenAI 等通用）。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def summarize(self, system: str, user: str) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


async def render(
    summarizer: SummarizeLlm,
    requirement: str,
    workflow_result: dict,
) -> str:
    user_msg = (
        f"原始需求: {requirement}\n"
        f"workflow 结果: {json.dumps(workflow_result, ensure_ascii=False, indent=2)}"
    )
    return await summarizer.summarize(SYSTEM_PROMPT, user_msg)