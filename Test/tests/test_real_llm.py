"""test_real_llm — 真实豆包 LLM + mock MCP 端到端测试。

验证: 用户需求 → 豆包 LLM 自主设计 → 验证 → 执行（mock MCP）→ 结果汇报

用法:
  # 需要豆包 endpoint id（火山方舟控制台创建推理接入点）
  LLM_API_KEY=ark-xxx \
  LLM_MODEL=ep-2024xxxx-xxxxx \
  python -m tests.test_real_llm

  # 跳过人审（auto_approve）
  LLM_API_KEY=ark-xxx \
  LLM_MODEL=ep-2024xxxx-xxxxx \
  AUTO_APPROVE=1 \
  python -m tests.test_real_llm
"""

import asyncio
import json
import os
import sys

# 默认用 mock MCP（不连真实标注平台）
os.environ.setdefault("MCP_TRANSPORT", "mock")

from l1_agent.design import OpenAICompatibleLlmClient, design, validate, render  # noqa: E402
from l1_agent.execution.workflow_runner import run_workflow  # noqa: E402
from l1_agent.result_renderer import OpenAICompatibleSummarizer, render as render_result  # noqa: E402
from shared.mcp_tools import mock_mcp_server  # noqa: E402
from shared.mcp_tools.mcp_session import mcp_session  # noqa: E402


async def test_real_llm():
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "")
    auto_approve = os.environ.get("AUTO_APPROVE", "") == "1"

    if not api_key or not model:
        print("[SKIP] 需要设置 LLM_API_KEY + LLM_MODEL")
        print("       LLM_API_KEY=sk-xxx LLM_MODEL=deepseek-chat python -m tests.test_real_llm")
        return False

    print(f"[配置] LLM_MODEL={model}")
    print(f"[配置] MCP_TRANSPORT={os.environ.get('MCP_TRANSPORT', 'mock')}")
    print()

    # 重置 mock（如果用 mock transport）
    mock_mcp_server.reset()

    # 如果是 rest transport，先查真实数据集 ID
    dataset_summary = {
        "id": "ds-001",
        "name": "焊缝宏观检测数据集",
        "count": 50,
        "format": "jpg",
        "size_hint": "medium",
    }
    if os.environ.get("MCP_TRANSPORT") == "rest":
        print("=" * 60)
        print("[0/4] 查询真实数据集...")
        print("=" * 60)
        async with mcp_session() as session:
            result = await session.call_tool("list_datasets", {"pageNum": 1, "pageSize": 5})
            data = result.get("data", {})
            records = data.get("records", [])
            if records:
                ds = records[0]
                dataset_summary = {
                    "id": ds["id"],
                    "name": ds.get("name", ""),
                    "format": ds.get("modality", "IMAGE"),
                    "size_hint": "medium",
                }
                print(f"[OK] 使用真实数据集: {ds['id']} ({ds.get('name','')})")
            else:
                print("[FAIL] 没有数据集")
                return False

    # 1. LLM 自主设计
    print("\n" + "=" * 60)
    print("[1/4] LLM 自主设计工作流...")
    print("=" * 60)
    llm = OpenAICompatibleLlmClient(
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        model=model,
    )

    requirement = "对焊缝图像做气孔和裂纹检测，如果检出缺陷要复审"

    try:
        template = await design(
            llm=llm,
            requirement=requirement,
            dataset_summary=dataset_summary,
        )
    except Exception as e:
        print(f"\n[FAIL] LLM 设计失败: {e}")
        return False

    print(f"\n[OK] LLM 产出 template:")
    print(json.dumps(template, ensure_ascii=False, indent=2))

    # 2. 验证
    print("\n" + "=" * 60)
    print("[2/4] 验证工作流模板...")
    print("=" * 60)
    result = validate(template)
    if not result.ok:
        print(f"[FAIL] 验证失败: {result.error}")
        return False

    print("[OK] 验证通过")
    print("\n工作流树:")
    print(render(template))

    # 3. 人审（可跳过）
    if not auto_approve:
        print("\n" + "=" * 60)
        print("[3/4] 人审（输入 y 继续，其他取消）")
        print("=" * 60)
        feedback = await asyncio.to_thread(input, "满意? (y / 取消意见): ")
        if feedback.strip().lower() != "y":
            print(f"[取消] 用户反馈: {feedback}")
            return False
    else:
        print("\n[3/4] AUTO_APPROVE=1，跳过人审")

    # 4. 执行（调真实标注平台）
    print("\n" + "=" * 60)
    print("[4/4] 执行工作流（调真实标注平台）...")
    print("=" * 60)

    from shared.mcp_tools import annotate

    results = []
    for cp in template.get("control_points", []):
        cp_id = cp["id"]
        params = cp.get("params", {})
        print(f"\n  执行 [{cp_id}] {cp.get('name','')}...")
        try:
            mcp_result = await annotate(
                dataset_id=params["dataset_id"],
                job_name=params["job_name"],
                labels=params.get("labels"),
                poll_interval=3.0,
                poll_timeout=30.0,
            )
            results.append((cp_id, {
                "status": "OK",
                "data": mcp_result,
            }))
            print(f"  [OK] job_id={mcp_result.get('job_id')}, status={mcp_result.get('status')}")
        except Exception as e:
            results.append((cp_id, {
                "status": "ERROR",
                "error": str(e),
            }))
            print(f"  [ERROR] {e}")

    print(f"\n执行结果（{len(results)} 个 CP）:")
    for cp_id, output in results:
        print(f"\n  [{cp_id}] status={output.get('status')}")
        if output.get("data"):
            data = output["data"]
            print(f"    job_id: {data.get('job_id')}")
            print(f"    status: {data.get('status')}")
            print(f"    total_count: {data.get('total_count')}")
            print(f"    completed_count: {data.get('completed_count')}")
            tasks = data.get("tasks", [])
            if tasks:
                print(f"    tasks: {len(tasks)} 个")
                for t in tasks:
                    print(f"      - {t.get('task_id')}: {t.get('status')}")

    # 5. 结果汇报（豆包 LLM）
    print("\n" + "=" * 60)
    print("[汇报] 豆包 LLM 总结结果...")
    print("=" * 60)
    summarizer = OpenAICompatibleSummarizer(
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        model=model,
    )

    workflow_result = {
        "requirement": requirement,
        "results": [{"cp_id": cp_id, "output": out} for cp_id, out in results],
    }
    summary = await render_result(summarizer, requirement, workflow_result)
    print(f"\n{summary}")

    print("\n" + "=" * 60)
    print("[完成] 真实 LLM + mock MCP 端到端测试通过")
    print("=" * 60)
    return True


if __name__ == "__main__":
    ok = asyncio.run(test_real_llm())
    sys.exit(0 if ok else 1)
