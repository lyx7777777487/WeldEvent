"""L1 Cognitive Plane — Shared enums.

All enums are str-based for JSON serialisation and Pydantic compatibility.
Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.advice.md, 2.11, 3.3).
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Section 2.advice.md — Core enums (25 enums)
# ---------------------------------------------------------------------------


class BrainStateType(str, Enum):
    IDLE = "IDLE"
    OBSERVING = "OBSERVING"
    UNDERSTANDING = "UNDERSTANDING"
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"
    MEMORY_RETRIEVAL = "MEMORY_RETRIEVAL"
    MEMORY_MATCHING = "MEMORY_MATCHING"  # v5: Routine Mode state per accepted ARC-001
    REASONING = "REASONING"
    DECISION_GENERATION = "DECISION_GENERATION"
    VALIDATION = "VALIDATION"
    PUBLICATION = "PUBLICATION"
    WAITING_FEEDBACK = "WAITING_FEEDBACK"
    ERROR = "ERROR"
    ABORT = "ABORT"


class ReasoningMode(str, Enum):
    ROUTINE = "ROUTINE"
    ADAPTIVE = "ADAPTIVE"
    EXPLORATORY = "EXPLORATORY"


class PersonaType(str, Enum):
    PLANNER = "PLANNER"
    COPILOT = "COPILOT"
    CAA = "CAA"


class DecisionPointType(str, Enum):
    DP0 = "DP0"
    DP1 = "DP1"
    DP2 = "DP2"
    DP3 = "DP3"
    DP4 = "DP4"


class NoveltyLevel(str, Enum):
    KNOWN = "KNOWN"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class EventType(str, Enum):
    WORKFLOW_ENTERED = "WORKFLOW_ENTERED"
    IQA_COMPLETED = "IQA_COMPLETED"
    PPA_COMPLETED = "PPA_COMPLETED"
    MEA_COMPLETED = "MEA_COMPLETED"
    RDA_VDA_COMPLETED = "RDA_VDA_COMPLETED"
    HUMAN_FEEDBACK_RECEIVED = "HUMAN_FEEDBACK_RECEIVED"
    MEMORY_PROMOTION_REQUESTED = "MEMORY_PROMOTION_REQUESTED"
    VALIDATION_CRITICAL = "VALIDATION_CRITICAL"


class SafetyStatus(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class RuleStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECT = "REJECT"


class ShadowStatus(str, Enum):
    CONCUR = "CONCUR"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class ConsistencyStatus(str, Enum):
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"


class AggregatedValidationResult(str, Enum):
    APPROVED = "APPROVED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class ValidationStageStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    TERMINATED = "TERMINATED"


class PromotionStatus(str, Enum):
    RAW = "RAW"
    UNDER_REVIEW = "UNDER_REVIEW"
    VALIDATED = "VALIDATED"
    PROMOTION_PENDING = "PROMOTION_PENDING"
    PROMOTED = "PROMOTED"
    ARCHIVED = "ARCHIVED"


class MemoryType(str, Enum):
    APPROVED_DECISION = "APPROVED_DECISION"
    OPERATOR_FEEDBACK = "OPERATOR_FEEDBACK"
    VALIDATED_INVESTIGATION = "VALIDATED_INVESTIGATION"
    REASONING_TRACE = "REASONING_TRACE"
    EXPERIENCE = "EXPERIENCE"
    STANDARD_PARAMETER = "STANDARD_PARAMETER"
    DEFECT_PATTERN = "DEFECT_PATTERN"
    CHECKPOINT = "CHECKPOINT"
    # Op 8.1: 三层记忆类型 (episodic/semantic/procedural)
    # Source: Generative Agents (Park et al., 2023) + Letta/MemGPT memory block
    EPISODIC = "EPISODIC"           # 具体经验: WorkflowExecutionRecord (L2_CASE + L3_EXPERIENCE)
    SEMANTIC = "SEMANTIC"           # 泛化知识: 从多次 Episodic 提取的规则 (L4_KNOWLEDGE)
    PROCEDURAL = "PROCEDURAL"       # 学会的流程: 用户标记"以后都用这个"的 WorkflowSpec 模板 (L4_KNOWLEDGE)


class KnowledgeType(str, Enum):
    STANDARD = "STANDARD"
    REGULATION = "REGULATION"
    MANUAL = "MANUAL"
    PROCESS_SPEC = "PROCESS_SPEC"
    CASE_LIBRARY = "CASE_LIBRARY"


class CollaborationLayer(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class ReviewType(str, Enum):
    APPROVAL = "APPROVAL"
    EXPLANATION = "EXPLANATION"
    FEEDBACK = "FEEDBACK"
    ESCALATION = "ESCALATION"


class FallbackMode(str, Enum):
    NONE = "NONE"
    COGNITIVE_FALLBACK = "COGNITIVE_FALLBACK"
    HUMAN_INTERVENTION = "HUMAN_INTERVENTION"


class AudienceType(str, Enum):
    OPERATOR = "OPERATOR"
    ENGINEER = "ENGINEER"
    AUDITOR = "AUDITOR"


class WorkflowType(str, Enum):
    FULL = "FULL"
    ABBREVIATED = "ABBREVIATED"
    ENHANCED = "ENHANCED"


class InspectionStrategyType(str, Enum):
    STANDARD = "STANDARD"
    INTENSIVE = "INTENSIVE"
    SAMPLING = "SAMPLING"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ConsensusStatus(str, Enum):
    ACHIEVED = "ACHIEVED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class UrgencyLevel(str, Enum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


class RoutingDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REWORK = "REWORK"
    ESCALATE = "ESCALATE"


class EffortLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CAAInvocationTrigger(str, Enum):
    VALIDATION_REVIEW = "VALIDATION_REVIEW"
    CONFIDENCE_BELOW_THRESHOLD = "CONFIDENCE_BELOW_THRESHOLD"
    ESCALATION = "ESCALATION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXPLANATION_REQUESTED = "EXPLANATION_REQUESTED"
    UNKNOWN_DEFECT = "UNKNOWN_DEFECT"
    FALLBACK_ACTIVATED = "FALLBACK_ACTIVATED"


# ---------------------------------------------------------------------------
# Section 2.11 — Learning enums
# ---------------------------------------------------------------------------


class LearningEventType(str, Enum):
    EXPERIENCE = "EXPERIENCE"
    FEEDBACK = "FEEDBACK"
    PROMOTION_CANDIDATE = "PROMOTION_CANDIDATE"
    KNOWLEDGE_SUGGESTION = "KNOWLEDGE_SUGGESTION"


class ProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Section 3.3 — Brain-internal event types
# ---------------------------------------------------------------------------


class BrainInternalEventType(str, Enum):
    DECISION_INITIATED = "DECISION_INITIATED"
    REASONING_MODE_SELECTED = "REASONING_MODE_SELECTED"
    PERSONA_ACTIVATED = "PERSONA_ACTIVATED"
    REASONING_COMPLETED = "REASONING_COMPLETED"
    VALIDATION_REQUESTED = "VALIDATION_REQUESTED"
    DECISION_PUBLISHED = "DECISION_PUBLISHED"
    DECISION_REJECTED = "DECISION_REJECTED"
    DECISION_ESCALATED = "DECISION_ESCALATED"
    BRAIN_STATE_CHANGED = "BRAIN_STATE_CHANGED"
    MEMORY_MATCH_COMPLETED = "MEMORY_MATCH_COMPLETED"
    COGNITIVE_FALLBACK_ACTIVATED = "COGNITIVE_FALLBACK_ACTIVATED"
    HUMAN_INTERVENTION_ACTIVATED = "HUMAN_INTERVENTION_ACTIVATED"
    ESCALATION_COUNTER_RESET = "ESCALATION_COUNTER_RESET"


# ---------------------------------------------------------------------------
# Brain state-machine triggers
# ---------------------------------------------------------------------------


class BrainTrigger(str, Enum):
    EVENT_DEQUEUED = "event_dequeued"
    CONTEXT_LOADED_ROUTINE = "context_loaded_ROUTINE"
    CONTEXT_LOADED_ADAPTIVE = "context_loaded_ADAPTIVE"
    CONTEXT_LOADED_EXPLORATORY = "context_loaded_EXPLORATORY"
    CONTEXT_UNDERSTOOD = "context_understood"
    KNOWLEDGE_RECEIVED = "knowledge_received"
    MEMORY_RECEIVED_ROUTINE = "memory_received_ROUTINE"
    MEMORY_RECEIVED_ADAPTIVE = "memory_received_ADAPTIVE"
    MEMORY_RECEIVED_EXPLORATORY = "memory_received_EXPLORATORY"
    MATCH_PRODUCED = "match_produced"
    REASONING_COMPLETED = "reasoning_completed"
    DECISION_GENERATED = "decision_generated"
    APPROVED = "approved"
    REQUIRES_REVIEW = "requires_review"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    PUBLISHED_ROUTINE_APPROVED = "published_ROUTINE_APPROVED"
    PUBLISHED_ADAPTIVE_APPROVED = "published_ADAPTIVE_APPROVED"
    PUBLISHED_EXPLORATORY = "published_EXPLORATORY"
    PUBLISHED_REQUIRES_REVIEW = "published_REQUIRES_REVIEW"
    ESCALATION_PUBLISHED = "escalation_published"
    FEEDBACK_RECEIVED = "feedback_received"
    FEEDBACK_TIMEOUT = "feedback_timeout"
    UNRECOVERABLE_ERROR = "unrecoverable_error"
    RECOVERABLE_ERROR = "recoverable_error"
    ERROR_RECOVERED = "error_recovered"
    ABORT = "abort"


# ---------------------------------------------------------------------------
# Interaction Layer enums (Source: L1-Interaction-Layer-Business-Requirements.md §2–§7)
# ---------------------------------------------------------------------------


class MatchStrategy(str, Enum):
    KEYWORD = "keyword"
    LLM_LABEL = "llm_label"
    COMPOSITE = "composite"


class InstructionType(str, Enum):
    UPGRADE_STRATEGY = "upgrade_strategy"
    DOWNGRADE_STRATEGY = "downgrade_strategy"
    SET_STRATEGY = "set_strategy"
    ADJUST_PARAMETER = "adjust_parameter"
    OVERRIDE_THRESHOLD = "override_threshold"
    SKIP_IMAGE = "skip_image"
    MARK_FOR_REVIEW = "mark_for_review"
    INSERT_EXTRA_STEP = "insert_extra_step"
    ANNOTATE = "annotate"
    REQUEST_EXPLANATION = "request_explanation"


class InstructionStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    EXPIRED = "expired"
    APPLIED = "applied"


class ResponseType(str, Enum):
    TEXT_REPLY = "text_reply"
    CLARIFICATION_REQUEST = "clarification_request"
    SESSION_CLOSED = "session_closed"
    STRUCTURED_OUTPUT = "structured_output"
    ERROR = "error"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class DesignPhase(str, Enum):
    COLLECTING = "collecting"
    REFINING = "refining"
    CONFIRMED = "confirmed"
    PUBLISHED = "published"


class InterventionGranularity(str, Enum):
    CASE = "case"
    IMAGE = "image"
    STEP = "step"
    PARAMETER = "parameter"


class AnnotationType(str, Enum):
    BBOX = "bbox"
    POLYGON = "polygon"
    LINE = "line"
    POINT = "point"
    HEATMAP = "heatmap"
    MASK = "mask"


class ResultLevel(str, Enum):
    ROUTINE = "routine"
    MARGINAL = "marginal"
    ANOMALY = "anomaly"


class SenderType(str, Enum):
    SYSTEM = "system"
    USER = "user"


class ChatMessageType(str, Enum):
    TEXT = "text"
    INSPECTION_RESULT = "inspection_result"
    IMAGE_ANNOTATED = "image_annotated"
    LOG_ENTRY = "log_entry"
    PROGRESS_UPDATE = "progress_update"
    STRATEGY_CHANGE = "strategy_change"
    ALERT = "alert"
    KNOWLEDGE_ANSWER = "knowledge_answer"
    CLARIFICATION = "clarification"
    CONFIRMATION_REQUEST = "confirmation_request"
    USER_TEXT = "user_text"
    USER_SELECTION = "user_selection"
    ERROR = "error"


class UserActionType(str, Enum):
    ZOOM_IN = "zoom_in"
    EXPLAIN = "explain"
    INTENSIFY = "intensify"
    RECHECK = "recheck"
    ESCALATE = "escalate"
    MARK_REVIEW = "mark_review"
    OVERRIDE = "override"
    CONFIRM = "confirm"
    CANCEL = "cancel"
