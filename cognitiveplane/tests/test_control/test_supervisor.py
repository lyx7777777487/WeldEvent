"""Tests for control/supervisor.py — SupervisorCenter."""

from datetime import datetime, timedelta, timezone

import pytest

from cognitiveplane.control.supervisor import (
    HealthCheckResult,
    HealthStatus,
    LoopDetectionResult,
    SupervisorCenter,
    TimeoutDetectionResult,
)
from cognitiveplane.shared.enums import FallbackMode, ReasoningMode


class TestHealthCheck:
    def test_healthy_when_all_available(self):
        sc = SupervisorCenter()
        result = sc.health_check({"llm": True, "memory": True, "gateway": True})
        assert result.status == HealthStatus.HEALTHY
        assert result.checks["llm"] is True

    def test_degraded_when_some_unavailable(self):
        sc = SupervisorCenter()
        result = sc.health_check({"llm": True, "memory": False})
        assert result.status == HealthStatus.DEGRADED

    def test_unhealthy_when_all_unavailable(self):
        sc = SupervisorCenter()
        result = sc.health_check({"llm": False, "memory": False})
        assert result.status == HealthStatus.UNHEALTHY

    def test_no_subsystems_returns_healthy(self):
        sc = SupervisorCenter()
        result = sc.health_check()
        assert result.status == HealthStatus.HEALTHY


class TestLoopDetection:
    def test_no_loop_initially(self):
        sc = SupervisorCenter()
        result = sc.detect_loop()
        assert result.loop_detected is False

    def test_detect_tool_call_loop(self):
        sc = SupervisorCenter()
        for _ in range(4):
            sc.record_tool_call("search_standards")
        result = sc.detect_loop()
        assert result.loop_detected is True
        assert "search_standards" in (result.pattern or "")

    def test_no_loop_with_varied_calls(self):
        sc = SupervisorCenter()
        sc.record_tool_call("search_standards")
        sc.record_tool_call("search_cases")
        sc.record_tool_call("search_process")
        result = sc.detect_loop()
        assert result.loop_detected is False

    def test_detect_state_loop(self):
        sc = SupervisorCenter()
        for _ in range(4):
            sc.record_state_transition("VALIDATION", "PUBLICATION")
        result = sc.detect_loop()
        assert result.loop_detected is True
        assert "state:" in (result.pattern or "")


class TestTimeoutDetection:
    def test_no_timeout_when_within_limit(self):
        sc = SupervisorCenter(timeout_ms=30000)
        sc.start_execution()
        result = sc.detect_timeout()
        assert result.timed_out is False

    def test_timeout_when_exceeded(self):
        sc = SupervisorCenter(timeout_ms=1)
        sc.start_execution()
        # Manually set start time to past
        sc._execution_start = datetime.now(timezone.utc) - timedelta(seconds=1)
        result = sc.detect_timeout()
        assert result.timed_out is True
        assert result.elapsed_ms > 0

    def test_no_execution_start(self):
        sc = SupervisorCenter()
        result = sc.detect_timeout()
        assert result.timed_out is False


class TestFallback:
    def test_no_fallback_when_healthy(self):
        sc = SupervisorCenter()
        result = sc.select_fallback(ReasoningMode.ROUTINE)
        assert result == FallbackMode.NONE

    def test_routine_fallback_to_cognitive(self):
        sc = SupervisorCenter()
        sc.start_execution()
        for _ in range(4):
            sc.record_tool_call("search_standards")
        result = sc.select_fallback(ReasoningMode.ROUTINE)
        assert result == FallbackMode.COGNITIVE_FALLBACK

    def test_adaptive_fallback_to_cognitive(self):
        sc = SupervisorCenter()
        sc.start_execution()
        for _ in range(4):
            sc.record_tool_call("search_standards")
        result = sc.select_fallback(ReasoningMode.ADAPTIVE)
        assert result == FallbackMode.COGNITIVE_FALLBACK

    def test_exploratory_fallback_to_human(self):
        sc = SupervisorCenter()
        sc.start_execution()
        for _ in range(4):
            sc.record_tool_call("search_standards")
        result = sc.select_fallback(ReasoningMode.EXPLORATORY)
        assert result == FallbackMode.HUMAN_INTERVENTION


class TestIterationCheck:
    def test_within_limit(self):
        sc = SupervisorCenter(max_iterations=10)
        assert sc.check_iterations(5) is False

    def test_at_limit(self):
        sc = SupervisorCenter(max_iterations=10)
        assert sc.check_iterations(10) is True

    def test_over_limit(self):
        sc = SupervisorCenter(max_iterations=10)
        assert sc.check_iterations(15) is True


class TestExceptionClasses:
    def test_decision_timeout_error(self):
        from cognitiveplane.control.exceptions import DecisionTimeoutError
        err = DecisionTimeoutError("case-1", "ADAPTIVE")
        assert err.case_id == "case-1"
        assert err.reasoning_mode == "ADAPTIVE"

    def test_reasoning_loop_error(self):
        from cognitiveplane.control.exceptions import ReasoningLoopError
        err = ReasoningLoopError("case-1", 15)
        assert err.case_id == "case-1"
        assert err.iterations == 15

    def test_validation_block_error(self):
        from cognitiveplane.control.exceptions import ValidationBlockError
        err = ValidationBlockError("case-1", "Safety BLOCK")
        assert err.case_id == "case-1"
        assert err.reason == "Safety BLOCK"

    def test_escalation_overflow_error(self):
        from cognitiveplane.control.exceptions import EscalationOverflowError
        err = EscalationOverflowError("case-1", 5)
        assert err.consecutive_critical == 5

    def test_weldmap_unavailable_error(self):
        from cognitiveplane.control.exceptions import WeldMapUnavailableError
        err = WeldMapUnavailableError("publish_decision", retries=3)
        assert err.operation == "publish_decision"
        assert err.retries == 3

    def test_llm_unavailable_error(self):
        from cognitiveplane.control.exceptions import LLMUnavailableError
        err = LLMUnavailableError("deepseek")
        assert err.provider == "deepseek"
