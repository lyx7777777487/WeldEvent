"""连通性测试 — 验证标注平台能连上、能调工具。

用法:
  # 方式 1: REST transport（推荐，直接调 8081 REST API，不依赖 MCP SDK）
  MCP_TRANSPORT=rest \
  MCP_REST_URL=http://172.16.11.11:8081 \
  MCP_AUTH_URL=http://172.16.11.11:8079/api/auth/login \
  MCP_USERNAME=admin \
  MCP_PASSWORD=admin123 \
  python -m tests.test_mcp_connectivity

  # 方式 2: 直接传 JWT token
  MCP_TRANSPORT=rest \
  MCP_REST_URL=http://172.16.11.11:8081 \
  MCP_JWT_TOKEN=你的token \
  python -m tests.test_mcp_connectivity

  # 方式 3: MCP transport（需 MCP server，当前不可用）
  MCP_TRANSPORT=http \
  MCP_SERVER_URL=http://172.16.11.11:8079/api/mcp/ \
  MCP_JWT_TOKEN=你的token \
  python -m tests.test_mcp_connectivity
"""

import asyncio
import json
import os
import sys

from shared.mcp_tools.mcp_session import mcp_session, McpSessionError, McpInfraError, McpBusinessError


async def test_connectivity():
    """测试 3 步: 连接 → list_datasets → create_job（不 trigger）"""
    transport = os.environ.get("MCP_TRANSPORT", "mock")
    if transport == "mock":
        print("[SKIP] MCP_TRANSPORT=mock，跳过真实连通性测试")
        print("       请设 MCP_TRANSPORT=rest + MCP_REST_URL + 认证")
        return False

    if transport == "rest":
        url = os.environ.get("MCP_REST_URL", "http://172.16.11.11:8081")
    else:
        url = os.environ.get("MCP_SERVER_URL", "")
    print(f"[1/4] 连接标注平台 ({transport}): {url}")

    try:
        async with mcp_session() as session:
            print("      ✅ 连接成功")

            # 测试 1: list_datasets
            print("[2/4] 调 list_datasets...")
            result = await session.call_tool("list_datasets", {"pageNum": 1, "pageSize": 5})
            print(f"      ✅ 返回: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")

            # 从 list_datasets 结果里取第一个真实 dataset_id
            data = result.get("data", result) if isinstance(result, dict) else {}
            records = data.get("records", []) if isinstance(data, dict) else []
            if not records:
                print("\n⚠️ 没有数据集，跳过 create_job 测试")
                print("🎉 连接 + list_datasets 通过！")
                return True
            dataset_id = records[0].get("id", "")
            print(f"      使用数据集: {dataset_id}")

            # 测试 2: create_job（用真实 dataset_id，不 trigger）
            print("[3/4] 调 create_job（测试作业，不触发标注）...")
            job_result = await session.call_tool("create_job", {
                "datasetId": dataset_id,
                "name": "连通性测试作业",
                "labels": ["气孔"],
                "annotationType": "CLASSIFICATION",
                "platform": "LABEL_STUDIO",
            })
            print(f"      ✅ 返回: {json.dumps(job_result, ensure_ascii=False, indent=2)}")

            # 测试 3: get_job
            job_data = job_result.get("data", job_result) if isinstance(job_result, dict) else {}
            job_id = job_data.get("jobId") or job_data.get("id", "") if isinstance(job_data, dict) else ""
            if job_id:
                print(f"[4/4] 调 get_job(jobId={job_id})...")
                job_info = await session.call_tool("get_job", {"id": job_id})
                print(f"      ✅ 返回: {json.dumps(job_info, ensure_ascii=False, indent=2)[:500]}")

            print("\n🎉 全部通过！标注平台连通性正常。")
            return True

    except McpSessionError as e:
        print(f"\n❌ 连接失败: {e}")
        print("   检查: MCP_SERVER_URL 是否正确、MCP_JWT_TOKEN 是否有效")
        return False
    except McpBusinessError as e:
        print(f"\n⚠️ 连接成功但业务失败: {e}")
        print("   检查: dataset_id 是否存在、token 权限是否够")
        return False
    except McpInfraError as e:
        print(f"\n❌ 基础设施失败: {e}")
        print("   检查: 网络是否通、MCP server 是否在线")
        return False
    except Exception as e:
        print(f"\n❌ 未知错误: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    ok = asyncio.run(test_connectivity())
    sys.exit(0 if ok else 1)
