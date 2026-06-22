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

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# 直接从controlplane导入接口定义，保持一致性
from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus


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
    
    async def on_error(self, error: Exception) -> ActivityOutput:
        """错误处理钩子 — 返回ERROR状态的ActivityOutput。"""
        return ActivityOutput(
            status=ActivityStatus.ERROR,
            error=f"{self.activity_name}: {type(error).__name__}: {error}",
        )
    
    # ------------------------------------------------------------------
    # 统一执行入口（框架调用此方法，不要覆写）
    # ------------------------------------------------------------------
    
    async def run(self, input: ActivityInput) -> ActivityOutput:
        """统一的执行入口 — 包含生命周期管理和错误处理。"""
        try:
            await self.on_start(input)
            result = await self.execute(input)
            return await self.on_complete(result)
        except Exception as e:
            return await self.on_error(e)
    
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
# 导出接口（保持与controlplane一致）
# ---------------------------------------------------------------------------

__all__ = [
    "BaseActivity",
    "ActivityInput",
    "ActivityOutput",
    "ActivityStatus",
    "ActivityMetadata",
]