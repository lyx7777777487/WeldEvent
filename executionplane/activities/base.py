"""Activity基类 — Execution Plane执行单元抽象。

Source: WeldEvent架构方案 §5.2 (Agent接口契约)

设计原则:
  - Activity是被动的执行单元，由L2 Temporal调度
  - Activity不具备自主性，只响应调度执行
  - Activity通过WeldMap读写共享状态
  - Activity返回ActivityOutput给调度器
  - Activity接口与Control Plane保持一致

命名约定:
  - Agent（L1 Brain）→ 自主决策者
  - Activity（L3 Execution）→ 执行单元

接口一致性:
  - ActivityInput/ActivityOutput/ActivityStatus 与 controlplane/domain/activity.py 保持一致
  - workflow_id 从 workflow_context.workflow_id 获取
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# L3 独立副本（端口/适配器隔离）— 不再从 controlplane import
from .contracts import ActivityInput, ActivityOutput, ActivityStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Activity元数据（executionplane扩展）
# ---------------------------------------------------------------------------

@dataclass
class ActivityMetadata:
    """Activity元数据 — 描述Activity的能力和特性。"""
    activity_id: str                       # 如 "iqa_activity", "ppa_activity"
    name: str                              # 显示名: "图像质量评估"
    version: str = "v1"
    description: str = ""
    capabilities: list[str] = field(default_factory=list)  # ['cv', 'rule', 'mllm']
    execution_target: str = "cpu"          # cpu / gpu / edge
    estimated_latency_ms: int = 100        # 预估执行耗时


# ---------------------------------------------------------------------------
# Activity基类
# ---------------------------------------------------------------------------

class BaseActivity(ABC):
    """Activity基类 — 所有Execution Plane执行单元的抽象。
    
    职责:
      1. 从WeldMap读取上游数据
      2. 使用能力组件执行业务逻辑
      3. 将结果写回WeldMap
      4. 返回ActivityOutput给Control Plane
    
    子类只需实现:
      - activity_name: Activity名称
      - metadata: Activity元数据
      - execute(): 执行逻辑
    
    Usage::
        class MyActivity(BaseActivity):
            @property
            def activity_name(self) -> str:
                return "my_activity"
            
            @property
            def metadata(self) -> ActivityMetadata:
                return ActivityMetadata(
                    activity_id="my_activity",
                    name="我的Activity",
                    capabilities=["cv", "rule"],
                )
            
            async def execute(self, input: ActivityInput) -> ActivityOutput:
                # 从workflow_context获取workflow_id
                workflow_id = input.workflow_context.get("workflow_id")
                
                # 执行逻辑
                ...
                return ActivityOutput(status=ActivityStatus.OK, data={"result": ...})
    """
    
    @property
    @abstractmethod
    def activity_name(self) -> str:
        """Activity名称 — 对应Control Plane ActivityBinding.activity_name。"""
        ...
    
    @property
    @abstractmethod
    def metadata(self) -> ActivityMetadata:
        """Activity元数据。"""
        ...
    
    @abstractmethod
    async def execute(self, input: ActivityInput) -> ActivityOutput:
        """执行Activity逻辑 — 核心实现点。
        
        典型流程:
            1. 从workflow_context获取workflow_id
            2. 解析input.params获取所需参数
            3. 从weldmap读取上游数据
            4. 调用能力组件执行业务逻辑
            5. 将结果写入weldmap
            6. 返回ActivityOutput
        
        Args:
            input: Control Plane下发的执行请求
        
        Returns:
            ActivityOutput: 执行结果
        """
        ...
    
    # ------------------------------------------------------------------
    # 可选方法
    # ------------------------------------------------------------------
    
    async def on_start(self, input: ActivityInput) -> None:
        """执行前钩子 — 可用于日志、指标上报等。"""
        pass
    
    async def on_complete(self, output: ActivityOutput) -> ActivityOutput:
        """执行后钩子 — 可对输出做后处理。"""
        return output
    
    # 注: 原 on_error 钩子已移除。
    # 设计决策: 异常由 Temporal RetryPolicy 处理，activity 不再捕获异常。
    #   - 瞬时故障（网络/服务不可用）: 异常透传到 Temporal，由 RetryPolicy 自动重试
    #   - 业务错误（参数无效/数据不存在）: execute() 应返回 ActivityOutput(ERROR)
    # 这样使 Temporal 的重试机制能正确区分瞬时故障与业务错误。
    
    # ------------------------------------------------------------------
    # 统一执行入口（框架调用此方法，不要覆写）
    # ------------------------------------------------------------------
    
    async def run(self, input: ActivityInput) -> ActivityOutput:
        """统一执行入口 — 生命周期管理 + 异常透传。
        
        异常处理策略（与 Temporal RetryPolicy 配合）:
          - 瞬时故障（网络/服务不可用）: 异常透传 → Temporal 自动重试
          - 业务错误（参数无效/数据不存在）: execute() 返回 ActivityOutput(ERROR)
          - 生命周期钩子异常: log warning 不中断主流程
        """
        await self._safe_on_start(input)
        result = await self.execute(input)
        return await self._safe_on_complete(result)
    
    async def _safe_on_start(self, input: ActivityInput) -> None:
        """安全执行 on_start 钩子 — 异常仅 log warning 不中断主流程。"""
        try:
            await self.on_start(input)
        except Exception as e:
            logger.warning("on_start hook failed for %s: %s: %s",
                           self.activity_name, type(e).__name__, e)
    
    async def _safe_on_complete(self, result: ActivityOutput) -> ActivityOutput:
        """安全执行 on_complete 钩子 — 异常仅 log warning，返回 execute 的原始结果。"""
        try:
            return await self.on_complete(result)
        except Exception as e:
            logger.warning("on_complete hook failed for %s: %s: %s",
                           self.activity_name, type(e).__name__, e)
            return result
    
    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    
    async def health_check(self) -> dict[str, Any]:
        """健康检查 — 返回Activity健康状态。"""
        return {
            "activity": self.activity_name,
            "status": "healthy",
            "version": self.metadata.version,
            "capabilities": self.metadata.capabilities,
            "execution_target": self.metadata.execution_target,
        }


# ---------------------------------------------------------------------------
# 导出接口（从 .contracts re-export，保持调用方向后兼容）
# ---------------------------------------------------------------------------

__all__ = [
    "BaseActivity",
    "ActivityInput",
    "ActivityOutput",
    "ActivityStatus",
    "ActivityMetadata",
]
