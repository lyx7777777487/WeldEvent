"""WeldEvent shared 包 — 跨层共享的中立基础设施。

设计原则:
  - 只放"传输/协议/数据结构"等纯中立代码，不包含任何业务逻辑
  - L1 认知层、L2 控制层、L3 执行层均可依赖此包，此包不依赖任何层
  - 各层的业务 ABC 保留在各层（如 cognitiveplane.adapters.mcp.base.MCPClient
    是 L1 业务 ABC，不放这里）

当前内容:
  - mcp/: MCP 协议消息层 + 传输层（StdioMCPClient / HTTPMCPClient）

参考:
  - boundary-pinning 2026-06-25 §1.5 "工业执行 MCP 永远不暴露给 Brain"
  - "传输层不共享"是误解 — 该契约约束的是工具实现，不是传输字节流
"""
