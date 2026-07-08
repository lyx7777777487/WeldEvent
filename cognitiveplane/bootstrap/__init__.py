"""Bootstrap package — 装配函数 re-export。

从 app.py 拆出的装配逻辑统一入口：
  bootstrap_llm                — LLM provider 装配
  build_llm_cache              — LLM 响应缓存构造
  build_dependencies           — CognitiveDependencies 装配
  build_tool_registry_for_mcp  — MCP ToolRegistry 构造
  build_seed_knowledge         — 6 类种子数据构造
"""

from cognitiveplane.bootstrap.llm_setup import (
    bootstrap_llm,
    build_llm_cache,
    probe_chat,
)
from cognitiveplane.bootstrap.dependencies import (
    build_dependencies,
    build_tool_registry_for_mcp,
)
from cognitiveplane.bootstrap.seed_knowledge import build_seed_knowledge

__all__ = [
    "bootstrap_llm",
    "build_llm_cache",
    "probe_chat",
    "build_dependencies",
    "build_tool_registry_for_mcp",
    "build_seed_knowledge",
]
