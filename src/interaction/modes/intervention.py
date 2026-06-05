"""Mode D: Intervention — non-blocking production line instruction injection.

Source: L1-Interaction-Layer-Business-Requirements.md §3.5.

Core principle: 产线永不停止 (production line never stops).
All interventions are async instruction injection, never pausing the line.
"""

import re
from datetime import datetime, timezone
from uuid import uuid4

from src.interaction.base import (
    ContextRequirement,
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserMessage,
)
from src.interaction.dependencies import ModeDependencies
from src.interaction.signals.instruction import LiveInstruction
from src.shared.enums import (
    InstructionStatus,
    InstructionType,
    InterventionGranularity,
    MatchStrategy,
)
from src.shared.ports.event_bus import InstructionPublishInput
from src.shared.types import InstructionId, SessionId


class InterventionMode(ModeProtocol):
    """Stateful intervention mode — injects non-blocking instructions.

    Parses intervention intent from user input, constructs LiveInstruction,
    publishes via InstructionPublishPort, and returns confirmation.
    """

    mode_id = "cognitive.intervention"
    display_name = "Intervention"
    description = "Inject non-blocking instructions into running production line (upgrade/downgrade strategy, skip images, adjust parameters)"

    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["加强", "加强检测", "从严", "升级", "upgrade", "intensify"],
            priority=70,
            confidence_threshold=0.3,
        ),
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["恢复", "常规", "正常", "降级", "downgrade", "standard"],
            priority=65,
            confidence_threshold=0.3,
        ),
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["跳过", "skip", "略过", "下一张"],
            priority=60,
            confidence_threshold=0.3,
        ),
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["调整", "修改", "改变", "参数", "阈值", "adjust"],
            priority=55,
            confidence_threshold=0.25,
        ),
        IntentPattern(
            match_strategy=MatchStrategy.COMPOSITE,
            patterns=["从图", "从第", "起", "开始", "后面"],
            required_context=ContextRequirement(needs_active_case=True),
            priority=75,
            confidence_threshold=0.3,
        ),
    ]

    required_ports = ["InstructionPublishPort"]
    creates_session = True
    session_data_schema = None  # Will be InterventionSessionData

    async def handle(
        self, message: UserMessage, deps: ModeDependencies
    ) -> ModeResponse:
        """Parse intervention intent, publish instruction, return confirmation."""
        # 1. Detect instruction type
        instruction_type = self._detect_instruction_type(message.raw_text)

        # 2. Detect effective from image
        effective_from_image = self._detect_image_index(message.raw_text)

        # 3. Build payload
        payload = {"raw_input": message.raw_text}

        # 4. Create LiveInstruction
        instruction = LiveInstruction(
            instruction_id=InstructionId(value=uuid4()),
            instruction_type=instruction_type,
            status=InstructionStatus.ACTIVE,
            target_case_id=message.case_id,
            target_image_index=effective_from_image,
            granularity=InterventionGranularity.IMAGE,
            payload=payload,
            reason=message.raw_text,
            issued_by=message.operator_id,
        )

        # 5. Publish instruction (non-blocking)
        instruction_port = deps.get("InstructionPublishPort")
        pub_result = await instruction_port.publish_instruction(
            InstructionPublishInput(
                instruction_type=instruction.instruction_type.value,
                target_case_id=str(instruction.target_case_id.value) if instruction.target_case_id else None,
                target_image_index=instruction.target_image_index,
                payload=instruction.payload,
                reason=instruction.reason,
                issued_by=instruction.issued_by,
            )
        )

        # 6. Format confirmation
        effective_text = f"从第{effective_from_image}张图起" if effective_from_image is not None else "当前图片起"
        reply = (
            f"✓ 干预指令已发布 (产线未停止)\n\n"
            f"指令详情:\n"
            f"  类型: {instruction_type.value}\n"
            f"  生效: {effective_text}\n"
            f"  原因: {message.raw_text}\n\n"
            f"指令ID: {pub_result.instruction_id}"
        )

        return ModeResponse(
            mode_id=self.mode_id,
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply=reply,
            ports_accessed=["InstructionPublishPort"],
        )

    @staticmethod
    def _detect_instruction_type(text: str) -> InstructionType:
        """Detect instruction type from user input."""
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["加强", "从严", "升级", "upgrade", "intensify"]):
            return InstructionType.UPGRADE_STRATEGY
        if any(kw in text_lower for kw in ["恢复", "常规", "正常", "降级", "downgrade"]):
            return InstructionType.DOWNGRADE_STRATEGY
        if any(kw in text_lower for kw in ["跳过", "略过", "skip"]):
            return InstructionType.SKIP_IMAGE
        if any(kw in text_lower for kw in ["调整", "修改", "改变", "参数", "阈值", "adjust"]):
            return InstructionType.ADJUST_PARAMETER
        return InstructionType.SET_STRATEGY

    @staticmethod
    def _detect_image_index(text: str) -> int | None:
        """Detect 'from image N' pattern from user input."""
        # Match "从图4", "从图4起", "从第4张"
        m = re.search(r"从图(\d+)", text)
        if m:
            return int(m.group(1))
        m = re.search(r"从第(\d+)张", text)
        if m:
            return int(m.group(1))
        m = re.search(r"图(\d+)", text)
        if m:
            return int(m.group(1))
        return None
