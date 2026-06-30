"""Integrations — 外部 MCP 服务接入层。

每个子模块对应一个外部 MCP 服务（技术员独立维护的 server），
封装该服务的工具调用为 Python 异步 API。

与 mcp/ 的区别:
  - mcp/: 纯协议层（protocol + client ABC），不绑定具体业务
  - integrations/: 业务层，每个子模块对应一个具体的外部 MCP 服务

当前接入:
  - label_studio: 标注系统 MCP 服务（接入技术员设计的接口）
"""
