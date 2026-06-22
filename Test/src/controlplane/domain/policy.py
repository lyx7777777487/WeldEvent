from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_interval: timedelta = timedelta(seconds=1)
    backoff_coefficient: float = 2.0
    maximum_interval: timedelta = timedelta(seconds=100)
    non_retryable_error_types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TimeoutPolicy:
    schedule_to_close: timedelta | None = None
    schedule_to_start: timedelta | None = None
    start_to_close: timedelta | None = None
    heartbeat: timedelta | None = None


@dataclass(frozen=True)
class ExecutionPolicy:
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
