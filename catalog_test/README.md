# WeldEvent 清单测试模块 (catalog_test)

对 `WeldEvent_执行期情况流穷举清单.md` 的每一条功能进行自动化测试。

## 两层架构

### L1 工具级测试 (layer1_tool/)
- **不启动系统**, 直接调 `WorkflowControlTool` + `FakeConnector`
- 验证: 每个 governance action 发送了正确的 signal + payload
- 验证: tier2 确认门 (revoke/override/case_correction) 的 approve/reject 行为
- 验证: modify_spec 参数变更 vs 拓扑变更的判别
- 验证: 缺必填参数 / bridge 失败的报错
- **232 个测试**, 27 秒完成

### L2 端到端测试 (layer2_e2e/)
- **启动系统**, 通过 HTTP API 真实对话
- 上传 `test.jpg` -> "分析这张焊缝图" -> design workflow -> launch
- 测试: 暂停/恢复/取消、批次冻结、回溯、注入上下文、人工审查、撤回、override...
- 记录: 对话内容、弹窗时机、用户选择
- **15 个 E2E 场景**, 需要 API+Temporal+worker 运行

## 快速开始

```bash
# 1. 仅 L1 (不需要系统运行)
cd /Users/liuyixuan/WeldEvent  # 或本模块所在目录
python -m catalog_test.run_all --l1

# 2. L1 并行 (10 并发, 需 pytest-xdist)
python -m catalog_test.run_all --l1 --parallel 10

# 3. 全部 (L1 + L2, 需系统运行)
# 先启动系统:
cd /Users/liuyixuan/WeldEvent
python -m controlplane.worker &          # L2 Temporal worker
python -m cognitiveplane.app &          # L1 API server
# 再跑测试:
WELDEVENT_RUN_E2E=1 python -m catalog_test.run_all --all

# 4. 直接用 pytest
python -m pytest catalog_test/layer1_tool/ -v          # L1
WELDEVENT_RUN_E2E=1 python -m pytest catalog_test/layer2_e2e/ -v -s  # L2
```

## 清单覆盖

| 指标 | 数值 |
|---|---|
| 清单总条目 | 283 |
| 大类 | 22 (A-V) |
| 可测条目 (✅+🆕) | 130 |
| ❓ 未实现 | 146 |
| L1 可测 (有 governance action) | 64 |
| L2 可测 (需真实 workflow) | 78 |
| L1 governance action 种类 | 18 |

## 文件结构

```
catalog_test/
├── catalog_map.py              # 清单条目 -> 测试场景映射 (source of truth)
├── conftest.py                 # pytest 全局配置 (sys.path + fixtures)
├── run_all.py                  # 一键运行入口
├── layer1_tool/                # L1 工具级测试
│   ├── fake_connector.py       # 模拟 EventConnector (记录 signal)
│   ├── fixtures.py             # 工具构造 fixtures
│   ├── signal_router.py        # action -> signal name 路由
│   ├── test_catalog_l1.py      # 参数化遍历 64 条 L1 条目
│   ├── test_confirm_gate.py    # tier2 确认门 (approve/reject)
│   ├── test_modify_spec.py     # 参数 vs 拓扑变更判别
│   ├── test_validation.py      # 参数校验 / 错误处理
│   └── test_basic_control.py   # pause/resume/cancel/query/rework/inject
├── layer2_e2e/                 # L2 端到端测试
│   ├── client.py               # 异步 HTTP API 客户端
│   ├── recorder.py             # 对话记录器 (对话/弹窗/选择)
│   ├── scenarios.py            # 15 个 E2E 场景定义
│   ├── report.py               # 合并报告生成器
│   └── test_e2e_scenarios.py    # pytest E2E 测试
└── reports/                    # 测试报告输出
    ├── combined_report.md      # L1+L2 合并报告
    ├── e2e_results.json        # L2 原始数据
    └── e2e_report.md           # L2 人类可读报告
```

## 测试的 18 种 governance action

| Action | 信号 | Tier2 | 清单条目 |
|---|---|---|---|
| pause | pause | - | B1, B4, B7 |
| resume | resume | - | (配对) |
| cancel | cancel_by_user | - | B11, M14 |
| rework_node | rework_node | - | A3, C1-C3, C14, D3, I6, M2, T4 |
| inject_context | inject_context | - | L1, L3, L6-L8 |
| modify | modify_spec | - | N1-N3, N7, N8, N10 |
| pause_scope | batch_signals | - | B2, B3 |
| resume_scope | batch_signals | - | B-RES |
| batch_hold | batch_signals | - | E10, F5, K6, P4 |
| release_hold | batch_signals | - | E-REL |
| rework_batch | batch_signals | - | E-RWK |
| quarantine_batch | batch_signals | ✅ | E-QTN |
| human_review | batch_signals | - | E8, O1-O3, O9, O11, O19 |
| relabel_request | batch_signals | - | C6, E6 |
| delegate_review | batch_signals | - | K4, O13, O14 |
| standard_update | batch_signals | - | K1, P2, P25, Q16 |
| revoke_approval | batch_signals | ✅ | C4, K5, K7, O8, P5, P27, P28 |
| ground_truth_override | batch_signals | ✅ | O12, O20, Q18 |
| case_library_correction | batch_signals | ✅ | K2, P3, Q13, Q17 |

## 已知问题

- **pause/resume/cancel 不检查 send_signal 返回值**: `_send_signal` 调 `connector.send_signal()` 后不检查返回值, bridge 返回 False 也报 ok=True. governance action 会检查 (正确), 但基础控制不检查.
