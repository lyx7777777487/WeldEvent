"""test_all_tools — 逐个测试所有 20 个标注平台接口。

用 mock transport 测试所有工具的完整流程，确保每个工具都能正确调用和返回。
然后用 REST transport 测试真实平台（只测读操作 + create_job，不破坏数据）。

用法:
  # mock 测试（测全部 20 个工具）
  MCP_TRANSPORT=mock python -m tests.test_all_tools

  # 真实平台测试（只测读操作 + create_job）
  MCP_TRANSPORT=rest \
  MCP_REST_URL=http://172.16.11.11:8081 \
  MCP_AUTH_URL=http://172.16.11.11:8079/api/auth/login \
  MCP_USERNAME=admin MCP_PASSWORD=admin123 \
  python -m tests.test_all_tools
"""

import asyncio
import json
import os
import sys

from shared.mcp_tools.mcp_session import mcp_session
from shared.mcp_tools.annotate_tool import _unwrap_api_response, _extract_id, McpBusinessError

passed = 0
failed = 0


def ok(name, result=None):
    global passed
    passed += 1
    msg = f"  [OK] {name}"
    if result:
        # 简短显示
        s = json.dumps(result, ensure_ascii=False, default=str)
        if len(s) > 120:
            s = s[:120] + "..."
        msg += f" → {s}"
    print(msg)


def fail(name, err):
    global failed
    failed += 1
    print(f"  [FAIL] {name} → {err}")


async def call(session, name, args=None):
    """调用工具并解包 ApiResponse。"""
    result = await session.call_tool(name, args or {})
    return _unwrap_api_response(result, name)


async def test_mock():
    """mock transport: 测全部 20 个工具。"""
    from shared.mcp_tools import mock_mcp_server

    print("=" * 60)
    print("Mock Transport — 测试全部 20 个工具")
    print("=" * 60)

    mock_mcp_server.reset()

    async with mcp_session() as s:
        # ===== 数据集 =====
        print("\n--- 数据集 ---")

        # 1. list_datasets
        try:
            r = await call(s, "list_datasets", {"pageNum": 1, "pageSize": 10})
            assert r.get("total", 0) >= 1
            ok("list_datasets", {"total": r["total"]})
        except Exception as e:
            fail("list_datasets", e)

        ds_id = "ds-001"

        # 2. get_dataset
        try:
            r = await call(s, "get_dataset", {"id": ds_id})
            assert r["id"] == ds_id
            ok("get_dataset", {"id": r["id"], "name": r["name"]})
        except Exception as e:
            fail("get_dataset", e)

        # 3. create_dataset
        new_ds_id = None
        try:
            r = await call(s, "create_dataset", {"name": "测试数据集", "modality": "IMAGE"})
            new_ds_id = r["id"]
            assert new_ds_id
            ok("create_dataset", {"id": new_ds_id})
        except Exception as e:
            fail("create_dataset", e)

        # 4. list_dataset_images
        try:
            r = await call(s, "list_dataset_images", {"id": ds_id})
            ok("list_dataset_images", {"total": r.get("total", 0)})
        except Exception as e:
            fail("list_dataset_images", e)

        # 5. upload_dataset_images
        try:
            r = await call(s, "upload_dataset_images", {"id": ds_id, "files": ["img1.jpg", "img2.jpg"]})
            assert r["uploaded"] == 2
            ok("upload_dataset_images", {"uploaded": r["uploaded"]})
        except Exception as e:
            fail("upload_dataset_images", e)

        # 6. list_dataset_images（验证上传后）
        try:
            r = await call(s, "list_dataset_images", {"id": ds_id})
            assert r.get("total", 0) == 2
            ok("list_dataset_images (after upload)", {"total": r["total"]})
        except Exception as e:
            fail("list_dataset_images (after upload)", e)

        # ===== 作业 =====
        print("\n--- 作业 ---")

        # 7. create_job
        job_id = None
        try:
            r = await call(s, "create_job", {
                "datasetId": ds_id, "name": "测试作业",
                "labels": ["气孔"], "annotationType": "CLASSIFICATION", "platform": "LABEL_STUDIO",
            })
            job_id = _extract_id(r, "jobId", "id")
            assert job_id
            ok("create_job", {"job_id": job_id})
        except Exception as e:
            fail("create_job", e)

        # 8. get_job
        try:
            r = await call(s, "get_job", {"id": job_id})
            assert r["id"] == job_id
            ok("get_job", {"id": r["id"], "status": r["status"]})
        except Exception as e:
            fail("get_job", e)

        # 9. list_jobs
        try:
            r = await call(s, "list_jobs", {"datasetId": ds_id})
            assert r.get("total", 0) >= 1
            ok("list_jobs", {"total": r["total"]})
        except Exception as e:
            fail("list_jobs", e)

        # ===== 任务 =====
        print("\n--- 任务 ---")

        # 10. create_task
        task_id = None
        try:
            r = await call(s, "create_task", {"jobId": job_id})
            task_id = _extract_id(r, "taskId", "id")
            assert task_id
            ok("create_task", {"task_id": task_id})
        except Exception as e:
            fail("create_task", e)

        # 11. list_tasks
        try:
            r = await call(s, "list_tasks", {"jobId": job_id})
            assert r.get("total", 0) >= 1
            ok("list_tasks", {"total": r["total"]})
        except Exception as e:
            fail("list_tasks", e)

        # 12. list_all_tasks
        try:
            r = await call(s, "list_all_tasks", {"pageNum": 1, "pageSize": 10})
            assert r.get("total", 0) >= 1
            ok("list_all_tasks", {"total": r["total"]})
        except Exception as e:
            fail("list_all_tasks", e)

        # 13. get_task
        try:
            r = await call(s, "get_task", {"id": task_id})
            assert r["id"] == task_id
            ok("get_task", {"id": r["id"], "status": r["status"]})
        except Exception as e:
            fail("get_task", e)

        # 14. assign_task
        try:
            r = await call(s, "assign_task", {"taskId": task_id, "assigneeId": "user-002", "assigneeName": "张三"})
            ok("assign_task")
        except Exception as e:
            fail("assign_task", e)

        # 15. start_label_item
        try:
            r = await call(s, "start_label_item", {"taskId": task_id, "itemId": "item-001"})
            assert r["status"] == "LABELING"
            ok("start_label_item", {"status": r["status"]})
        except Exception as e:
            fail("start_label_item", e)

        # ===== Agent 控制 =====
        print("\n--- Agent 控制 ---")

        # 16. trigger_agent
        try:
            r = await call(s, "trigger_agent", {"jobId": job_id})
            ok("trigger_agent")
        except Exception as e:
            fail("trigger_agent", e)

        # 17. start_task
        try:
            r = await call(s, "start_task", {"taskId": task_id})
            assert r["status"] == "RUNNING"
            ok("start_task", {"status": r["status"]})
        except Exception as e:
            fail("start_task", e)

        # 18. report_progress
        try:
            r = await call(s, "report_progress", {"taskId": task_id, "progress": 50, "label": "气孔", "confidence": 0.95})
            assert r["progress"] == 50
            ok("report_progress", {"progress": r["progress"]})
        except Exception as e:
            fail("report_progress", e)

        # 19. request_human
        try:
            r = await call(s, "request_human", {"taskId": task_id, "imageId": "img-0001", "message": "不确定", "preLabel": "气孔", "preConfidence": 0.6})
            assert r["id"]
            ok("request_human", {"id": r["id"], "status": r["status"]})
        except Exception as e:
            fail("request_human", e)

        # 20. confirm_human
        try:
            r = await call(s, "confirm_human", {"taskId": task_id, "imageId": "img-0001", "humanLabel": "裂纹", "message": "确认"})
            assert r["status"] == "CONFIRMED"
            ok("confirm_human", {"status": r["status"]})
        except Exception as e:
            fail("confirm_human", e)

        # ===== 清理 =====
        print("\n--- 清理 ---")

        # 21. delete_dataset（删测试数据集）
        try:
            r = await call(s, "delete_dataset", {"id": new_ds_id})
            ok("delete_dataset")
        except Exception as e:
            fail("delete_dataset", e)

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"Mock 测试结果: {passed} 通过, {failed} 失败")
    print(sep)
    return failed == 0


