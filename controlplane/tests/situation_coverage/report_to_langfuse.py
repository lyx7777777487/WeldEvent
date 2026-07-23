"""把情况覆盖矩阵报告作为 score 上报到 Langfuse.

每次跑覆盖矩阵时, 把 (tested/covered/pending) 三个数值作为 score 挂到一条
固定的 "situation-coverage" trace 上. Langfuse 看板即可看到覆盖度随时间
变化的趋势线, 评估进度可视化.

用法:
    python -m controlplane.tests.situation_coverage.report_to_langfuse
    # 会先跑覆盖矩阵再上报
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 确保能 import
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from controlplane.tests.situation_coverage.coverage_tracker import (
    load_situations, scan_tests, build_report,
)


def _load_env() -> None:
    env = Path(__file__).resolve().parents[3] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_env()
    situations = load_situations()
    found, all_tests = scan_tests()
    rep = build_report(situations, found, all_tests)

    pct = rep.tested / rep.total * 100 if rep.total else 0
    print(f"覆盖度: tested={rep.tested}/{rep.total} ({pct:.1f}%)")

    # 检查 Langfuse 是否启用
    try:
        import importlib
        import cognitiveplane.adapters.observability.tracing as t
        importlib.reload(t)
        t.setup_tracing()
        if not t._LANGFUSE_ENABLED:
            print("[langfuse] 未启用 (无 key), 跳过上报. 本地仅打印报告.")
            return 0
        client = t.get_langfuse()
    except Exception as e:
        print(f"[langfuse] 初始化失败 ({e}), 跳过上报")
        return 0

    # v4 API: start_as_current_observation 作 context manager, 内部 score 挂到当前 trace
    with client.start_as_current_observation(name="situation-coverage", as_type="span") as span:
        span.update(output={"total": rep.total, "tested": rep.tested,
                            "covered": rep.covered, "pending": rep.pending})
        trace_id = client.get_current_trace_id()

        def _score(name, value, comment):
            try:
                client.score_current_trace(name=name, value=value,
                                            data_type="NUMERIC", comment=comment)
            except Exception as e:
                print(f"[langfuse] score {name} 上报失败: {e}")

        _score("situation_tested", rep.tested, f"tested {rep.tested}/{rep.total}")
        _score("situation_covered", rep.covered, f"covered {rep.covered}/{rep.total}")
        _score("situation_pending", rep.pending, f"pending {rep.pending}/{rep.total}")
        _score("situation_tested_pct", round(pct, 1), f"{pct:.1f}% tested")

        for tier in ["L0", "L1", "L2", "L3"]:
            tdata = rep.by_tier.get(tier, {})
            total = tdata.get("total", 0)
            tested = tdata.get("tested", 0)
            if total:
                _score(f"tier_{tier}_tested_pct", round(tested / total * 100, 1),
                       f"{tier} tested {tested}/{total}")

    t.flush()
    print(f"[langfuse] 已上报到 trace_id={trace_id}")
    print(f"[langfuse] jp dashboard -> Traces -> 'situation-coverage' 查看趋势")
    return 0


if __name__ == "__main__":
    sys.exit(main())
