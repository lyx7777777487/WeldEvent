"""IQA接口 — 图像质量检测入口（IQA特有）。

使用方法:
  1. 设置图片路径（单张或批量）
  2. 调用run_iqa()执行检测
  3. 查看检测结果

注意:
  - 此接口仅用于IQA Activity
  - 其他Activity的接口应在各自的子模块中定义
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from .activity import IqaActivity
from .config import QualityStandardRegistry, QualityStandard
from ...activities.base import ActivityInput, ActivityOutput, ActivityStatus
from ...capabilities.numpy_cv_checker import NumpyCVRuleChecker
from ...capabilities.mllm_provider import MllmProvider
from ...capabilities.mock_providers import MockMllmProvider
from ...weldmap.in_memory import InMemoryWeldMapClient


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------

@dataclass
class IqaResult:
    """IQA检测结果"""
    
    image_path: str
    workflow_id: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = ""
    route_decision: str = ""
    confidence: float = 0.0
    
    resolution_passed: bool = False
    resolution_detail: str = ""
    exposure_passed: bool = False
    exposure_detail: str = ""
    focus_passed: bool = False
    focus_detail: str = ""
    completeness_passed: bool = False
    completeness_detail: str = ""
    
    deep_vision_triggered: bool = False
    deep_vision_anomalies: list[str] = field(default_factory=list)
    mllm_error: str | None = None
    error: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "image_path": self.image_path,
            "workflow_id": self.workflow_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "route_decision": self.route_decision,
            "confidence": self.confidence,
            "checks": {
                "resolution": {"passed": self.resolution_passed, "detail": self.resolution_detail},
                "exposure": {"passed": self.exposure_passed, "detail": self.exposure_detail},
                "focus": {"passed": self.focus_passed, "detail": self.focus_detail},
                "completeness": {"passed": self.completeness_passed, "detail": self.completeness_detail},
            },
            "deep_vision": {
                "triggered": self.deep_vision_triggered,
                "anomalies": self.deep_vision_anomalies,
                "error": self.mllm_error,
            },
            "error": self.error,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
    
    @property
    def is_passed(self) -> bool:
        return self.status == "OK"
    
    @property
    def needs_review(self) -> bool:
        return self.status == "MARGINAL"
    
    @property
    def is_rejected(self) -> bool:
        return self.status == "NG"
    
    @property
    def has_error(self) -> bool:
        return self.status == "ERROR"


# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------

_default_weldmap: InMemoryWeldMapClient | None = None
_default_cv_checker: NumpyCVRuleChecker | None = None
_default_mllm: MllmProvider | None = None
_default_registry: QualityStandardRegistry | None = None


def _get_default_components() -> tuple[
    InMemoryWeldMapClient,
    NumpyCVRuleChecker,
    MllmProvider | None,
    QualityStandardRegistry,
]:
    """获取默认组件"""
    global _default_weldmap, _default_cv_checker, _default_mllm, _default_registry
    
    if _default_weldmap is None:
        _default_weldmap = InMemoryWeldMapClient()
    if _default_cv_checker is None:
        _default_cv_checker = NumpyCVRuleChecker()
    if _default_mllm is None:
        _default_mllm = MockMllmProvider()
    if _default_registry is None:
        _default_registry = QualityStandardRegistry()
    
    return _default_weldmap, _default_cv_checker, _default_mllm, _default_registry


def configure_mllm(mllm: MllmProvider | None) -> None:
    """配置MLLM提供者"""
    global _default_mllm
    _default_mllm = mllm


def register_standard(standard: QualityStandard) -> None:
    """注册自定义质量标准"""
    global _default_registry
    if _default_registry is None:
        _default_registry = QualityStandardRegistry()
    _default_registry.register(standard)


# ---------------------------------------------------------------------------
# 核心接口
# ---------------------------------------------------------------------------

async def run_iqa(
    image_path: str,
    standard_id: str = "macro_weld",
    workflow_id: str | None = None,
    mllm_enabled: bool = True,
) -> IqaResult:
    """执行单张图片的质量检测"""
    
    path = Path(image_path)
    if not path.exists():
        return IqaResult(
            image_path=image_path,
            workflow_id=workflow_id or f"IQA-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            status="ERROR",
            error=f"图片文件不存在: {image_path}",
        )
    
    weldmap, cv_checker, mllm, registry = _get_default_components()
    actual_mllm = mllm if mllm_enabled else None
    
    iqa = IqaActivity(
        weldmap=weldmap,
        cv_checker=cv_checker,
        mllm=actual_mllm,
        standard_registry=registry,
        default_standard_id=standard_id,
    )
    
    wf_id = workflow_id or f"IQA-{path.stem}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    output = await iqa.execute(ActivityInput(
        control_point_id="CP0",
        workflow_context={"workflow_id": wf_id},
        params={"image_path": str(path.absolute())},
    ))
    
    return _convert_output_to_result(output, image_path, wf_id)


async def run_iqa_batch(
    image_paths: list[str],
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
) -> list[IqaResult]:
    """批量执行图片质量检测"""
    
    if parallel:
        tasks = [
            run_iqa(path, standard_id, None, mllm_enabled)
            for path in image_paths
        ]
        results = await asyncio.gather(*tasks)
    else:
        results = []
        for path in image_paths:
            result = await run_iqa(path, standard_id, None, mllm_enabled)
            results.append(result)
    
    return results


async def run_iqa_folder(
    folder_path: str,
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
    extensions: list[str] | None = None,
    recursive: bool = False,
) -> list[IqaResult]:
    """检测文件夹中的所有图片"""
    
    default_extensions = ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.PNG', '.JPG', '.JPEG']
    exts = extensions or default_extensions
    
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return []
    
    image_paths = []
    if recursive:
        for ext in exts:
            image_paths.extend(folder.rglob(f'*{ext}'))
    else:
        for ext in exts:
            image_paths.extend(folder.glob(f'*{ext}'))
    
    image_paths = sorted([str(p) for p in image_paths])
    
    if not image_paths:
        return []
    
    return await run_iqa_batch(image_paths, standard_id, mllm_enabled, parallel)


# ---------------------------------------------------------------------------
# 同步接口
# ---------------------------------------------------------------------------

def run_iqa_sync(
    image_path: str,
    standard_id: str = "macro_weld",
    workflow_id: str | None = None,
    mllm_enabled: bool = True,
) -> IqaResult:
    """同步版本的IQA检测"""
    return asyncio.run(run_iqa(image_path, standard_id, workflow_id, mllm_enabled))


def run_iqa_batch_sync(
    image_paths: list[str],
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
) -> list[IqaResult]:
    """同步版本的批量检测"""
    return asyncio.run(run_iqa_batch(image_paths, standard_id, mllm_enabled, parallel))


def run_iqa_folder_sync(
    folder_path: str,
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
    extensions: list[str] | None = None,
    recursive: bool = False,
) -> list[IqaResult]:
    """同步版本的文件夹检测"""
    return asyncio.run(run_iqa_folder(
        folder_path, standard_id, mllm_enabled, parallel, extensions, recursive
    ))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _convert_output_to_result(
    output: ActivityOutput,
    image_path: str,
    workflow_id: str,
) -> IqaResult:
    """将ActivityOutput转换为IqaResult"""
    
    if output.status == ActivityStatus.ERROR:
        return IqaResult(
            image_path=image_path,
            workflow_id=workflow_id,
            status="ERROR",
            error=output.error,
        )
    
    data = output.data or {}
    checks = data.get("checks", {})
    
    return IqaResult(
        image_path=image_path,
        workflow_id=workflow_id,
        status=output.status.value,
        route_decision=data.get("route_decision", ""),
        confidence=data.get("confidence", 0.0),
        resolution_passed=checks.get("resolution", {}).get("passed", False),
        resolution_detail=checks.get("resolution", {}).get("detail", ""),
        exposure_passed=checks.get("exposure", {}).get("passed", False),
        exposure_detail=checks.get("exposure", {}).get("detail", ""),
        focus_passed=checks.get("focus", {}).get("passed", False),
        focus_detail=checks.get("focus", {}).get("detail", ""),
        completeness_passed=checks.get("completeness", {}).get("passed", False),
        completeness_detail=checks.get("completeness", {}).get("detail", ""),
        deep_vision_triggered=data.get("deep_vision", {}).get("triggered", False),
        deep_vision_anomalies=data.get("deep_vision", {}).get("anomalies", []),
        mllm_error=data.get("deep_vision", {}).get("error"),
    )


def list_available_standards() -> list[str]:
    """列出所有可用的质量标准ID"""
    _, _, _, registry = _get_default_components()
    return registry.list_ids()


def get_standard_info(standard_id: str) -> dict[str, Any]:
    """获取质量标准的详细信息"""
    _, _, _, registry = _get_default_components()
    standard = registry.get(standard_id)
    return standard.to_dict()


def save_results_to_json(results: list[IqaResult], output_path: str) -> None:
    """保存检测结果到JSON文件"""
    data = [r.to_dict() for r in results]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def print_summary(results: list[IqaResult]) -> None:
    """打印检测结果摘要"""
    total = len(results)
    passed = sum(1 for r in results if r.is_passed)
    needs_review = sum(1 for r in results if r.needs_review)
    rejected = sum(1 for r in results if r.is_rejected)
    errors = sum(1 for r in results if r.has_error)
    
    print(f"\n{'='*50}")
    print(f"IQA检测摘要")
    print(f"{'='*50}")
    print(f"总计: {total}张")
    print(f"通过: {passed}张 ({passed/total*100:.1f}%)")
    print(f"需审查: {needs_review}张 ({needs_review/total*100:.1f}%)")
    print(f"拒绝: {rejected}张 ({rejected/total*100:.1f}%)")
    print(f"错误: {errors}张 ({errors/total*100:.1f}%)")
    print(f"{'='*50}\n")