async def test_rest():
    """REST transport: 只测读操作 + create_job（不破坏数据）。"""
    print("\n" + "=" * 60)
    print("REST Transport — 测试真实平台（只测读操作 + create_job）")
    print("=" * 60)

    async with mcp_session() as s:
        # 1. list_datasets
        try:
            r = await call(s, "list_datasets", {"pageNum": 1, "pageSize": 5})
            records = r.get("records", [])
            ok("list_datasets", {"total": r.get("total"), "first": records[0]["name"] if records else None})
            ds_id = records[0]["id"] if records else None
        except Exception as e:
            fail("list_datasets", e)
            return False

        if not ds_id:
            print("  [SKIP] 无数据集")
            return True

        # 2. get_dataset
        try:
            r = await call(s, "get_dataset", {"id": ds_id})
            ok("get_dataset", {"id": r.get("id"), "name": r.get("name")})
        except Exception as e:
            fail("get_dataset", e)

        # 3. list_dataset_images
        try:
            r = await call(s, "list_dataset_images", {"id": ds_id})
            ok("list_dataset_images", {"total": r.get("total", 0)})
        except Exception as e:
            fail("list_dataset_images", e)

        # 4. list_jobs
        try:
            r = await call(s, "list_jobs", {"datasetId": ds_id})
            ok("list_jobs", {"total": r.get("total", 0)})
        except Exception as e:
            fail("list_jobs", e)

        # 5. create_job（不触发标注，只创建）
        try:
            r = await call(s, "create_job", {
                "datasetId": ds_id, "name": "接口测试作业",
                "labels": ["测试"], "annotationType": "CLASSIFICATION", "platform": "LABEL_STUDIO",
            })
            job_id = _extract_id(r, "jobId", "id")
            ok("create_job", {"job_id": job_id})
        except Exception as e:
            fail("create_job", e)
            return False

        # 6. get_job
        try:
            r = await call(s, "get_job", {"id": job_id})
            ok("get_job", {"id": r.get("id"), "status": r.get("status")})
        except Exception as e:
            fail("get_job", e)

        # 7. list_tasks
        try:
            r = await call(s, "list_tasks", {"jobId": job_id})
            ok("list_tasks", {"total": r.get("total", 0)})
        except Exception as e:
            fail("list_tasks", e)

        # 8. list_all_tasks
        try:
            r = await call(s, "list_all_tasks", {"pageNum": 1, "pageSize": 5})
            ok("list_all_tasks", {"total": r.get("total", 0)})
        except Exception as e:
            fail("list_all_tasks", e)

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"REST 测试结果: {passed} 通过, {failed} 失败")
    print(sep)
    return failed == 0


async def main():
    transport = os.environ.get("MCP_TRANSPORT", "mock")
    print(f"Transport: {transport}\n")

    if transport == "mock":
        success = await test_mock()
    elif transport == "rest":
        success = await test_rest()
    else:
        print(f"未知 transport: {transport}")
        return False

    return success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
