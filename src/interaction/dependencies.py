"""Dependency injection for Interaction Layer modes.

ModeDependencies (per-request container) and PortProvider (global registry).
Source: L1-Interaction-Layer-Business-Requirements.md §4.4.
"""

from typing import Any


class ModeDependencies:
    """Per-request dependency container for mode handle() calls.

    Modes access their required ports via `deps.get("PortName")`.
    """

    def __init__(self, **ports: Any) -> None:
        self._ports: dict[str, Any] = ports

    def get(self, port_name: str) -> Any:
        """Get a port by name. Raises KeyError if not available."""
        if port_name not in self._ports:
            raise KeyError(f"Port '{port_name}' not available in ModeDependencies")
        return self._ports[port_name]

    def has(self, port_name: str) -> bool:
        """Check if a port is available."""
        return port_name in self._ports

    @property
    def available_ports(self) -> list[str]:
        """List all available port names."""
        return list(self._ports.keys())


class PortProvider:
    """Global port registry — builds ModeDependencies per request.

    Register ports at startup, then build() per mode handle() call
    with only the ports that mode requires.
    """

    def __init__(self) -> None:
        self._ports: dict[str, Any] = {}

    def register(self, port_name: str, port: Any) -> None:
        """Register a port instance by name."""
        self._ports[port_name] = port

    def build(self, required_ports: list[str]) -> ModeDependencies:
        """Build a ModeDependencies with only the required ports."""
        ports = {}
        for name in required_ports:
            if name in self._ports:
                ports[name] = self._ports[name]
        return ModeDependencies(**ports)
