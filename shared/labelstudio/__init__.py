"""Label Studio 共享配置 + 认证 — 跨 L1/L3 共享。

设计:
  - config.LabelStudioConfig: 标注平台连接配置（URL/认证/超时），从环境变量加载
  - auth.login_labelstudio: 使用 username/password 登录获取 JWT token

不放业务方法（list_datasets/get_dataset 等）— 那些保留在各层:
  - L1 (cognitiveplane.adapters.mcp.label_studio_server): 通过 MCPAdapter
    直接调工具名，返回 dict
  - L3 (executionplane.integrations.label_studio.client.LabelStudioMCPClient):
    封装 9 个类型安全的业务方法，返回 ToolCallResult

向后兼容:
  - L1/L3 文件 re-export LabelStudioConfig，现有 import 不中断
"""
from __future__ import annotations

from shared.labelstudio.auth import login_labelstudio
from shared.labelstudio.config import LabelStudioConfig

__all__ = ["LabelStudioConfig", "login_labelstudio"]
