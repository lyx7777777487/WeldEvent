"""design_phase — 设计期主循环。

两个独立计数器（见 Test/docs/03-l1-agent-design.md）：
  - schema_retries：连续 schema 验证失败，上限 max_schema_retries
  - review_rounds：人审驳回轮数，上限 max_design_rounds
"""

import logging

from l1_agent.design import (
    design,
    validate,
    render,
    ask_feedback,
    LlmClient,
    LlmDesignError,
)

logger = logging.getLogger(__name__)


class DesignTimeoutError(Exception):
    """人审驳回超过最大轮数。"""


class SchemaRetryExhausted(Exception):
    """LLM 连续 schema 失败超过上限。"""


async def design_phase(
    llm: LlmClient,
    requirement: str,
    dataset_summary: dict,
    max_design_rounds: int = 5,
    max_schema_retries: int = 3,
    auto_approve: bool = False,
) -> dict:
    """设计期主循环。

    auto_approve=True 时跳过人审 input()，第一次 schema 通过即返回。
    用于测试环境（无法走 stdin）和 CI。
    """
    history: list[dict] = []
    schema_retries = 0

    for review_round in range(max_design_rounds):
        try:
            tpl = await design(llm, requirement, dataset_summary, history)
        except LlmDesignError as e:
            schema_retries += 1
            if schema_retries > max_schema_retries:
                raise SchemaRetryExhausted(
                    f"LLM 连续 {schema_retries} 次失败: {e}"
                ) from e
            history.append({"role": "system", "content": f"LLM 调用失败（第 {schema_retries} 次）: {e}"})
            continue

        validation = validate(tpl)
        if not validation.ok:
            schema_retries += 1
            if schema_retries > max_schema_retries:
                raise SchemaRetryExhausted(
                    f"LLM 连续 {schema_retries} 次 schema 失败: {validation.error}"
                )
            history.append({
                "role": "system",
                "content": f"schema 错误（第 {schema_retries} 次）: {validation.error}",
            })
            continue
        schema_retries = 0

        print(render(tpl))
        if auto_approve:
            return tpl
        feedback = await ask_feedback()
        if feedback.strip().lower() == "y":
            return tpl
        history.append({"role": "user", "content": feedback})

    raise DesignTimeoutError("超过最大人审轮数仍未达成共识")