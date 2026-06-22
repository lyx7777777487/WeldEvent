from dataclasses import dataclass
from datetime import timedelta
from enum import Enum


class GateAction(str, Enum):
    CONTINUE = "CONTINUE"
    REDIRECT = "REDIRECT"
    TERMINATE = "TERMINATE"


@dataclass(frozen=True)
class HumanGateDefinition:
    timeout: timedelta | None = None
    redirect_target: str | None = None
