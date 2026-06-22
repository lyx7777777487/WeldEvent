"""FastAPI 后端 — 包装 L1 Agent 功能为 REST API，供前端调用。

启动:
  cd Test && PYTHONPATH=src python -m web.server

环境变量:
  LLM_API_KEY     — DeepSeek API key
  LLM_BASE_URL    — 默认 https://api.deepseek.com/v1
  LLM_MODEL       — 默认 deepseek-chat
  MCP_TRANSPORT   — rest / mock（默认 rest）
  MCP_REST_URL    — 标注平台 REST API 地址
  MCP_AUTH_URL    — 登录接口
  MCP_USERNAME    — 用户名
  MCP_PASSWORD    — 密码
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("web")

app = FastAPI(title="WeldEvent L1 Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


# ===== MCP 调用日志（NDJSON streamable 实时推送） =====

_mcp_log_subscribers: list[asyncio.Queue] = []
_mcp_log_seq = 0


def _push_mcp_log(entry: dict):
    """记录一次 MCP tool 调用，推送给所有流式订阅者。"""
    global _mcp_log_seq
    _mcp_log_seq += 1
    entry["seq"] = _mcp_log_seq
    for q in _mcp_log_subscribers:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass


@app.get("/api/mcp-log/stream")
async def mcp_log_stream():
    """NDJSON 流式端点：实时推送 MCP tool 调用日志（每行一个 JSON）。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _mcp_log_subscribers.append(q)

    async def ndjson_stream():
        try:
            # 先发连接成功事件
            yield json.dumps(
                {"type": "connected", "message": "MCP 日志流已连接", "timestamp": __import__("time").strftime("%H:%M:%S")},
                ensure_ascii=False,
            ) + "\n"
            while True:
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15)
                    yield json.dumps(entry, ensure_ascii=False) + "\n"
                except asyncio.TimeoutError:
                    # 心跳行（空 JSON 注释，前端忽略）
                    yield "{}\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in _mcp_log_subscribers:
                _mcp_log_subscribers.remove(q)

    return StreamingResponse(ndjson_stream(), media_type="application/x-ndjson")


# ===== 请求/响应模型 =====

class DesignRequest(BaseModel):
    requirement: str
    dataset_id: str
    dataset_name: str = ""


class ExecuteRequest(BaseModel):
    template: dict
    poll_timeout: float = 30.0


class DatasetCreateRequest(BaseModel):
    name: str
    modality: str = "IMAGE"
    description: str = ""


class JobCreateRequest(BaseModel):
    datasetId: str
    name: str
    labels: list[str] = []
    annotationType: str = "CLASSIFICATION"
    platform: str = "LABEL_STUDIO"


class TaskCreateRequest(BaseModel):
    jobId: str
    assigneeId: str | None = None
    assigneeName: str | None = None


class TaskAssignRequest(BaseModel):
    taskId: str
    assigneeId: str
    assigneeName: str = ""


class StartLabelItemRequest(BaseModel):
    taskId: str
    itemId: str


class AgentTriggerRequest(BaseModel):
    jobId: str


class AgentStartTaskRequest(BaseModel):
    taskId: str


class AgentRequestHumanRequest(BaseModel):
    taskId: str
    imageId: str
    message: str = ""
    preLabel: str = ""
    preConfidence: float = 0.0
    result: str = ""


class AgentConfirmHumanRequest(BaseModel):
    taskId: str
    imageId: str
    humanLabel: str = ""
    message: str = ""


class AgentReportProgressRequest(BaseModel):
    taskId: str
    progress: int = 0
    message: str = ""
    imageId: str = ""
    label: str = ""
    confidence: float = 0.0
    source: str = "AI"


# ===== 全局状态（单用户测试用） =====

_llm_client = None
_design_history: list[dict] = []
_execution_tasks: dict[str, dict] = {}  # execution_id → state


def _get_llm():
    global _llm_client
    if _llm_client is None:
        from l1_agent.design import OpenAICompatibleLlmClient
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise HTTPException(500, "LLM_API_KEY 未设置")
        _llm_client = OpenAICompatibleLlmClient(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        )
    return _llm_client


# ===== 辅助 =====

