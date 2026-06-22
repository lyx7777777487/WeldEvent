"""Mock MCP Server — 模拟真实标注平台的 MCP 工具。

模拟真实 REST API（http://172.16.11.11:8081）的全部 18 个端点，
通过 MCP 协议暴露。返回值与真实 API 一致（含 ApiResponse 包装）。

模拟的工具（18 个）:
  数据集:
    1. list_datasets         — GET /api/datasets
    2. create_dataset        — POST /api/datasets
    3. get_dataset           — GET /api/datasets/{id}
    4. delete_dataset        — DELETE /api/datasets/{id}
    5. list_dataset_images   — GET /api/datasets/{id}/images
    6. upload_dataset_images — POST /api/datasets/{id}/images
    7. list_jobs             — GET /api/datasets/{id}/jobs
  作业:
    8. create_job            — POST /api/jobs
    9. get_job               — GET /api/jobs/{id}
   10. list_tasks            — GET /api/jobs/{id}/tasks
  任务:
   11. list_all_tasks        — GET /api/tasks
   12. create_task           — POST /api/tasks
   13. get_task              — GET /api/tasks/{id}
   14. assign_task           — PUT /api/tasks/{taskId}/assign
   15. start_label_item      — POST /api/label/tasks/{taskId}/items/{itemId}
  Agent 控制:
   16. trigger_agent         — POST /api/agent/trigger
   17. start_task            — POST /api/agent/start-task
   18. request_human         — POST /api/agent/request-human
   19. confirm_human         — POST /api/agent/confirm-human
   20. report_progress        — POST /api/agent/report-progress

行为:
  - create_job 后内部状态机进入 CREATED
  - trigger_agent 后状态机自动推进到 COMPLETED（模拟 AI 标注完成）
  - get_job 返回 AnnotationJobResp（status/totalCount/completedCount）
  - list_tasks 返回分页 IPage<AnnotationTaskResp>
  - 所有返回值都包 ApiResponse 包装: {code: 200, message: "ok", data: T}
"""

import time
from typing import Any

# 模块级单例状态
_failure_mode: str | None = None
_failure_message: str = "mock failure"

# 模拟数据存储
_datasets: list[dict] = []
_dataset_images: dict[str, list[dict]] = {}  # dataset_id → [image_info]
_jobs: dict[str, dict] = {}  # job_id → AnnotationJobResp
_tasks: dict[str, list[dict]] = {}  # job_id → [AnnotationTaskResp]
_all_tasks: dict[str, dict] = {}  # task_id → task（跨 job 索引）
_job_counter: int = 0
_task_counter: int = 0
_dataset_counter: int = 0
_image_counter: int = 0
_human_reviews: list[dict] = []  # 人工审核记录
_progress_reports: list[dict] = []  # 进度上报记录


def set_failure_mode(mode: str | None, message: str = "mock failure") -> None:
    """测试前调用，让下一次工具调用触发指定失败。

    mode:
      None      — 正常返回
      "business" — 抛 McpBusinessError（应转 ERROR output 不重试）
      "infra"    — 抛 McpInfraError（应触发 Temporal 重试）
    """
    global _failure_mode, _failure_message
    _failure_mode = mode
    _failure_message = message


