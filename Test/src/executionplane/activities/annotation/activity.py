"""Annotation Activity — 调用外部标注 MCP server 的执行单元。

接法 A（裸函数模式）：参照 controlplane/adapter/mocks.py 的风格，
直接写一个 @activity.defn 装饰的函数，不走 BaseActivity 实例适配。

职责:
  1. 从 ActivityInput.params 取出 dataset_id / job_name 等参数
  2. 通过 shared.mcp_tools.annotate_tool 调用外部标注 MCP server
  3. 区分业务失败（转 ERROR output 不重试）与基础设施失败（抛异常走 retry_policy）
  4. 长耗时调用通过 activity.heartbeat() 保活

参数契约（ActivityInput.params）:
  {
    "dataset_id": str,         — 必填，数据集 ID
    "job_name": str,           — 必填，作业名称
    "labels": list[str] | None, — 可选，标签列表，如 ["气孔","裂纹"]
    "annotation_type": str,    — 可选，标注类型，默认 CLASSIFICATION
  }

返回 dict:
  {
    "status": "OK" | "ERROR",
    "data": dict | None,
    "error": str | None,
  }
"""

import asyncio
import logging

from temporalio import activity
from temporalio.exceptions import ApplicationError

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
from shared.mcp_tools.annotate_tool import annotate, McpBusinessError, McpInfraError

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_S = 1.0


@activity.defn(name="annotation")
async def annotation_activity(input: ActivityInput) -> dict:
    """标注 Activity — 调外部 MCP server 的作业级标注流程。

    业务失败（McpBusinessError）→ 转 ActivityOutput(ERROR)，不抛异常，不触发 Temporal 重试。
    基础设施失败（McpInfraError / 超时 / 网络）→ 抛 ApplicationError，Temporal 按 retry_policy 重试。
    """
    params = input.params or {}
    dataset_id = params.get("dataset_id")
    job_name = params.get("job_name")

    if not dataset_id or not job_name:
        return _to_dict(ActivityOutput(
            status=ActivityStatus.ERROR,
            error="annotation activity 缺少必填参数 dataset_id / job_name",
        ))

    annotate_kwargs = {
        "dataset_id": dataset_id,
        "job_name": job_name,
        "labels": params.get("labels"),
        "annotation_type": params.get("annotation_type", "CLASSIFICATION"),
    }
    annotate_kwargs = {k: v for k, v in annotate_kwargs.items() if v is not None}

    task = asyncio.create_task(annotate(**annotate_kwargs))
    try:
        while not task.done():
            _heartbeat_safe()
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
        mcp_result = task.result()
    except McpBusinessError as e:
        logger.warning("annotation 业务失败 dataset=%s: %s", dataset_id, e)
        return _to_dict(ActivityOutput(
            status=ActivityStatus.ERROR,
            error=f"MCP 业务失败: {e}",
            data={"dataset_id": dataset_id, "job_name": job_name, "mcp_error": str(e)},
        ))
    except McpInfraError as e:
        logger.error("annotation 基础设施失败 dataset=%s: %s", dataset_id, e)
        raise ApplicationError(f"MCP 基础设施失败: {e}") from e
    except ApplicationError:
        raise
    except Exception as e:
        logger.exception("annotation 未知异常 dataset=%s", dataset_id)
        raise ApplicationError(f"annotation 未知异常: {e}") from e

    return _to_dict(_to_activity_output(mcp_result, dataset_id, job_name))


def _to_activity_output(mcp_result: dict, dataset_id: str, job_name: str) -> ActivityOutput:
    """把 MCP 返回 dict 转成 ActivityOutput。"""
    status_str = mcp_result.get("status", "completed")
    if status_str in ("failed", "timeout"):
        return ActivityOutput(
            status=ActivityStatus.ERROR,
            error=f"MCP 返回 {status_str}: job_id={mcp_result.get('job_id')}",
            data={
                "dataset_id": dataset_id,
                "job_name": job_name,
                "job_id": mcp_result.get("job_id"),
                "raw": mcp_result,
            },
        )
    return ActivityOutput(
        status=ActivityStatus.OK,
        data={
            "dataset_id": dataset_id,
            "job_name": job_name,
            "job_id": mcp_result.get("job_id"),
            "status": "completed",
            "total_count": mcp_result.get("total_count", 0),
            "completed_count": mcp_result.get("completed_count", 0),
            "tasks": mcp_result.get("tasks", []),
            "raw": mcp_result.get("raw"),
        },
    )


def _to_dict(output: ActivityOutput) -> dict:
    return {"status": output.status.value, "data": output.data, "error": output.error}


def _heartbeat_safe() -> None:
    """在 activity context 内发心跳；脱离 context（单测）时静默跳过。"""
    try:
        activity.heartbeat()
    except RuntimeError:
        pass
