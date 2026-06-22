# AGENTS.md

## 当前阶段

**Phase 2 — LLM 能看图说话**（进行中）

阶段定义见 `docs/superpowers/plans/2026-06-15-capability-loops-redesign.md` §十一。
阶段 2 验收标准见同文档 §11.4。

阶段 2 期间禁止：
- 引入任何 MCP 工具（detect_defects/annotate_label 推迟阶段 3）
- 引入 AgentLoop 双 task 模型（推迟阶段 3）
- 引入 switch_role / manage_plan / spawn_investigator 工具
- 引入 request_image_detail 工具（阶段 2 仅用 Thumbnail）
- 修改 §A.2 中标注"阶段 4+ 引入"的任何组件

## LLM Provider

- ReAct 主循环：DeepSeek-Chat（文本推理，32K 上下文）
- 多模态调用：Volc Doubao-Vision-Pro-32K（通过 `vision_complete`，独立预算）
- 视觉调用不进 ReAct 主循环上下文，走 Capability 层独立路径
