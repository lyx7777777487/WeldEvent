"""WeldMap 客户端抽象 — Agent 读写黑板的统一接口。

Source: Complete_architecture_V1.docx §2.3-2.5

设计原则:
  - Agent 不直接通信，全部通过 WeldMap 共享状态
  - State Watcher 订阅机制：Agent B 订阅路径，Agent A 写入后自动通知
  - 乐观锁 CAS 写入：带版本号，防止并发冲突
  - Event Sourcing：每次写入记录不可变事件
"""

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass

from .models import (
    AnnotationsData,
    DecisionData,
    ImageQualityReport,
    MaskData,
    ValidationData,
    WeldMapEvent,
    WeldMapPath,
    WeldMapSnapshot,
    WorkflowId,
)


@dataclass(frozen=True)
class WriteResult:
    """写入结果。"""
    success: bool
    path: str
    version: int                      # 写入后的新版本号
    conflict: bool = False            # 是否发生版本冲突


@dataclass(frozen=True)
class ReadResult:
    """读取结果。"""
    found: bool
    value: Any | None = None
    path: str = ""
    version: int = 0


class WeldMapClient(ABC):
    """WeldMap 读写客户端 — 所有 Agent 通过此接口访问黑板。
    
    实现可以是 InMemory（开发/测试）、Redis（生产）、或 MinIO+DB 组合。
    Agent 层不关心底层存储，只依赖此接口。
    """

    @abstractmethod
    async def initialize(self, workflow_id: WorkflowId) -> WeldMapSnapshot:
        """初始化一个新的 WeldMap 空间。"""
        ...

    @abstractmethod
    async def read_snapshot(self, workflow_id: WorkflowId) -> WeldMapSnapshot | None:
        """读取完整 WeldMap 快照。"""
        ...

    @abstractmethod
    async def read_path(
        self, workflow_id: WorkflowId, path: WeldMapPath
    ) -> ReadResult:
        """读取单一路径的值。

        Args:
            workflow_id: 工作流 ID
            path: 如 'image/quality', 'mask/confidence', 'annotations/L1'

        Returns:
            ReadResult with value and current version.
        """
        ...

    @abstractmethod
    async def write_path(
        self,
        workflow_id: WorkflowId,
        path: WeldMapPath,
        value: Any,
        expected_version: int | None = None,
        source: str = "",
    ) -> WriteResult:
        """写入单一路径（CAS 乐观锁）。

        Args:
            workflow_id: 工作流 ID
            path: 目标路径
            value: 要写入的值
            expected_version: 期望的当前版本（None 则不检查）
            source: 写入者标识（Agent 名称）

        Returns:
            WriteResult. 若 expected_version 不匹配则 conflict=True。
        """
        ...

    # ------------------------------------------------------------------
    # 便捷方法 — IQA
    # ------------------------------------------------------------------

    async def write_image_quality(
        self, workflow_id: WorkflowId, report: ImageQualityReport, source: str = "iqa_agent"
    ) -> WriteResult:
        """写入 IQA 质量报告。"""
        return await self.write_path(
            workflow_id, WeldMapPath("image/quality"), report, source=source
        )

    async def read_image_quality(
        self, workflow_id: WorkflowId
    ) -> ImageQualityReport | None:
        """读取 IQA 质量报告。"""
        result = await self.read_path(workflow_id, WeldMapPath("image/quality"))
        if isinstance(result.value, ImageQualityReport):
            return result.value
        return None

    async def read_image_preprocess(
        self, workflow_id: WorkflowId
    ) -> dict[str, Any] | None:
        """读取 PPA 预处理结果（预处理后图片路径 + 应用策略）。

        PPA 写入 image/preprocess 路径，供后续节点（HCA/Annotation 等）
        获取 PPA 预处理输出。当前消费者待 Phase 5+ 实现。
        """
        result = await self.read_path(workflow_id, WeldMapPath("image/preprocess"))
        if result.value is None:
            return None
        return result.value if isinstance(result.value, dict) else None

    # ------------------------------------------------------------------
    # 便捷方法 — PPA (Mask)
    # ------------------------------------------------------------------

    async def write_mask(self, workflow_id: WorkflowId, mask: MaskData, source: str = "ppa_agent") -> WriteResult:
        """写入分割掩码数据。"""
        return await self.write_path(
            workflow_id, WeldMapPath("mask"), mask, source=source
        )

    async def read_mask(self, workflow_id: WorkflowId) -> MaskData | None:
        """读取分割掩码数据。"""
        result = await self.read_path(workflow_id, WeldMapPath("mask"))
        if isinstance(result.value, MaskData):
            return result.value
        return None

    # ------------------------------------------------------------------
    # 便捷方法 — MEA (Annotations)
    # ------------------------------------------------------------------

    async def write_annotations(
        self, workflow_id: WorkflowId, annotations: AnnotationsData, source: str = "mea_agent"
    ) -> WriteResult:
        """写入标注集。"""
        return await self.write_path(
            workflow_id, WeldMapPath("annotations"), annotations, source=source
        )

    async def read_annotations(self, workflow_id: WorkflowId) -> AnnotationsData | None:
        """读取标注集。"""
        result = await self.read_path(workflow_id, WeldMapPath("annotations"))
        if isinstance(result.value, AnnotationsData):
            return result.value
        return None

    # ------------------------------------------------------------------
    # 便捷方法 — VDA (Validation + Decision)
    # ------------------------------------------------------------------

    async def write_validation(
        self, workflow_id: WorkflowId, validation: ValidationData, source: str = "vda_agent"
    ) -> WriteResult:
        """写入校验结果。"""
        return await self.write_path(
            workflow_id, WeldMapPath("validation"), validation, source=source
        )

    async def write_decision(
        self, workflow_id: WorkflowId, decision: DecisionData, source: str = "vda_agent"
    ) -> WriteResult:
        """写入判定结果。"""
        return await self.write_path(
            workflow_id, WeldMapPath("decision"), decision, source=source
        )

    # ------------------------------------------------------------------
    # Event Sourcing
    # ------------------------------------------------------------------

    @abstractmethod
    async def append_event(self, workflow_id: WorkflowId, event: WeldMapEvent) -> None:
        """追加一条不可变事件到历史记录。"""
        ...

    @abstractmethod
    async def read_events(
        self, workflow_id: WorkflowId, since: int | None = None
    ) -> list[WeldMapEvent]:
        """读取事件历史。since 为起始事件索引。"""
        ...
