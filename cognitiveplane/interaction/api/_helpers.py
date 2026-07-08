"""Shared helpers for chat API handler modules.

Extracted from chat.py to avoid circular imports and enable reuse across
chat_handlers / stream_handlers / etc. This module must NOT import from
chat.py (chat.py imports this module at top level).

Source: 7-plane redesign spec §7 FastAPI + plan §2.3 + §A.3.
"""

from dataclasses import dataclass
from fastapi import APIRouter
from cognitiveplane.governance.evaluation import EvaluationInput
import logging
import sys

logger = logging.getLogger("chat_api")
# 确保 INFO 级别日志能输出到 stdout（uvicorn 默认只配自己的 logger）
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 避免重复输出


def _inject_session_image_refs(
    message: str, session_id: str, router: APIRouter,
) -> str:
    """将 session 之前已上传的 image_refs 注入到用户消息中。

    不注入则 LLM 在后续请求中不知道有哪些图片可用，
    导致 design_workflow 不传 image_refs → L3 IQA 拿不到图片。
    """
    image_sessions = getattr(router, "image_sessions", None)
    if image_sessions is None:
        return message
    image_ids = image_sessions.list_images(session_id)
    if not image_ids:
        return message
    id_list = ", ".join(image_ids)
    return (
        f"{message}\n\n"
        f"[系统：本会话之前已上传 {len(image_ids)} 张图片。"
        f"可用 image_ref: {id_list}。"
        f"调用 analyze_image(image_ref=\"<ref>\", ...) 分析图片。"
        f"**多图时必须逐张分析**：每轮迭代只调一次 analyze_image，"
        f"禁止同一轮并发调用 2+ 次，否则视觉 API 限流导致超时。"
        f"调用 design_workflow 时必须把上述 image_ref 清单完整传入"
        f" image_refs 参数（数组形式），"
        f"否则下游 L3 activity 无法获取图片。]"
    )


def _get_or_create_session(session_manager, operator_id, session_id, case_id):
    """获取或创建会话（复用前端传的 session_id，保证历史对话累积）。"""
    return session_manager.get_or_create_session(operator_id, session_id, case_id)


@dataclass
class _EvalProxy:
    """评估代理对象 — 兼容 InteractionResponse / ChatResponse 字段差异。"""
    text_reply: str = ""
    tools_used: list[str] | None = None


def _trigger_evaluation(
    evaluator,
    user_input: str,
    response,
    session_id: str,
    trace_id: str | None = None,
) -> None:
    """Phase 5: 在线评估 — 异步打分，不阻塞返回。

    response 可以是 InteractionResponse（有 text_reply）或 ChatResponse（有 reply）。
    """
    if not getattr(evaluator, "_enabled", True):
        return
    reply = getattr(response, "text_reply", None) or getattr(response, "reply", "") or ""
    tools_used = getattr(response, "tools_used", []) or []
    evaluator.evaluate_background(
        EvaluationInput(
            user_input=user_input,
            reply=reply,
            tools_used=tools_used,
            session_id=session_id,
            trace_id=trace_id,
        )
    )


def prepare_session_context(
    message: dict, session_id: str, session_manager, router,
) -> tuple[str, list[dict]]:
    """共享: 注入 image_refs + 取 session.history.

    HTTP /stream 和 WS /ws 共用此函数, 保证两路径行为一致.
    agent_loop._prepare_session_context 和 stream_handlers.handle_chat_stream
    都调用此函数, 消除重复实现.

    Returns:
        (user_msg_with_image_refs, session_history)
    """
    raw_msg = message.get("message", "")
    user_msg = _inject_session_image_refs(raw_msg, session_id, router) if router is not None else raw_msg
    session_history: list[dict] = []
    if session_manager is not None:
        try:
            operator_id = message.get("operator_id", "operator-001")
            sess = session_manager.get_or_create_session(operator_id, session_id, None)
            session_history = list(getattr(sess, "messages", []) or [])
        except Exception:
            logger.debug("get session history failed", exc_info=True)
    return user_msg, session_history


def persist_session(
    session_manager, session_id: str, user_msg: str,
    final_reply: str, final_reasoning: str | None,
    operator_id: str = "operator-001",
) -> None:
    """共享: 流结束后把本轮 user/assistant 消息追加到 session.messages.

    HTTP /stream 的 finally 块和 WS /ws 的 _persist_session 共用此函数,
    消除重复实现.
    """
    if session_manager is None or not final_reply:
        return
    try:
        sess = session_manager.get_or_create_session(operator_id, session_id, None)
        sess.messages.append({"role": "user", "content": user_msg})
        assistant_msg: dict = {"role": "assistant", "content": final_reply}
        if final_reasoning:
            assistant_msg["reasoning_content"] = final_reasoning
        sess.messages.append(assistant_msg)
    except Exception:
        logger.debug("persist session failed", exc_info=True)
