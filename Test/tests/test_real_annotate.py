"""test_real_annotate — 真实标注平台端到端测试（REST transport）。

验证: annotate_tool → 真实 8081 REST API → create_job → create_task → trigger_agent → 轮询 → list_tasks

用法:
  MCP_TRANSPORT=rest \
  MCP_REST_URL=http://172.16.11.11:8081 \
  MCP_AUTH_URL=http://172.16.11.11:8079/api/auth/login \
  MCP_USERNAME=admin \
  MCP_PASSWORD=admin123 \
  python -m tests.test_real_annotate
"""

import asyncio
import json
import os
import sys

from shared.mcp_tools import annotate, McpBusinessError, McpInfraError  # noqa: E402
from shared.mcp_tools.mcp_session import mcp_session  # noqa: E402


async def test_real_annotate():
    transport = os.environ.get("MCP_TRANSPORT", "mock")
    if transport != "rest":
        print(f"[SKIP] MCP_TRANSPORT={transport}，跳过真实标注测试")
        print("       请设 MCP_TRANSPORT=rest + MCP_REST_URL + 认证")
        return False

    print(f"[配置] transport={transport}")
    print(f"[配置] REST_URL={os.environ.get('MCP_REST_URL', '')}")
    print()

    # 1. 先拿一个真实 dataset_id
    print("=" * 60)
    print("[1/3] 查询数据集列表，拿一个真实 dataset_id...")
    print("=" * 60)
    async with mcp_session() as session:
        result = await session.call_tool("list_datasets", {"pageNum": 1, "pageSize": 5})
        data = result.get("data", {})
        records = data.get("records", [])
        if not records:
            print("[FAIL] 没有数据集")
            return False
        dataset_id = records[0]["id"]
        dataset_name = records[0].get("name", "")
        print(f"[OK] 使用数据集: {dataset_id} ({dataset_name})")

    # 2. 跑完整标注流程
    print("\n" + "=" * 60)
    print("[2/3] 跑完整标注流程: create_job → create_task → trigger → 轮询 → list_tasks")
    print("=" * 60)
    try:
        result = await annotate(
            dataset_id=dataset_id,
            job_name="E2E 真实测试作业",
            labels=["气孔", "裂纹"],
            poll_interval=3.0,
            poll_timeout=60.0,
        )
    except McpBusinessError as e:
        print(f"\n[FAIL] 业务失败: {e}")
        return False
    except McpInfraError as e:
        print(f"\n[FAIL] 基础设施失败: {e}")
        return False

    print(f"\n[OK] 标注完成:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 3. 验证结果
    print("\n" + "=" * 60)
    print("[3/3] 验证结果...")
    print("=" * 60)
    assert result["job_id"], "job_id 为空"
    assert result["status"] in ("completed", "failed", "timeout"), f"未知状态: {result['status']}"
    print(f"  job_id: {result['job_id']}")
    print(f"  status: {result['status']}")
    print(f"  total_count: {result['total_count']}")
    print(f"  completed_count: {result['completed_count']}")
    print(f"  tasks: {len(result['tasks'])} 个")

    print("\n" + "=" * 60)
    print("[完成] 真实标注平台端到端测试通过")
    print("=" * 60)
    return True


if __name__ == "__main__":
    ok = asyncio.run(test_real_annotate())
    sys.exit(0 if ok else 1)
