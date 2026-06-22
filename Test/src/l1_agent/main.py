"""L1 Agent CLI 入口。

用法:
  python -m l1_agent.main "对这批焊缝图做宏观标注" /path/to/dataset

流程（见 Test/docs/03-l1-agent-design.md）:
  1. 设计期: LLM 生成 template → 三重验证 → 人审 → 循环
  2. 运行期: save template → 启动 Temporal workflow → 等结果
  3. summarize: 把结果讲给 user 听（固定 1 次 LLM 调用）

环境变量:
  LLM_API_KEY          — 必填（火山豆包方舟平台 API Key）
  LLM_BASE_URL         — 默认 https://ark.cn-beijing.volces.com/api/v3
  LLM_MODEL            — 默认 doubao-1.5-pro-32k
  TEMPORAL_HOST        — 默认 localhost:7233
  MCP_TRANSPORT        — mock / http（默认 mock）
  TEMPLATE_REPO_ROOT   — 默认 templates
"""

import asyncio
import logging
import sys

from l1_agent.config import L1Config
from l1_agent.dataset import summarize_dataset
from l1_agent.design import OpenAICompatibleLlmClient
from l1_agent.design_phase import design_phase, DesignTimeoutError, SchemaRetryExhausted
from l1_agent.execution import WorkflowRunner, connect_temporal
from l1_agent.result_renderer import OpenAICompatibleSummarizer, render as render_result
from shared.template_repository import FileTemplateRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("l1_agent")


async def main(requirement: str, dataset_path: str) -> str:
    config = L1Config.from_env()
    if not config.llm_api_key:
        raise SystemExit("LLM_API_KEY 未设置")

    dataset_summary = summarize_dataset(dataset_path)
    logger.info("dataset: %s", dataset_summary)

    llm = OpenAICompatibleLlmClient(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
        model=config.llm_model,
    )

    # 1. 设计期
    template = await design_phase(
        llm=llm,
        requirement=requirement,
        dataset_summary=dataset_summary,
        max_design_rounds=config.max_design_rounds,
        max_schema_retries=config.max_schema_retries,
    )
    logger.info("template 定稿: %s@%s", template["id"], template.get("version", "1"))

    # 2. 运行期
    temporal_client = await connect_temporal(config.temporal_host, config.temporal_namespace)
    repository = FileTemplateRepository(root_dir=config.template_repo_root)
    runner = WorkflowRunner(temporal_client, repository, task_queue=config.task_queue)

    handle = await runner.start(template)
    result = await runner.wait_result(handle)
    logger.info("workflow 完成: %s", handle.id)

    # 3. summarize
    summarizer = OpenAICompatibleSummarizer(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
        model=config.llm_model,
    )
    return await render_result(summarizer, requirement, result)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python -m l1_agent.main \"<需求>\" <dataset_path>", file=sys.stderr)
        sys.exit(1)
    try:
        output = asyncio.run(main(sys.argv[1], sys.argv[2]))
        print("\n" + output)
    except (DesignTimeoutError, SchemaRetryExhausted) as e:
        print(f"\n设计期失败: {e}", file=sys.stderr)
        sys.exit(2)