"""MCPRegistry — 发现 + 三层 ToolPolicy 判定 + 注册到 ToolRegistry.

Spec §2.4 + §3.1 + §3.3 — 薄桥接，不持有工具实现，只持有 MCPServer
引用和判定缓存。list_changed 事件触发重判 + 更新 ToolRegistry。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cognitiveplane.adapters.mcp.base import (
    MCPAdapter,
    MCPPolicyDecision,
    MCPServer,
    ToolDescriptor,
)
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)
from cognitiveplane.control.event_log import BrainEventType, EventLog

logger = logging.getLogger(__name__)


@dataclass
class _CachedEntry:
    """一个 server 的工具列表 + 判定结果缓存。"""
    server_name: str
    descriptors: list[ToolDescriptor]
    policies: dict[str, MCPPolicyDecision]  # tool_name → decision


class MCPRegistry:
    """MCP 工具发现 + 三层判定 + 注册到 ToolRegistry。

    使用:
        registry = MCPRegistry(classifier=..., event_log=...)
        await registry.register_server(server1)
        await registry.register_server(server2)
        await registry.register_all(tool_registry)
    """

    def __init__(
        self,
        classifier: MCPToolPolicyClassifier,
        event_log: EventLog | None = None,
    ) -> None:
        self._classifier = classifier
        self._event_log = event_log
        # server_name → (MCPServer, _CachedEntry)
        self._servers: dict[str, tuple[MCPServer, _CachedEntry]] = {}
        # tool_registry 引用 — register_all 时设置，list_changed 时用
        self._tool_registry: "ToolRegistry | None" = None  # type: ignore[name-defined]

    async def register_server(self, server: MCPServer) -> None:
        """注册一个 MCPServer — 拉取工具列表、跑三层判定、缓存。

        Spec §3.1 step 5.
        """
        try:
            descriptors = await server.list_tools()
        except Exception as e:
            logger.warning("MCP server %s list_tools failed: %s — skipping", server.name, e)
            if self._event_log is not None:
                self._event_log.emit(
                    BrainEventType.TOOL_RESULT,
                    source="mcp_registry",
                    data={
                        "event": "list_tools_failed",
                        "server": server.name,
                        "error": str(e),
                    },
                )
            return

        policies: dict[str, MCPPolicyDecision] = {}
        for desc in descriptors:
            try:
                policies[desc.name] = self._classifier.classify(desc)
            except Exception as e:
                logger.warning(
                    "MCP tool %s policy classify failed: %s — fallback conservative",
                    desc.name,
                    e,
                )
                policies[desc.name] = MCPPolicyDecision(
                    auto_approve=False, tier="B", reason="classify_error:conservative"
                )

        self._servers[server.name] = (
            server,
            _CachedEntry(
                server_name=server.name,
                descriptors=descriptors,
                policies=policies,
            ),
        )

        # 订阅 list_changed
        server.on_list_changed(lambda s=server: self._handle_list_changed(s))

        if self._event_log is not None:
            for desc in descriptors:
                policy = policies[desc.name]
                self._event_log.emit(
                    BrainEventType.TOOL_CALL,
                    source="mcp_registry",
                    data={
                        "event": "tool_registered",
                        "server": server.name,
                        "tool": desc.name,
                        "policy": {
                            "auto_approve": policy.auto_approve,
                            "tier": policy.tier,
                            "require_reason": policy.require_reason,
                            "reason": policy.reason,
                        },
                    },
                )

    def list_cached_tools(self) -> list[tuple[ToolDescriptor, MCPPolicyDecision]]:
        """返回所有已注册 server 的 (descriptor, policy) 列表 — 测试用。"""
        result: list[tuple[ToolDescriptor, MCPPolicyDecision]] = []
        for _, entry in self._servers.values():
            for desc in entry.descriptors:
                result.append((desc, entry.policies[desc.name]))
        return result

    async def register_all(self, tool_registry: "ToolRegistry") -> None:  # type: ignore[name-defined]
        """把所有已注册 server 的工具注册到 ToolRegistry。

        Spec §3.1 step 6 — 为每个 (descriptor, policy) 创建 MCPAdapter
        并注册到 ToolRegistry。
        """
        self._tool_registry = tool_registry
        for server, entry in self._servers.values():
            for desc in entry.descriptors:
                policy = entry.policies[desc.name]
                adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)
                tool_registry.register(adapter)

    async def _handle_list_changed(self, server: MCPServer) -> None:
        """list_changed 事件 — 重新拉工具列表、diff、重判、更新 ToolRegistry。

        Spec §3.3 + §5 — 重载失败时保留旧工具列表不动。
        """
        try:
            new_descriptors = await server.list_tools()
        except Exception as e:
            logger.warning(
                "MCP server %s list_changed re-fetch failed: %s — keeping old tools",
                server.name,
                e,
            )
            if self._event_log is not None:
                self._event_log.emit(
                    BrainEventType.TOOL_RESULT,
                    source="mcp_registry",
                    data={
                        "event": "list_changed_refetch_failed",
                        "server": server.name,
                        "error": str(e),
                    },
                )
            return

        old_tuple = self._servers.get(server.name)
        old_entry: _CachedEntry | None = old_tuple[1] if old_tuple is not None else None
        old_names = {d.name for d in old_entry.descriptors} if old_entry else set()
        new_names = {d.name for d in new_descriptors}

        added = new_names - old_names
        removed = old_names - new_names
        # changed = same name but different descriptor
        old_by_name = {d.name: d for d in (old_entry.descriptors if old_entry else [])}
        changed = {
            d.name
            for d in new_descriptors
            if d.name in old_names and d != old_by_name[d.name]
        }

        # 重判 + 更新缓存
        new_policies: dict[str, MCPPolicyDecision] = {}
        for desc in new_descriptors:
            new_policies[desc.name] = self._classifier.classify(desc)

        self._servers[server.name] = (
            server,
            _CachedEntry(
                server_name=server.name,
                descriptors=new_descriptors,
                policies=new_policies,
            ),
        )

        # 更新 ToolRegistry
        if self._tool_registry is not None:
            for name in removed:
                self._tool_registry.unregister(name)
            for desc in new_descriptors:
                if desc.name in added or desc.name in changed:
                    policy = new_policies[desc.name]
                    adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)
                    self._tool_registry.register(adapter)  # register 覆盖同名

        if self._event_log is not None:
            self._event_log.emit(
                BrainEventType.TOOL_CALL,
                source="mcp_registry",
                data={
                    "event": "list_changed_processed",
                    "server": server.name,
                    "added": sorted(added),
                    "removed": sorted(removed),
                    "changed": sorted(changed),
                },
            )