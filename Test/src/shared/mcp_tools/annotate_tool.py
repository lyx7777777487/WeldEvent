"""annotate_tool — 调用标注平台的作业级标注流程。

适配真实标注平台 REST API（见 http://172.16.11.11:8081/swagger-ui/index.html）：
  - 地址: http://172.16.11.11:8081
  - 认证: JWT（Authorization: Bearer <token>）
  - 响应包装: ApiResponse<T> = {code: int, message: str, data: T}

流程:
  1. POST /api/jobs           → createJob  (CreateJobReq → CreateJobResp)
  2. POST /api/tasks          → createTask (CreateTaskReq → CreateTaskResp)
  3. POST /api/agent/trigger   → triggerAgent (异步触发 AI 标注)
  4. GET  /api/jobs/{id}       → getJobById (轮询作业状态)
  5. GET  /api/jobs/{id}/tasks → listTasksByJob (取任务结果)

入参:
  dataset_id: str          — 必填，数据集 ID
  job_name: str            — 必填，作业名称
  labels: list[str] | None — 可选，标签列表，如 ["气孔","裂纹","夹渣"]
  annotation_type: str    — 可选，标注类型，默认 CLASSIFICATION
  platform: str            — 可选，标注平台，默认 LABEL_STUDIO
  poll_interval: float     — 可选，轮询间隔秒，默认 5.0
  poll_timeout: float      — 可选，轮询超时秒，默认 600

返回 dict（已解包 ApiResponse，直接是 data 层）:
  {
    "job_id": str,
    "status": "completed" | "failed" | "timeout",
    "total_count": int,
    "completed_count": int,
    "tasks": [
      {
        "task_id": str,
        "status": str,
        "assignee_id": str | None,
        "assignee_name": str | None,
        "total_count": int,
        "completed_count": int,
      }
    ],
  }

异常:
  McpBusinessError — 标注 server 业务拒绝（不重试）
  McpInfraError    — 网络/超时/server 崩溃（应重试）
"""

import asyncio
import logging

from shared.mcp_tools.mcp_session import mcp_session, McpBusinessError, McpInfraError

logger = logging.getLogger(__name__)

__all__ = ["annotate", "McpBusinessError", "McpInfraError"]

_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_POLL_TIMEOUT = 600.0


async def annotate(
    dataset_id: str,
    job_name: str,
    labels: list[str] | None = None,
    annotation_type: str = "CLASSIFICATION",
    platform: str = "LABEL_STUDIO",
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
) -> dict:
    """调标注平台，执行完整作业级标注流程。

    流程:
      1. create_job(dataset_id, job_name, labels) → job_id
      2. create_task(job_id) → task_id
      3. trigger_agent(job_id) → 触发 AI 标注（异步）
      4. 轮询 get_job(job_id) 直到完成或超时
      5. list_tasks(job_id) → 取所有任务结果
    """
    async with mcp_session() as session:
        # 1. 创建作业
        job_args = {
            "datasetId": dataset_id,
            "name": job_name,
            "annotationType": annotation_type,
            "platform": platform,
        }
        if labels:
            job_args["labels"] = labels

        job_result = await session.call_tool("create_job", job_args)
        job_data = _unwrap_api_response(job_result, "create_job")
        job_id = _extract_id(job_data, "jobId", "id")
        logger.info("create_job: dataset=%s, job=%s", dataset_id, job_id)

        # 2. 创建任务
        task_result = await session.call_tool("create_task", {"jobId": job_id})
        task_data = _unwrap_api_response(task_result, "create_task")
        logger.info("create_task: job=%s, task=%s", job_id, _extract_id(task_data, "taskId", "id", ""))

        # 3. 触发 AI 标注
        trigger_result = await session.call_tool("trigger_agent", {"jobId": job_id})
        _unwrap_api_response(trigger_result, "trigger_agent")
        logger.info("trigger_agent: job=%s", job_id)

        # 4. 轮询作业状态
        final_status = await _poll_job_status(
            session, job_id, poll_interval, poll_timeout
        )

        # 5. 取任务结果
        tasks_result = await session.call_tool("list_tasks", {"jobId": job_id})
        tasks_data = _unwrap_api_response(tasks_result, "list_tasks")
        tasks = _extract_tasks(tasks_data)

        return {
            "job_id": job_id,
            "status": final_status,
            "total_count": _safe_int(tasks_data, "total", len(tasks)),
            "completed_count": sum(1 for t in tasks if t.get("status") in ("COMPLETED", "DONE")),
            "tasks": tasks,
        }


