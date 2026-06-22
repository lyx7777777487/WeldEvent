"""SupervisorCenter — health check, loop detection, timeout detection, fallback.

Source: 7-plane redesign spec §5 SupervisorCenter.
Monitors decision pipeline execution for health, loops, and timeouts.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from cognitiveplane.shared.enums import FallbackMode, ReasoningMode


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    status: HealthStatus
    checks: dict[str, bool] = field(default_factory=dict)
    message: str = ""


@dataclass
class LoopDetectionResult:
    loop_detected: bool
    pattern: str | None = None
    iterations: int = 0


@dataclass
class TimeoutDetectionResult:
    timed_out: bool
    elapsed_ms: int = 0
    limit_ms: int = 0


class SupervisorCenter:
    """Monitors decision pipeline execution.

    Source: 7-plane redesign spec §5 SupervisorCenter.
    Responsibilities:
    1. Health Check — verify subsystem availability
    2. Loop Detection — detect repeated tool calls or state transitions
    3. Timeout Detection — enforce decision pipeline time limits
    4. Fallback — select fallback strategy when issues detected
    """

    def __init__(
        self,
        max_iterations: int = 10,
        timeout_ms: int = 30000,
        loop_window: int = 5,
    ) -> None:
        self._max_iterations = max_iterations
        self._timeout_ms = timeout_ms
        self._loop_window = loop_window
        self._execution_start: datetime | None = None
        self._tool_call_history: list[str] = []
        self._state_transition_history: list[str] = []

    def start_execution(self) -> None:
        """Mark the start of a decision pipeline execution."""
        self._execution_start = datetime.now(timezone.utc)
        self._tool_call_history = []
        self._state_transition_history = []

    def record_tool_call(self, tool_name: str) -> None:
        """Record a tool call for loop detection."""
        self._tool_call_history.append(tool_name)

    def record_state_transition(self, from_state: str, to_state: str) -> None:
        """Record a state transition for loop detection."""
        self._state_transition_history.append(f"{from_state}->{to_state}")

    def health_check(self, subsystems: dict[str, bool] | None = None) -> HealthCheckResult:
        """Check health of subsystems.

        Returns HEALTHY if all available, DEGRADED if some unavailable,
        UNHEALTHY if critical subsystems unavailable.
        """
        if subsystems is None:
            return HealthCheckResult(status=HealthStatus.HEALTHY, message="No subsystems to check")

        checks = dict(subsystems)
        available = sum(1 for v in checks.values() if v)
        total = len(checks)

        if available == total:
            status = HealthStatus.HEALTHY
        elif available > 0:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.UNHEALTHY

        return HealthCheckResult(
            status=status,
            checks=checks,
            message=f"{available}/{total} subsystems available",
        )

    def detect_loop(self) -> LoopDetectionResult:
        """Detect repeated tool call or state transition patterns.

        A loop is detected when the same tool name appears 3+ times
        in the last loop_window calls, or the same state transition
        appears 3+ times in the last loop_window transitions.
        """
        window = self._tool_call_history[-self._loop_window:] if self._tool_call_history else []

        if len(window) >= 3:
            # Check for repeated tool calls
            from collections import Counter
            counts = Counter(window)
            for tool_name, count in counts.items():
                if count >= 3:
                    return LoopDetectionResult(
                        loop_detected=True,
                        pattern=f"tool:{tool_name}",
                        iterations=count,
                    )

        state_window = self._state_transition_history[-self._loop_window:] if self._state_transition_history else []
        if len(state_window) >= 3:
            from collections import Counter
            state_counts = Counter(state_window)
            for pattern, count in state_counts.items():
                if count >= 3:
                    return LoopDetectionResult(
                        loop_detected=True,
                        pattern=f"state:{pattern}",
                        iterations=count,
                    )

        return LoopDetectionResult(loop_detected=False)

    def detect_timeout(self) -> TimeoutDetectionResult:
        """Check if current execution has exceeded time limit."""
        if self._execution_start is None:
            return TimeoutDetectionResult(timed_out=False)

        elapsed = (datetime.now(timezone.utc) - self._execution_start).total_seconds() * 1000
        return TimeoutDetectionResult(
            timed_out=elapsed > self._timeout_ms,
            elapsed_ms=int(elapsed),
            limit_ms=self._timeout_ms,
        )

    def select_fallback(self, reasoning_mode: ReasoningMode) -> FallbackMode:
        """Select fallback strategy based on reasoning mode and current issues.

        ROUTINE: retry→fallback
        ADAPTIVE: downgrade to ROUTINE
        EXPLORATORY: notify human
        """
        loop = self.detect_loop()
        timeout = self.detect_timeout()

        if loop.loop_detected or timeout.timed_out:
            if reasoning_mode == ReasoningMode.ROUTINE:
                return FallbackMode.COGNITIVE_FALLBACK
            elif reasoning_mode == ReasoningMode.ADAPTIVE:
                return FallbackMode.COGNITIVE_FALLBACK
            else:  # EXPLORATORY
                return FallbackMode.HUMAN_INTERVENTION

        return FallbackMode.NONE

    def check_iterations(self, current: int) -> bool:
        """Check if iteration count exceeds maximum."""
        return current >= self._max_iterations