async def _call_tool(name: str, args: dict) -> dict:
    """统一调用 MCP 工具，解包 ApiResponse 返回 data。同时记录调用日志。"""
    from shared.mcp_tools.mcp_session import mcp_session, McpSessionError, McpInfraError
    from shared.mcp_tools.annotate_tool import _unwrap_api_response, McpBusinessError
    import time as _time

    t0 = _time.time()
    entry = {
        "type": "mcp_call",
        "tool": name,
        "args": args,
        "status": "pending",
        "timestamp": _time.strftime("%H:%M:%S"),
    }
    _push_mcp_log(entry)

    try:
        async with mcp_session() as session:
            result = await session.call_tool(name, args)
            unwrapped = _unwrap_api_response(result, name)

            # 记录成功
            elapsed = round((_time.time() - t0) * 1000, 1)
            # 截断过长的返回值用于展示
            result_str = str(unwrapped)
            if len(result_str) > 500:
                result_str = result_str[:500] + "...(截断)"
            _push_mcp_log({
                "type": "mcp_result",
                "tool": name,
                "status": "success",
                "result": result_str,
                "elapsed_ms": elapsed,
                "timestamp": _time.strftime("%H:%M:%S"),
            })
            return unwrapped
    except McpBusinessError as e:
        elapsed = round((_time.time() - t0) * 1000, 1)
        _push_mcp_log({
            "type": "mcp_result",
            "tool": name,
            "status": "error",
            "error": str(e),
            "elapsed_ms": elapsed,
            "timestamp": _time.strftime("%H:%M:%S"),
        })
        raise HTTPException(422, str(e))
    except (McpSessionError, McpInfraError) as e:
        elapsed = round((_time.time() - t0) * 1000, 1)
        _push_mcp_log({
            "type": "mcp_result",
            "tool": name,
            "status": "error",
            "error": f"连接失败: {e}",
            "elapsed_ms": elapsed,
            "timestamp": _time.strftime("%H:%M:%S"),
        })
        raise HTTPException(502, f"标注平台连接失败: {e}")


# ===== 标注平台 API 路由（20 个接口） =====

# ---------- 数据集管理 (6) ----------

@app.get("/api/datasets")
async def list_datasets(page: int = 1, size: int = 50):
    """1. list_datasets — 查数据集列表。"""
    return await _call_tool("list_datasets", {"pageNum": page, "pageSize": size})


@app.post("/api/datasets")
async def create_dataset(req: DatasetCreateRequest):
    """2. create_dataset — 创建数据集。"""
    return await _call_tool("create_dataset", req.model_dump())


@app.get("/api/datasets/{dataset_id}")
async def get_dataset(dataset_id: str):
    """3. get_dataset — 查数据集详情。"""
    return await _call_tool("get_dataset", {"id": dataset_id})


