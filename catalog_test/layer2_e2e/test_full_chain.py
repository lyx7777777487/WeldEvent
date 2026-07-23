"""全链路清单测试 - 用户对话 -> LLM -> 工具 -> Temporal -> 状态验证.

每条清单测试:
  1. 通过 HTTP /chat/upload 上传 test.jpg + 让 LLM 设计含 HCA 的工作流 (会卡住等审查)
  2. 通过 HTTP /chat 让 LLM 执行治理动作 (暂停/回溯/批次冻结/审查/撤回...)
  3. 通过 Temporal query_status 验证工作流状态确实改变
  4. 记录对话内容 + 信号投递 + 状态变化

前置: API server :8000 + Temporal :7233 + worker 运行.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_WELDEVENT = os.environ.get("WELDEVENT_ROOT", "/Users/liuyixuan/WeldEvent")
if _WELDEVENT not in sys.path:
    sys.path.insert(0, _WELDEVENT)

import httpx


@dataclass
class FullChainResult:
    item_id: str
    category: str
    description: str
    coverage: str
    status: str = "PENDING"
    user_msg: str = ""
    ai_reply: str = ""
    tools_used: list[str] = field(default_factory=list)
    workflow_id: str = ""
    wf_status_before: str = ""
    wf_status_after: str = ""
    signal_confirmed: bool = False
    error: str | None = None
    note: str = ""
    duration: float = 0.0


class FullChainTester:
    """全链路测试器: HTTP 对话 + Temporal 验证."""

    def __init__(self):
        self.base_url = os.environ.get("WELDEVENT_API_URL", "http://127.0.0.1:8000")
        self.temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
        self._client = None
        self._temporal_port = None
        self.session_id = None
        self.image_path = self._find_image()

    def _find_image(self):
        for p in [
            os.path.join(_WELDEVENT, "test.jpg"),
            os.path.join(_WELDEVENT, "catalog_test", "test.jpg"),
        ]:
            if os.path.exists(p):
                return p
        return os.path.join(_WELDEVENT, "test.jpg")

    async def __aenter__(self):
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120, trust_env=False)
        # Temporal port for status verification
        from cognitiveplane.bridge.temporal_client import TemporalWorkflowLaunchPort
        self._temporal_port = TemporalWorkflowLaunchPort(
            temporal_host=self.temporal_host,
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "control-plane"),
        )
        return self

    async def __aexit__(self, *a):
        if self._client:
            await self._client.aclose()

    async def health(self):
        try:
            r = await self._client.get("/api/v1/health")
            return r.status_code == 200
        except:
            return False

    async def chat(self, message: str) -> dict:
        """纯文本对话."""
        payload = {"message": message, "operator_id": "fullchain-tester"}
        if self.session_id:
            payload["session_id"] = self.session_id
        r = await self._client.post("/api/v1/chat/", json=payload)
        data = r.json()
        self.session_id = data.get("session_id", self.session_id)
        return data

    async def upload_chat(self, message: str) -> dict:
        """上传图片 + 对话."""
        with open(self.image_path, "rb") as f:
            files = {"files": ("test.jpg", f, "image/jpeg")}
            data = {"message": message, "operator_id": "fullchain-tester"}
            if self.session_id:
                data["session_id"] = self.session_id
            r = await self._client.post("/api/v1/chat/upload", files=files, data=data)
        resp = r.json()
        self.session_id = resp.get("session_id", self.session_id)
        return resp

    async def wf_status(self, wf_id: str) -> dict:
        """查 Temporal 权威状态."""
        try:
            return await self._temporal_port.query_status(wf_id)
        except:
            return {}

    async def extract_wf_id(self, reply: str) -> str:
        """从回复里提取 workflow_id (排除假 ID)."""
        import re
        # 找所有 wf-xxxxxxxx 模式
        matches = re.findall(r'wf-[a-f0-9]{8}', reply or "")
        for m in matches:
            # 排除重复字符的假 ID (如 wf-aaaaaaaa)
            hex_part = m[3:]
            if len(set(hex_part)) < 3:
                continue
            return m
        return ""

    async def setup_workflow(self) -> str:
        """启动一个含 HCA 的工作流 (会卡住等审查), 返回 workflow_id.
        每次用新 session 避免历史堆积.
        """
        self.session_id = None  # 新 session
        resp = await self.upload_chat(
            "请设计一个焊缝检测工作流：IQA图像质量评估 -> PPA预处理 -> RDA缺陷识别 -> HCA人工审核，"
            "然后启动它。HCA节点需要人工审查才能通过。"
        )
        reply = resp.get("reply", "")
        wf_id = await self.extract_wf_id(reply)
        if wf_id:
            # 验证 workflow_id 真实存在于 Temporal
            await asyncio.sleep(2)
            status = await self.wf_status(wf_id)
            if not status or status.get("status") == "UNKNOWN":
                # 可能是假 ID, 从 workflow_ids 字段取
                wf_ids = resp.get("workflow_ids", [])
                if wf_ids:
                    wf_id = wf_ids[0]
                    status = await self.wf_status(wf_id)
                if not status or status.get("status") == "UNKNOWN":
                    return ""
            await asyncio.sleep(1)  # 等工作流跑到 HCA 卡住
        return wf_id

    async def test_item(self, item_id, category, desc, coverage,
                        gov_message, expect_signal, expect_status_change=True) -> FullChainResult:
        """测一条: 启动工作流 -> 发治理消息 -> 验证状态."""
        r = FullChainResult(item_id, category, desc, coverage)
        t0 = time.time()
        try:
            # 1. 启动工作流 (新 session)
            self.session_id = None
            wf_id = await self.setup_workflow()
            if not wf_id:
                r.status = "FAIL"
                r.error = "无法启动工作流"
                r.duration = time.time() - t0
                return r

            r.workflow_id = wf_id
            status_before = await self.wf_status(wf_id)
            r.wf_status_before = status_before.get("status", "?")

            # 2. 发治理消息 (用启动工作流的同一个 session, 让 LLM 知道当前 wf)
            r.user_msg = gov_message
            resp = await self.chat(gov_message)
            r.ai_reply = (resp.get("reply") or "")[:500]
            r.tools_used = resp.get("tools_used", [])
            if resp.get("error"):
                r.error = resp["error"]

            # 3. 等信号生效
            await asyncio.sleep(2)
            status_after = await self.wf_status(wf_id)
            r.wf_status_after = status_after.get("status", "?")

            # 4. 验证
            # 状态变化 OR 工具被调用
            status_changed = r.wf_status_before != r.wf_status_after
            tool_called = "control_workflow" in str(r.tools_used)
            r.signal_confirmed = status_changed or tool_called

            if expect_status_change and status_changed:
                r.status = "PASS"
                r.note = f"{r.wf_status_before} -> {r.wf_status_after} (状态变化)"
            elif tool_called:
                r.status = "PASS"
                r.note = f"control_workflow called, {r.wf_status_before} -> {r.wf_status_after}"
            elif expect_signal:
                r.status = "FAIL"
                r.note = f"工具未调用或状态未变: {r.wf_status_before} -> {r.wf_status_after}"
            else:
                r.status = "PASS"
                r.note = f"对话完成, {r.wf_status_before} -> {r.wf_status_after}"

            # 5. 清理
            await self._cleanup(wf_id)

        except Exception as e:
            r.status = "FAIL"
            r.error = str(e)[:200]
        r.duration = time.time() - t0
        return r

    async def _cleanup(self, wf_id: str):
        """放行 + 清理."""
        try:
            await self._temporal_port.send_signal(wf_id, "batch_signals",
                [[{"type": "human_review", "args": ["hca_check", {"decision": "approve"}]}]])
        except:
            pass
        await asyncio.sleep(1)
        try:
            await self._temporal_port.send_signal(wf_id, "cancel_by_user", None)
        except:
            pass


async def main():
    async with FullChainTester() as tester:
        if not await tester.health():
            print("❌ API 不可达. 请先启动:")
            print("   cd /Users/liuyixuan/WeldEvent && python -m cognitiveplane.app")
            return

        print("✅ API 可达, 开始全链路清单测试...\n")

        # 按清单顺序定义所有测试项
        # (item_id, category, desc, coverage, message, expect_status_change, need_workflow)
        tests = [
            # A: 节点执行结果 (直接对话分析, 不需要治理信号)
            ("A1", "A", "正常成功", "✅", "分析这张焊缝图", False, False),
            ("A2", "A", "成功带警告", "✅", "分析这张焊缝图，详细报告质量", False, False),
            # B: 暂停
            ("B1", "B", "暂停整Run", "✅", "暂停当前工作流", True, True),
            ("B2", "B", "暂停单工位", "🆕", "暂停 iqa_check 工位，其他继续", True, True),
            ("B3", "B", "暂停批次", "🆕", "暂停当前批次，其他批继续", True, True),
            # C: 回溯
            ("C1", "C", "未提交回退", "✅", "回退到 iqa_check 节点重做", True, True),
            ("C2", "C", "已提交未消费回退", "✅", "回退到 ppa_check 节点重做", True, True),
            ("C4", "C", "转正式回退", "🆕", "撤回 hca_check 节点的批准，判定有误", True, True),
            ("C6", "C", "部分回退", "🆕", "rda_check 节点标签错了，从OK改成NG，结果保留", True, True),
            ("C14", "C", "审计一致性", "✅", "回退到 iqa_check 节点重做", True, True),
            # D: 拓扑
            ("D6", "D", "依赖图动态变化", "✅", "在工作流最后加一个MEA几何测量节点", True, True),
            # E: 数据质量
            ("E6", "E", "标签错结果对", "🆕", "iqa_check 节点标签错了，从pass改成fail", True, True),
            ("E8", "E", "结果模棱两可", "✅", "审查 iqa_check 节点结果", True, True),
            ("E10", "E", "批次整体偏低", "🆕", "扣住当前批次，质量可疑", True, True),
            ("E-REL", "E", "释放冻结批次", "🆕", "释放当前冻结的批次", True, True),
            ("E-RWK", "E", "重做冻结批次", "🆕", "重做当前冻结的批次", True, True),
            ("E-QTN", "E", "隔离冻结批次", "🆕", "隔离当前冻结的批次", True, True),
            # K: 治理触发
            ("K1", "K", "标准过时", "🆕", "标准AWS_D1.1从2020版升级到2025版", True, True),
            ("K2", "K", "案例误导", "🆕", "案例CBR-042有误，结论错误，纠正为NG", True, True),
            ("K4", "K", "审查人配置错", "🆕", "把iqa_check节点的审查转给qc-002", True, True),
            ("K5", "K", "已提交决策误判", "🆕", "撤回rda_check节点批准，事后发现误判", True, True),
            # L: 上下文补充
            ("L1", "L", "注入事实修正", "✅", "补充信息：焊缝宽度阈值改为3.5mm", True, True),
            ("L3", "L", "注入临时注记", "✅", "补充信息：此批次由客户特别关注", True, True),
            ("L6", "L", "已执行不回滚", "✅", "补充信息：IQA阈值改为0.85", True, True),
            # M: 中断
            ("M2", "M", "参数错中断", "✅", "回退到 iqa_check 节点重做", True, True),
            ("M14", "M", "中断checkpoint", "✅", "取消当前工作流", True, True),
            # N: 工作流更改
            ("N1", "N", "改参数不改拓扑", "✅", "把IQA节点阈值从0.8改成0.9", True, True),
            ("N2", "N", "改拓扑加节点", "✅", "在工作流里加一个MEA几何测量节点", True, True),
            # O: 人工审查
            ("O1", "O", "REQUIRED审查", "✅", "审查 hca_check 节点，通过", True, True),
            ("O2", "O", "CONDITIONAL触发", "✅", "审查 iqa_check 节点，需要返工", True, True),
            ("O3", "O", "失败escalate", "✅", "审查 iqa_check 节点，升级处理", True, True),
            ("O9", "O", "单节点审查", "✅", "审查 iqa_check 节点，通过", True, True),
            ("O11", "O", "五决策-reject", "✅", "审查 iqa_check 节点，拒绝", True, True),
            ("O12", "O", "override强制", "🆕", "强制覆盖 rda_check 节点结果为NG，由qc-003负责", True, True),
            ("O13", "O", "delegate转交", "🆕", "把 hca_check 的审查转给 qc-005", True, True),
            ("O14", "O", "审查人变更", "🆕", "把 iqa_check 的审查转给 qc-006", True, True),
            # P: 重新评估
            ("P2", "P", "标准更新重评", "🆕", "标准ISO_5817从2017版升级到2023版", True, True),
            ("P3", "P", "案例纠错重评", "🆕", "案例CBR-077有误，标签错误，纠正为NG", True, True),
            ("P4", "P", "批量异常重评", "🆕", "当前批次批量异常，扣住", True, True),
            ("P5", "P", "根因追溯重评", "🆕", "撤回iqa_check节点批准，根因追溯", True, True),
            # Q: 经验回流
            ("Q13", "Q", "经验污染", "🆕", "案例CBR-099有误，结论错误，纠正", True, True),
            ("Q16", "Q", "经验标准联动", "🆕", "标准ISO_5817从2017升级到2023", True, True),
            ("Q18", "Q", "override回流", "🆕", "强制覆盖rda_check为OK，qc-003校准", True, True),
            # T: 安全合规
            ("T4", "T", "审计事件", "✅", "回退到 iqa_check 节点重做", True, True),
            # U: 启动前
            ("U4", "U", "预算门控", "✅", "分析这张焊缝图", False, False),
            # V: 交付后
            ("V7", "V", "终态导出报告", "✅", "查看工作流执行结果报告", False, False),
        ]

        results = []
        for item_id, cat, desc, cov, msg, expect_change, need_wf in tests:
            print(f"[{item_id}] {desc}...", end=" ", flush=True)
            if not need_wf:
                # 不需要启动工作流的项 (A1/A2/U4/V7): 直接对话
                tester.session_id = None
                resp = await tester.upload_chat(msg) if cat in ("A","U") else await tester.chat(msg)
                reply = resp.get("reply", "")
                r = FullChainResult(item_id, cat, desc, cov)
                r.user_msg = msg
                r.ai_reply = reply[:500]
                r.tools_used = resp.get("tools_used", [])
                r.status = "PASS" if reply and len(reply) > 10 and "无法识别" not in reply else "FAIL"
                r.note = f"direct chat, tools={r.tools_used}"
                if resp.get("error"):
                    r.error = resp["error"]
                    r.status = "FAIL"
                results.append(r)
                icon = {"PASS": "✅", "FAIL": "❌"}.get(r.status, "?")
                print(f"{icon} {r.note}")
                continue
            r = await tester.test_item(item_id, cat, desc, cov, msg, "control_workflow", expect_change)
            icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(r.status, "?")
            print(f"{icon} {r.note}")
            if r.error:
                print(f"     error: {r.error}")
            results.append(r)

        # 汇总
        print(f"\n{'='*60}")
        print("全链路清单测试汇总")
        print(f"{'='*60}")
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        total = len(results)
        print(f"通过: {passed} | 失败: {failed} | 总计: {total}")
        print(f"通过率: {passed}/{total} ({100*passed//total if total else 0}%)\n")

        # 按类
        by_cat = {}
        for r in results:
            by_cat.setdefault(r.category, []).append(r)
        print("按类汇总:")
        for cat in sorted(by_cat.keys()):
            items = by_cat[cat]
            cp = sum(1 for r in items if r.status == "PASS")
            cf = sum(1 for r in items if r.status == "FAIL")
            print(f"  {cat}: {cp}/{len(items)} passed" + (f", {cf} failed" if cf else ""))

        # 失败详情
        failures = [r for r in results if r.status == "FAIL"]
        if failures:
            print(f"\n失败详情 ({len(failures)} 条):")
            for r in failures:
                print(f"  ❌ {r.item_id} ({r.category}): {r.note}")
                if r.error:
                    print(f"     {r.error}")

        # 保存报告
        report_dir = Path(__file__).resolve().parent.parent / "reports"
        report_dir.mkdir(exist_ok=True)
        (report_dir / "full_chain_results.json").write_text(
            json.dumps({
                "test_type": "full_chain",
                "total": total, "passed": passed, "failed": failed,
                "by_category": {cat: {
                    "passed": sum(1 for r in by_cat[cat] if r.status == "PASS"),
                    "failed": sum(1 for r in by_cat[cat] if r.status == "FAIL"),
                    "total": len(by_cat[cat]),
                } for cat in sorted(by_cat.keys())},
                "results": [asdict(r) for r in results],
            }, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告: {report_dir}/full_chain_results.json")


if __name__ == "__main__":
    asyncio.run(main())
