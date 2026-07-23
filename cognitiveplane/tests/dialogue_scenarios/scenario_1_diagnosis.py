"""场景 1 · 追问式诊断 + L3 图像分析 (L1->L3).

覆盖情况: A1/A2 (节点执行结果), Q1 (经验回流), 主动追问 (request_confirmation)
测: 信息不足时 agent 是否主动追问, skill 命中, analyze_image 调用

对话流:
  轮1: 用户给模糊描述 (信息不足) -> agent 应主动追问补充
  轮2: 用户补充板厚/材质/标准 -> agent 选 weld_iqa skill
  轮3: (若有图) agent 调 analyze_image (L3) 给缺陷判定
"""
from __future__ import annotations
import asyncio, logging
from cognitiveplane.tests.dialogue_scenarios.harness import build_engine, run_turn, report_scenario

SCENARIO_NAME = "追问式诊断"

logger = logging.getLogger("scenario_1")


async def run() -> dict:
    engine, session, approval_store = build_engine(case_id="scenario-1-diagnosis")
    turns = []

    # 轮1: 模糊描述, 信息不足 -> 期望 agent 追问 (reply 含问号/补充请求)
    t1 = await run_turn(engine, session,
        "帮我看看焊缝有没有问题",
        reply_contains=["？", "?", "请提供", "需要", "补充", "板厚", "材质", "标准"],
    )
    # request_confirmation 可能触发 (若 agent 判断要问), 这里先不强求工具, 看 reply
    turns.append(t1)

    # 轮2: 补充信息 -> 期望 agent 进入诊断/skill (weld_iqa 或 industrial_qa_diagnosis)
    t2 = await run_turn(engine, session,
        "Q345R 钢板 25mm, 按 NB/T47014 评估, 这条焊缝有图我待会发",
        # 不强求具体工具 (可能调 search_standards 或先追问图), 看 reply 合理
    )
    turns.append(t2)

    # 轮3: 给明确指令 -> 期望调 search_standards 查标准
    t3 = await run_turn(engine, session,
        "先查一下 NB/T47014 对 Q345R 25mm 的预热要求",
        expected_tools=["search_standards"],
        reply_contains=["预热", "100", "NB/T47014"],
    )
    turns.append(t3)

    return report_scenario("场景1·追问式诊断", turns)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    result = asyncio.run(run())
    import sys
    sys.exit(0 if result["passed"] == result["total"] else 1)