@app.delete("/api/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str):
    """4. delete_dataset — 删除数据集。"""
    return await _call_tool("delete_dataset", {"id": dataset_id})


@app.get("/api/datasets/{dataset_id}/images")
async def list_dataset_images(dataset_id: str):
    """5. list_dataset_images — 查数据集图片列表。"""
    return await _call_tool("list_dataset_images", {"id": dataset_id})


@app.post("/api/datasets/{dataset_id}/images")
async def upload_dataset_images(dataset_id: str, files: list[UploadFile] = []):
    """6. upload_dataset_images — 上传图片到数据集（支持真实文件上传）。"""
    from shared.mcp_tools.mcp_session import mcp_session, McpSessionError, McpInfraError
    from shared.mcp_tools.annotate_tool import _unwrap_api_response, McpBusinessError
    import time as _time

    if not files:
        raise HTTPException(400, "未选择文件")

    file_names = [f.filename for f in files]
    args_display = {"id": dataset_id, "files": file_names, "count": len(files)}
    t0 = _time.time()
    _push_mcp_log({
        "type": "mcp_call",
        "tool": "upload_dataset_images",
        "args": args_display,
        "status": "pending",
        "timestamp": _time.strftime("%H:%M:%S"),
    })

    try:
        async with mcp_session() as session:
            # 如果 session 支持 upload_files（REST transport），直接传文件
            if hasattr(session, "upload_files"):
                result = await session.upload_files(dataset_id, files)
            else:
                # 否则用 call_tool（mock transport 等只传文件名）
                result = await session.call_tool("upload_dataset_images", {"id": dataset_id, "files": file_names})

            unwrapped = _unwrap_api_response(result, "upload_dataset_images")
            elapsed = round((_time.time() - t0) * 1000, 1)
            result_str = str(unwrapped)
            if len(result_str) > 500:
                result_str = result_str[:500] + "...(截断)"
            _push_mcp_log({
                "type": "mcp_result",
                "tool": "upload_dataset_images",
                "status": "success",
                "result": result_str,
                "elapsed_ms": elapsed,
                "timestamp": _time.strftime("%H:%M:%S"),
            })
            return unwrapped
    except McpBusinessError as e:
        elapsed = round((_time.time() - t0) * 1000, 1)
        _push_mcp_log({
            "type": "mcp_result",
            "tool": "upload_dataset_images",
            "status": "error",
            "error": str(e),
            "elapsed_ms": elapsed,
            "timestamp": _time.strftime("%H:%M:%S"),
        })
        raise HTTPException(422, str(e))
    except (McpSessionError, McpInfraError) as e:
        elapsed = round((_time.time() - t0) * 1000, 1)
        _push_mcp_log({
            "type": "mcp_result",
            "tool": "upload_dataset_images",
            "status": "error",
            "error": f"连接失败: {e}",
            "elapsed_ms": elapsed,
            "timestamp": _time.strftime("%H:%M:%S"),
        })
        raise HTTPException(502, f"标注平台连接失败: {e}")


# ---------- 作业管理 (3) ----------

@app.post("/api/jobs")
async def create_job(req: JobCreateRequest):
    """7. create_job — 创建作业。"""
    return await _call_tool("create_job", req.model_dump())


@app.get("/api/datasets/{dataset_id}/jobs")
async def list_jobs(dataset_id: str):
    """8. list_jobs — 查数据集下的作业列表。"""
    return await _call_tool("list_jobs", {"datasetId": dataset_id})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """9. get_job — 查作业详情。"""
    return await _call_tool("get_job", {"id": job_id})


# ---------- 任务管理 (5) ----------

@app.get("/api/jobs/{job_id}/tasks")
async def list_tasks(job_id: str):
    """10. list_tasks — 查作业下的任务列表。"""
    return await _call_tool("list_tasks", {"jobId": job_id})


@app.get("/api/tasks")
async def list_all_tasks(page: int = 1, size: int = 10):
    """11. list_all_tasks — 查所有任务。"""
    return await _call_tool("list_all_tasks", {"pageNum": page, "pageSize": size})


@app.post("/api/tasks")
async def create_task(req: TaskCreateRequest):
    """12. create_task — 创建任务。"""
    return await _call_tool("create_task", req.model_dump())


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """13. get_task — 查任务详情。"""
    return await _call_tool("get_task", {"id": task_id})


@app.put("/api/tasks/{task_id}/assign")
async def assign_task(task_id: str, req: TaskAssignRequest):
    """14. assign_task — 分配任务。"""
    args = {"taskId": task_id, "assigneeId": req.assigneeId, "assigneeName": req.assigneeName}
    return await _call_tool("assign_task", args)


@app.post("/api/label/tasks/{task_id}/items/{item_id}")
async def start_label_item(task_id: str, item_id: str):
    """15. start_label_item — 开始标注条目。"""
    return await _call_tool("start_label_item", {"taskId": task_id, "itemId": item_id})


# ---------- Agent 控制 (6) ----------

@app.post("/api/agent/trigger")
async def trigger_agent(req: AgentTriggerRequest):
    """16. trigger_agent — 触发 Agent 标注。"""
    return await _call_tool("trigger_ai", {"jobId": req.jobId})


@app.post("/api/agent/start-task")
async def start_task(req: AgentStartTaskRequest):
    """17. start_task — Agent 启动任务。"""
    return await _call_tool("start_task", {"taskId": req.taskId})


@app.post("/api/agent/request-human")
async def request_human(req: AgentRequestHumanRequest):
    """18. request_human — Agent 请求人工审核。"""
    return await _call_tool("request_human", req.model_dump())


@app.post("/api/agent/confirm-human")
async def confirm_human(req: AgentConfirmHumanRequest):
    """19. confirm_human — 确认人工审核。"""
    return await _call_tool("confirm_human", req.model_dump())


@app.post("/api/agent/report-progress")
async def report_progress(req: AgentReportProgressRequest):
    """20. report_progress — Agent 汇报进度。"""
    return await _call_tool("report_progress", req.model_dump())


# ===== L1 Agent 高级路由 =====

@app.post("/api/design")
async def design_workflow(req: DesignRequest):
    """调 LLM 设计工作流。"""
    from l1_agent.design import design, LlmDesignError
    from l1_agent.design.template_validator import validate

    llm = _get_llm()
    dataset_summary = {
        "id": req.dataset_id,
        "name": req.dataset_name,
        "format": "IMAGE",
        "size_hint": "medium",
    }

    try:
        template = await design(
            llm=llm,
            requirement=req.requirement,
            dataset_summary=dataset_summary,
        )
    except LlmDesignError as e:
        raise HTTPException(422, f"LLM 设计失败: {e}")

    validation = validate(template)
    if not validation.ok:
        raise HTTPException(422, f"验证失败: {validation.error}")

    return {"template": template, "validation": {"ok": True}}


@app.post("/api/execute")
async def execute_workflow(req: ExecuteRequest):
    """执行工作流（调标注平台，异步不等待）。

    只做 create_job → create_task → assign_task → start_task → trigger_ai，
    触发后立即返回。不轮询作业状态（真实标注需要时间，用户去标注平台后台看进度）。
    每次调用都走 _call_tool，MCP 日志会实时推送到前端。
    """
    from shared.mcp_tools.annotate_tool import _extract_id

    template = req.template
    results = []

    for cp in template.get("control_points", []):
        cp_id = cp["id"]
        params = cp.get("params", {})
        cp_name = cp.get("name", "")

        if not params.get("dataset_id") or not params.get("job_name"):
            results.append({
                "cp_id": cp_id,
                "name": cp_name,
                "status": "ERROR",
                "error": "缺少 dataset_id / job_name",
            })
            continue

        try:
            # 1. create_job — 创建作业
            job_args = {
                "datasetId": params["dataset_id"],
                "name": params["job_name"],
                "annotationType": "CLASSIFICATION",
                "platform": "LABEL_STUDIO",
            }
            if params.get("labels"):
                job_args["labels"] = params["labels"]

            job_data = await _call_tool("create_job", job_args)
            job_id = _extract_id(job_data, "jobId", "id")

            # 2. create_task — 创建任务
            task_data = await _call_tool("create_task", {"jobId": job_id})
            task_id = _extract_id(task_data, "taskId", "id")

            # 3. assign_task — 分配标注员
            assignee_id = params.get("assignee_id", "annotator1")
            assignee_name = params.get("assignee_name", "标注员1")
            assign_status = "ASSIGNED"
            try:
                await _call_tool("assign_task", {
                    "taskId": task_id,
                    "assigneeId": assignee_id,
                    "assigneeName": assignee_name,
                })
            except HTTPException as e:
                assign_status = f"分配失败: {e.detail}"

            # 4. start_task — Agent 启动任务
            agent_status = "STARTED"
            try:
                await _call_tool("start_task", {"taskId": task_id})
            except HTTPException as e:
                agent_status = f"启动失败: {e.detail}"

            # 5. trigger_ai — 触发 AI 标注
            ai_status = "已触发"
            try:
                await _call_tool("trigger_ai", {"jobId": job_id})
            except HTTPException as e:
                ai_status = f"触发失败: {e.detail}"

            results.append({
                "cp_id": cp_id,
                "name": cp_name,
                "status": "OK",
                "data": {
                    "job_id": job_id,
                    "task_id": task_id,
                    "assignee": f"{assignee_name} ({assignee_id})",
                    "assign_status": assign_status,
                    "agent_status": agent_status,
                    "ai_status": ai_status,
                    "message": "已创建作业→分配标注员→启动任务→触发AI标注",
                },
            })

        except HTTPException as e:
            results.append({
                "cp_id": cp_id,
                "name": cp_name,
                "status": "ERROR",
                "error": str(e.detail),
            })
        except Exception as e:
            results.append({
                "cp_id": cp_id,
                "name": cp_name,
                "status": "ERROR",
                "error": str(e),
            })

    # LLM 汇报
    summary = ""
    try:
        from l1_agent.result_renderer import OpenAICompatibleSummarizer, render as render_result
        llm = _get_llm()
        summarizer = OpenAICompatibleSummarizer(
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        )
        requirement = template.get("id", "标注任务")
        summary = await render_result(summarizer, requirement, {"results": results})
    except Exception as e:
        logger.warning("LLM 汇报失败: %s", e)
        summary = f"(汇报生成失败: {e})"

    return {"results": results, "summary": summary}


# ===== 静态文件 =====

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/{path:path}")
async def static_files(path: str):
    f = STATIC_DIR / path
    if f.exists() and f.is_file():
        return FileResponse(f)
    raise HTTPException(404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