def reset() -> None:
    """每个测试用例前重置。"""
    global _failure_mode, _failure_message, _datasets, _dataset_images
    global _jobs, _tasks, _all_tasks
    global _job_counter, _task_counter, _dataset_counter, _image_counter
    global _human_reviews, _progress_reports
    _failure_mode = None
    _failure_message = "mock failure"
    _datasets = [
        {
            "id": "ds-001",
            "name": "焊缝宏观检测数据集",
            "modality": "IMAGE",
            "description": "示例数据集",
            "ownerId": "user-001",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    ]
    _dataset_images = {}
    _jobs = {}
    _tasks = {}
    _all_tasks = {}
    _job_counter = 0
    _task_counter = 0
    _dataset_counter = 1  # 从 1 开始，避免和默认 ds-001 冲突
    _image_counter = 0
    _human_reviews = []
    _progress_reports = []


# 初始化默认数据集
reset()


def _ok(data: Any) -> dict:
    """包装成 ApiResponse 格式。"""
    return {"code": 200, "message": "ok", "data": data}


def _fail(code: int, message: str) -> dict:
    """包装成 ApiResponse 错误格式。"""
    return {"code": code, "message": message, "data": None}


class MockMcpServer:
    """in-process mock，实现 call_tool 接口，模拟真实标注平台的 9 个工具。"""

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if _failure_mode == "business":
            from shared.mcp_tools.mcp_session import McpBusinessError
            raise McpBusinessError(_failure_message)
        if _failure_mode == "infra":
            from shared.mcp_tools.mcp_session import McpInfraError
            raise McpInfraError(_failure_message)

        handler = _TOOL_HANDLERS.get(name)
        if not handler:
            return _fail(404, f"unknown tool: {name}")
        return handler(arguments)

    async def close(self) -> None:
        pass


# ===== 工具处理器 =====

def _list_datasets(args: dict) -> dict:
    page_num = args.get("pageNum", 1)
    page_size = args.get("pageSize", 10)
    start = (page_num - 1) * page_size
    end = start + page_size
    items = _datasets[start:end]
    return _ok({
        "records": items,
        "total": len(_datasets),
        "size": page_size,
        "current": page_num,
    })


def _get_dataset(args: dict) -> dict:
    dataset_id = args.get("id", "") or args.get("datasetId", "")
    for ds in _datasets:
        if ds["id"] == dataset_id:
            return _ok(ds)
    return _fail(404, "数据集不存在")


def _create_job(args: dict) -> dict:
    global _job_counter
    _job_counter += 1
    job_id = f"job-{_job_counter:04d}"

    labels = args.get("labels", [])
    if isinstance(labels, list):
        labels_str = ",".join(labels)
    else:
        labels_str = str(labels or "")

    job_info = {
        "id": job_id,
        "datasetId": args.get("datasetId", ""),
        "name": args.get("name", "未命名作业"),
        "platform": args.get("platform", "LABEL_STUDIO"),
        "annotationType": args.get("annotationType", "CLASSIFICATION"),
        "labels": labels_str,
        "status": "CREATED",
        "totalCount": 0,
        "completedCount": 0,
        "ownerId": "mock-user",
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }
    _jobs[job_id] = job_info
    _tasks[job_id] = []
    return _ok({"jobId": job_id})


def _list_jobs(args: dict) -> dict:
    dataset_id = args.get("datasetId", "") or args.get("id", "")
    jobs = [j for j in _jobs.values() if j.get("datasetId") == dataset_id]
    return _ok({
        "records": jobs,
        "total": len(jobs),
        "size": 50,
        "current": 1,
    })


def _get_job(args: dict) -> dict:
    job_id = args.get("id", "") or args.get("jobId", "")
    job = _jobs.get(job_id)
    if not job:
        return _fail(404, "作业不存在")
    return _ok(dict(job))


def _list_tasks(args: dict) -> dict:
    job_id = args.get("jobId", "") or args.get("id", "")
    tasks = _tasks.get(job_id, [])
    return _ok({
        "records": [dict(t) for t in tasks],
        "total": len(tasks),
        "size": 50,
        "current": 1,
    })


def _create_task(args: dict) -> dict:
    global _task_counter
    job_id = args.get("jobId", "")
    if job_id not in _jobs:
        return _fail(404, "作业不存在")

    _task_counter += 1
    task_id = f"task-{_task_counter:04d}"
    task = {
        "id": task_id,
        "jobId": job_id,
        "ownerId": "mock-user",
        "assigneeId": None,
        "assigneeName": None,
        "status": "PENDING",
        "totalCount": 0,
        "completedCount": 0,
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }
    _tasks[job_id].append(task)
    _all_tasks[task_id] = task
    _jobs[job_id]["totalCount"] = len(_tasks[job_id])

    return _ok({"taskId": task_id})


def _trigger_agent(args: dict) -> dict:
    job_id = args.get("jobId", "")
    job = _jobs.get(job_id)
    if not job:
        return _fail(404, "作业不存在")

    # 模拟 AI 标注完成：把所有 task 标记为 COMPLETED
    for task in _tasks.get(job_id, []):
        task["status"] = "COMPLETED"
        task["totalCount"] = 1
        task["completedCount"] = 1

    job["status"] = "COMPLETED"
    job["completedCount"] = len(_tasks.get(job_id, []))
    job["updatedAt"] = _now_iso()

    return _ok(None)


def _assign_task(args: dict) -> dict:
    task_id = args.get("taskId", "")
    assignee_id = args.get("assigneeId", "")
    assignee_name = args.get("assigneeName", "")

    for job_tasks in _tasks.values():
        for t in job_tasks:
            if t.get("id") == task_id:
                t["assigneeId"] = assignee_id
                t["assigneeName"] = assignee_name
                t["updatedAt"] = _now_iso()
                return _ok(None)

    return _fail(404, "任务不存在")


def _create_dataset(args: dict) -> dict:
    global _dataset_counter
    _dataset_counter += 1
    dataset_id = f"ds-{_dataset_counter:03d}"
    dataset = {
        "id": dataset_id,
        "name": args.get("name", "未命名数据集"),
        "modality": args.get("modality", "IMAGE"),
        "description": args.get("description", ""),
        "ownerId": "mock-user",
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }
    _datasets.append(dataset)
    return _ok(dataset)


def _delete_dataset(args: dict) -> dict:
    dataset_id = args.get("id", "")
    for i, ds in enumerate(_datasets):
        if ds["id"] == dataset_id:
            _datasets.pop(i)
            _dataset_images.pop(dataset_id, None)
            return _ok(None)
    return _fail(404, "数据集不存在")


def _list_dataset_images(args: dict) -> dict:
    dataset_id = args.get("id", "")
    images = _dataset_images.get(dataset_id, [])
    return _ok({
        "records": images,
        "total": len(images),
    })


def _upload_dataset_images(args: dict) -> dict:
    dataset_id = args.get("id", "")
    files = args.get("files", [])
    if dataset_id not in _dataset_images:
        _dataset_images[dataset_id] = []
    global _image_counter
    uploaded = []
    for f in files:
        _image_counter += 1
        img = {
            "id": f"img-{_image_counter:04d}",
            "datasetId": dataset_id,
            "filename": f if isinstance(f, str) else f.get("filename", ""),
            "url": f if isinstance(f, str) else f.get("url", ""),
            "status": "UPLOADED",
            "createdAt": _now_iso(),
        }
        _dataset_images[dataset_id].append(img)
        uploaded.append(img)
    return _ok({"uploaded": len(uploaded), "images": uploaded})


def _list_all_tasks(args: dict) -> dict:
    page_num = args.get("pageNum", 1)
    page_size = args.get("pageSize", 10)
    all_tasks = list(_all_tasks.values())
    start = (page_num - 1) * page_size
    end = start + page_size
    items = all_tasks[start:end]
    return _ok({
        "records": items,
        "total": len(all_tasks),
        "size": page_size,
        "current": page_num,
    })


def _get_task(args: dict) -> dict:
    task_id = args.get("id", "")
    task = _all_tasks.get(task_id)
    if not task:
        return _fail(404, "任务不存在")
    return _ok(dict(task))


def _start_label_item(args: dict) -> dict:
    task_id = args.get("taskId", "")
    item_id = args.get("itemId", "")
    task = _all_tasks.get(task_id)
    if not task:
        return _fail(404, "任务不存在")
    # 模拟开始标注某个 item
    return _ok({
        "taskId": task_id,
        "itemId": item_id,
        "status": "LABELING",
        "message": "标注已开始",
    })


def _start_task(args: dict) -> dict:
    task_id = args.get("taskId", "")
    task = _all_tasks.get(task_id)
    if not task:
        return _fail(404, "任务不存在")
    task["status"] = "RUNNING"
    task["updatedAt"] = _now_iso()
    return _ok({"taskId": task_id, "status": "RUNNING"})


def _request_human(args: dict) -> dict:
    review = {
        "id": f"review-{len(_human_reviews) + 1:04d}",
        "taskId": args.get("taskId", ""),
        "imageId": args.get("imageId", ""),
        "message": args.get("message", ""),
        "result": args.get("result", ""),
        "humanLabel": args.get("humanLabel", ""),
        "preLabel": args.get("preLabel", ""),
        "preConfidence": args.get("preConfidence", 0),
        "status": "PENDING",
        "createdAt": _now_iso(),
    }
    _human_reviews.append(review)
    return _ok(review)


def _confirm_human(args: dict) -> dict:
    task_id = args.get("taskId", "")
    image_id = args.get("imageId", "")
    for review in _human_reviews:
        if review["taskId"] == task_id and review["imageId"] == image_id:
            review["status"] = "CONFIRMED"
            review["humanLabel"] = args.get("humanLabel", review.get("humanLabel", ""))
            review["message"] = args.get("message", review.get("message", ""))
            review["updatedAt"] = _now_iso()
            return _ok(review)
    return _fail(404, "人工审核记录不存在")


def _report_progress(args: dict) -> dict:
    report = {
        "id": f"progress-{len(_progress_reports) + 1:04d}",
        "taskId": args.get("taskId", ""),
        "progress": args.get("progress", 0),
        "message": args.get("message", ""),
        "imageId": args.get("imageId", ""),
        "label": args.get("label", ""),
        "confidence": args.get("confidence", 0),
        "source": args.get("source", "AI"),
        "createdAt": _now_iso(),
    }
    _progress_reports.append(report)
    # 更新 task 进度
    task_id = args.get("taskId", "")
    if task_id in _all_tasks:
        _all_tasks[task_id]["completedCount"] = args.get("progress", 0)
        _all_tasks[task_id]["updatedAt"] = _now_iso()
    return _ok(report)


_TOOL_HANDLERS = {
    # 数据集
    "list_datasets": _list_datasets,
    "create_dataset": _create_dataset,
    "get_dataset": _get_dataset,
    "delete_dataset": _delete_dataset,
    "list_dataset_images": _list_dataset_images,
    "upload_dataset_images": _upload_dataset_images,
    # 作业
    "create_job": _create_job,
    "list_jobs": _list_jobs,
    "get_job": _get_job,
    "list_tasks": _list_tasks,
    # 任务
    "list_all_tasks": _list_all_tasks,
    "create_task": _create_task,
    "get_task": _get_task,
    "assign_task": _assign_task,
    "start_label_item": _start_label_item,
    # Agent 控制
    "trigger_agent": _trigger_agent,
    "start_task": _start_task,
    "request_human": _request_human,
    "confirm_human": _confirm_human,
    "report_progress": _report_progress,
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# CLI 入口：python -m shared.mcp_tools.mock_mcp_server
if __name__ == "__main__":
    import asyncio
    import json

    async def _main():
        reset()
        server = MockMcpServer()
        # 测试完整流程
        job = await server.call_tool("create_job", {
            "datasetId": "ds-001",
            "name": "测试作业",
            "labels": ["气孔", "裂纹"],
        })
        print("create_job:", json.dumps(job, indent=2, ensure_ascii=False))

        job_id = job["data"]["jobId"]

        await server.call_tool("create_task", {"jobId": job_id})
        await server.call_tool("trigger_agent", {"jobId": job_id})

        status = await server.call_tool("get_job", {"id": job_id})
        print("get_job:", json.dumps(status, indent=2, ensure_ascii=False))

        tasks = await server.call_tool("list_tasks", {"jobId": job_id})
        print("list_tasks:", json.dumps(tasks, indent=2, ensure_ascii=False))

    asyncio.run(_main())