async def _poll_job_status(
    session,
    job_id: str,
    interval: float,
    timeout: float,
) -> str:
    """轮询 get_job 直到作业完成或超时。"""
    elapsed = 0.0
    while elapsed < timeout:
        job_result = await session.call_tool("get_job", {"id": job_id})
        job_data = _unwrap_api_response(job_result, "get_job")
        status = _extract_job_status(job_data)
        completed = _safe_int(job_data, "completedCount", 0)
        total = _safe_int(job_data, "totalCount", 0)

        logger.debug(
            "poll job=%s: status=%s, %d/%d", job_id, status, completed, total
        )

        if status in ("COMPLETED", "DONE", "completed"):
            return "completed"
        if status in ("FAILED", "ERROR", "failed", "error"):
            return "failed"

        await asyncio.sleep(interval)
        elapsed += interval

    logger.warning("poll job=%s 超时 (%.0fs)", job_id, timeout)
    return "timeout"


def _unwrap_api_response(result: dict, op: str) -> dict:
    """解包 ApiResponse<T> — 校验 code==200，返回 data。

    真实 REST API 返回 {code, message, data}。
    MCP server 透传这个结构。
    """
    if not isinstance(result, dict):
        raise McpBusinessError(f"{op} 返回非 dict: {result}")

    # 如果没有 code 字段，可能是 mock 或已解包的数据
    if "code" not in result:
        return result

    code = result.get("code")
    if code != 200:
        msg = result.get("message", "(no message)")
        raise McpBusinessError(f"{op} 业务失败: code={code}, message={msg}")

    data = result.get("data")
    if data is None:
        # 平台返回 data: null 表示异步操作已接收，返回状态
        return {"status": "ACCEPTED", "message": "操作已提交"}
    if isinstance(data, list):
        return {"records": data, "total": len(data)}
    if isinstance(data, str):
        # MCP server 返回纯文本（如 "作业创建成功，jobId: xxx"）
        return {"message": data}
    return data


def _extract_id(data: dict, *keys: str) -> str:
    """从 API 返回中提取 ID，支持多个候选 key。

    兼容 MCP 纯文本返回（如 "作业创建成功，jobId: xxx"）。
    """
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if v:
                return str(v)
    # MCP 纯文本：尝试正则提取
    import re
    text = str(data) if data else ""
    for k in keys:
        m = re.search(rf"{k}[:\s]+([A-Za-z0-9]+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
    raise McpBusinessError(f"无法从返回提取 ID: {data}")


def _extract_job_status(job_data: dict) -> str:
    """从 get_job 返回中提取作业状态。"""
    if isinstance(job_data, dict):
        return str(job_data.get("status", "unknown"))
    return "unknown"


def _extract_tasks(tasks_data: dict) -> list[dict]:
    """从 list_tasks 返回中提取任务列表。

    真实 API 返回分页结构:
      {records: [...], total: N, current: 1, size: 50}
    """
    if isinstance(tasks_data, dict):
        records = tasks_data.get("records") or tasks_data.get("list") or tasks_data.get("data") or []
    elif isinstance(tasks_data, list):
        records = tasks_data
    else:
        records = []

    tasks = []
    for t in records if isinstance(records, list) else []:
        tasks.append({
            "task_id": str(t.get("id") or t.get("taskId") or ""),
            "job_id": str(t.get("jobId") or ""),
            "status": t.get("status") or "unknown",
            "assignee_id": t.get("assigneeId"),
            "assignee_name": t.get("assigneeName"),
            "total_count": _safe_int(t, "totalCount", 0),
            "completed_count": _safe_int(t, "completedCount", 0),
        })
    return tasks


def _safe_int(d: dict, key: str, default: int = 0) -> int:
    """安全取 int 字段。"""
    if not isinstance(d, dict):
        return default
    v = d.get(key, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
