"""IQA接口 — 图像质量检测入口。

使用方法:
  1. 设置图片路径（单张或批量）
  2. 调用run_iqa()执行检测
  3. 查看检测结果

示例:
  ```python
  from executionplane.interface.iqa_interface import run_iqa, IqaResult
  
  # 单张图片检测
  result = await run_iqa("/path/to/image.png")
  print(result.route_decision)
  
  # 批量检测
  results = await run_iqa_batch([
      "/path/to/image1.png",
      "/path/to/image2.png",
  ])
  
  # 使用自定义标准
  result = await run_iqa(
      "/path/to/image.png",
      standard_id="high_speed_line",
  )
  ```
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from ..activities.iqa_activity import IqaActivity
from ..activities.base import ActivityInput, ActivityOutput, ActivityStatus
from ..capabilities.numpy_cv_checker import NumpyCVRuleChecker
from ..capabilities.mllm_provider import MllmProvider
from ..capabilities.mock_providers import MockMllmProvider
from ..config.quality_standard import QualityStandardRegistry, QualityStandard
from ..weldmap.in_memory import InMemoryWeldMapClient


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------

@dataclass
class IqaResult:
    """IQA检测结果"""
    
    # 基本信息
    image_path: str
    workflow_id: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 检测状态
    status: str = ""  # OK / MARGINAL / NG / ERROR
    route_decision: str = ""  # AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW / REJECT
    confidence: float = 0.0
    
    # 各项检测结果
    resolution_passed: bool = False
    resolution_detail: str = ""
    exposure_passed: bool = False
    exposure_detail: str = ""
    focus_passed: bool = False
    focus_detail: str = ""
    completeness_passed: bool = False
    completeness_detail: str = ""
    
    # MLLM结果
    deep_vision_triggered: bool = False
    deep_vision_anomalies: list[str] = field(default_factory=list)
    mllm_error: str | None = None
    
    # 错误信息
    error: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "image_path": self.image_path,
            "workflow_id": self.workflow_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "route_decision": self.route_decision,
            "confidence": self.confidence,
            "checks": {
                "resolution": {
                    "passed": self.resolution_passed,
                    "detail": self.resolution_detail,
                },
                "exposure": {
                    "passed": self.exposure_passed,
                    "detail": self.exposure_detail,
                },
                "focus": {
                    "passed": self.focus_passed,
                    "detail": self.focus_detail,
                },
                "completeness": {
                    "passed": self.completeness_passed,
                    "detail": self.completeness_detail,
                },
            },
            "deep_vision": {
                "triggered": self.deep_vision_triggered,
                "anomalies": self.deep_vision_anomalies,
                "error": self.mllm_error,
            },
            "error": self.error,
        }
    
    def to_json(self) -> str:
        """转换为JSON"""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
    
    @property
    def is_passed(self) -> bool:
        """是否通过检测"""
        return self.status == "OK"
    
    @property
    def needs_review(self) -> bool:
        """是否需要人工审查"""
        return self.status == "MARGINAL"
    
    @property
    def is_rejected(self) -> bool:
        """是否被拒绝"""
        return self.status == "NG"
    
    @property
    def has_error(self) -> bool:
        """是否有错误"""
        return self.status == "ERROR"


# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------

# 默认组件（全局单例）
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
    """获取默认组件（懒加载）"""
    global _default_weldmap, _default_cv_checker, _default_mllm, _default_registry
    
    if _default_weldmap is None:
        _default_weldmap = InMemoryWeldMapClient()
    
    if _default_cv_checker is None:
        _default_cv_checker = NumpyCVRuleChecker()
    
    if _default_mllm is None:
        # 默认使用Mock（测试用）
        _default_mllm = MockMllmProvider()
    
    if _default_registry is None:
        _default_registry = QualityStandardRegistry()
    
    return _default_weldmap, _default_cv_checker, _default_mllm, _default_registry


def configure_mllm(mllm: MllmProvider | None) -> None:
    """配置MLLM提供者
    
    Args:
        mllm: MLLM提供者实例，None表示禁用MLLM
    
    Example:
        ```python
        # 禁用MLLM
        configure_mllm(None)
        
        # 使用真实MLLM
        from executionplane.capabilities.mllm_provider import OpenAIMllmProvider
        configure_mllm(OpenAIMllmProvider(api_key="..."))
        ```
    """
    global _default_mllm
    _default_mllm = mllm


def register_standard(standard: QualityStandard) -> None:
    """注册自定义质量标准
    
    Args:
        standard: 质量标准配置
    
    Example:
        ```python
        from executionplane.config.quality_standard import QualityStandard
        
        register_standard(QualityStandard(
            standard_id="my_custom",
            name="我的自定义标准",
            focus_laplacian_min=60.0,
        ))
        ```
    """
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
    """执行单张图片的质量检测
    
    Args:
        image_path: 图片路径（支持PNG/JPEG/TIFF/BMP）
        standard_id: 质量标准ID（默认: macro_weld）
            - macro_weld: 宏观焊缝检测（默认）
            - micro_metallography: 微观金相分析
            - high_speed_line: 高速产线检测（宽松）
            - night_inspection: 夜间检测
        workflow_id: 工作流ID（可选，自动生成）
        mllm_enabled: 是否启用MLLM（默认True）
    
    Returns:
        IqaResult: 检测结果
    
    Example:
        ```python
        # 基本使用
        result = await run_iqa("/path/to/weld_image.png")
        
        # 使用高速产线标准
        result = await run_iqa(
            "/path/to/image.png",
            standard_id="high_speed_line",
        )
        
        # 禁用MLLM
        result = await run_iqa(
            "/path/to/image.png",
            mllm_enabled=False,
        )
        
        # 查看结果
        print(f"状态: {result.status}")
        print(f"置信度: {result.confidence}")
        print(f"路由决策: {result.route_decision}")
        ```
    """
    
    # 验证图片路径
    path = Path(image_path)
    if not path.exists():
        return IqaResult(
            image_path=image_path,
            workflow_id=workflow_id or f"IQA-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            status="ERROR",
            error=f"图片文件不存在: {image_path}",
        )
    
    # 获取组件
    weldmap, cv_checker, mllm, registry = _get_default_components()
    
    # 如果禁用MLLM，临时设置为None
    actual_mllm = mllm if mllm_enabled else None
    
    # 创建IQA Activity
    iqa = IqaActivity(
        weldmap=weldmap,
        cv_checker=cv_checker,
        mllm=actual_mllm,
        standard_registry=registry,
        default_standard_id=standard_id,
    )
    
    # 生成workflow_id
    wf_id = workflow_id or f"IQA-{path.stem}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # 执行检测
    output = await iqa.execute(ActivityInput(
        workflow_id=wf_id,
        control_point_id="CP0",
        params={"image_path": str(path.absolute())},
    ))
    
    # 转换结果
    return _convert_output_to_result(output, image_path, wf_id)


async def run_iqa_folder(
    folder_path: str,
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
    extensions: list[str] | None = None,
    recursive: bool = False,
) -> list[IqaResult]:
    """检测文件夹中的所有图片
    
    Args:
        folder_path: 文件夹路径
        standard_id: 质量标准ID
        mllm_enabled: 是否启用MLLM
        parallel: 是否并行执行（默认False，串行执行）
        extensions: 图片扩展名列表（默认: ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif'])
        recursive: 是否递归搜索子文件夹（默认False）
    
    Returns:
        list[IqaResult]: 检测结果列表
    
    Example:
        ```python
        # 检测文件夹中所有图片
        results = await run_iqa_folder("/path/to/images/")
        
        # 递归检测子文件夹
        results = await run_iqa_folder(
            "/path/to/images/",
            recursive=True,
        )
        
        # 只检测PNG图片
        results = await run_iqa_folder(
            "/path/to/images/",
            extensions=['.png'],
        )
        
        # 并行检测（更快）
        results = await run_iqa_folder(
            "/path/to/images/",
            parallel=True,
        )
        ```
    """
    
    # 默认支持的图片扩展名
    default_extensions = ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.PNG', '.JPG', '.JPEG']
    exts = extensions or default_extensions
    
    # 获取文件夹中所有图片
    folder = Path(folder_path)
    if not folder.exists():
        print(f"⚠️ 文件夹不存在: {folder_path}")
        return []
    
    if not folder.is_dir():
        print(f"⚠️ 路径不是文件夹: {folder_path}")
        return []
    
    # 搜索图片文件
    image_paths = []
    if recursive:
        # 递归搜索
        for ext in exts:
            image_paths.extend(folder.rglob(f'*{ext}'))
            image_paths.extend(folder.rglob(f'*{ext.upper()}'))
    else:
        # 只搜索当前文件夹
        for ext in exts:
            image_paths.extend(folder.glob(f'*{ext}'))
            image_paths.extend(folder.glob(f'*{ext.upper()}'))
    
    # 转换为字符串列表并排序
    image_paths = sorted([str(p) for p in image_paths])
    
    if not image_paths:
        print(f"⚠️ 文件夹中没有找到图片: {folder_path}")
        print(f"  支持的扩展名: {exts}")
        return []
    
    print(f"找到 {len(image_paths)} 张图片")
    
    # 执行批量检测
    return await run_iqa_batch(
        image_paths,
        standard_id,
        mllm_enabled,
        parallel,
    )


async def run_iqa_batch(
    image_paths: list[str],
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
) -> list[IqaResult]:
    """批量执行图片质量检测
    
    Args:
        image_paths: 图片路径列表
        standard_id: 质量标准ID
        mllm_enabled: 是否启用MLLM
        parallel: 是否并行执行（默认False，串行执行）
    
    Returns:
        list[IqaResult]: 检测结果列表
    
    Example:
        ```python
        # 串行批量检测
        results = await run_iqa_batch([
            "/path/to/image1.png",
            "/path/to/image2.png",
            "/path/to/image3.png",
        ])
        
        # 并行批量检测（更快但占用更多资源）
        results = await run_iqa_batch(
            image_paths,
            parallel=True,
        )
        
        # 统计结果
        passed = sum(1 for r in results if r.is_passed)
        rejected = sum(1 for r in results if r.is_rejected)
        print(f"通过: {passed}, 拒绝: {rejected}")
        ```
    """
    
    if parallel:
        # 并行执行
        tasks = [
            run_iqa(path, standard_id, None, mllm_enabled)
            for path in image_paths
        ]
        results = await asyncio.gather(*tasks)
    else:
        # 串行执行
        results = []
        for path in image_paths:
            result = await run_iqa(path, standard_id, None, mllm_enabled)
            results.append(result)
    
    return results


def _convert_output_to_result(
    output: ActivityOutput,
    image_path: str,
    workflow_id: str,
) -> IqaResult:
    """将ActivityOutput转换为IqaResult"""
    
    if output.is_error:
        return IqaResult(
            image_path=image_path,
            workflow_id=workflow_id,
            status="ERROR",
            error=output.error,
        )
    
    data = output.data or {}
    checks = data.get("checks", {})
    
    resolution_check = checks.get("resolution", {})
    exposure_check = checks.get("exposure", {})
    focus_check = checks.get("focus", {})
    completeness_check = checks.get("completeness", {})
    
    deep_vision = data.get("deep_vision", {})
    
    return IqaResult(
        image_path=image_path,
        workflow_id=workflow_id,
        status=output.status.value,
        route_decision=data.get("route_decision", ""),
        confidence=data.get("confidence", 0.0),
        resolution_passed=resolution_check.get("passed", False),
        resolution_detail=resolution_check.get("detail", ""),
        exposure_passed=exposure_check.get("passed", False),
        exposure_detail=exposure_check.get("detail", ""),
        focus_passed=focus_check.get("passed", False),
        focus_detail=focus_check.get("detail", ""),
        completeness_passed=completeness_check.get("passed", False),
        completeness_detail=completeness_check.get("detail", ""),
        deep_vision_triggered=deep_vision.get("triggered", False),
        deep_vision_anomalies=deep_vision.get("anomalies", []),
        mllm_error=deep_vision.get("error"),
    )


# ---------------------------------------------------------------------------
# 同步接口（方便非async环境使用）
# ---------------------------------------------------------------------------

def run_iqa_sync(
    image_path: str,
    standard_id: str = "macro_weld",
    workflow_id: str | None = None,
    mllm_enabled: bool = True,
) -> IqaResult:
    """同步版本的IQA检测（阻塞调用）
    
    Args:
        image_path: 图片路径
        standard_id: 质量标准ID
        workflow_id: 工作流ID
        mllm_enabled: 是否启用MLLM
    
    Returns:
        IqaResult: 检测结果
    
    Example:
        ```python
        # 在非async环境中使用
        result = run_iqa_sync("/path/to/image.png")
        print(result.route_decision)
        ```
    """
    return asyncio.run(run_iqa(
        image_path,
        standard_id,
        workflow_id,
        mllm_enabled,
    ))


def run_iqa_batch_sync(
    image_paths: list[str],
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
) -> list[IqaResult]:
    """同步版本的批量检测
    
    Args:
        image_paths: 图片路径列表
        standard_id: 质量标准ID
        mllm_enabled: 是否启用MLLM
        parallel: 是否并行执行
    
    Returns:
        list[IqaResult]: 检测结果列表
    """
    return asyncio.run(run_iqa_batch(
        image_paths,
        standard_id,
        mllm_enabled,
        parallel,
    ))


def run_iqa_folder_sync(
    folder_path: str,
    standard_id: str = "macro_weld",
    mllm_enabled: bool = True,
    parallel: bool = False,
    extensions: list[str] | None = None,
    recursive: bool = False,
) -> list[IqaResult]:
    """同步版本的文件夹检测
    
    Args:
        folder_path: 文件夹路径
        standard_id: 质量标准ID
        mllm_enabled: 是否启用MLLM
        parallel: 是否并行执行
        extensions: 图片扩展名列表
        recursive: 是否递归搜索子文件夹
    
    Returns:
        list[IqaResult]: 检测结果列表
    
    Example:
        ```python
        # 在非async环境中使用
        results = run_iqa_folder_sync("/path/to/images/")
        print_summary(results)
        ```
    """
    return asyncio.run(run_iqa_folder(
        folder_path,
        standard_id,
        mllm_enabled,
        parallel,
        extensions,
        recursive,
    ))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def list_available_standards() -> list[str]:
    """列出所有可用的质量标准ID
    
    Returns:
        list[str]: 标准ID列表
    
    Example:
        ```python
        standards = list_available_standards()
        print(standards)
        # ['macro_weld', 'micro_metallography', 'high_speed_line', 'night_inspection']
        ```
    """
    _, _, _, registry = _get_default_components()
    return registry.list_ids()


def get_standard_info(standard_id: str) -> dict[str, Any]:
    """获取质量标准的详细信息
    
    Args:
        standard_id: 标准ID
    
    Returns:
        dict: 标准配置信息
    
    Example:
        ```python
        info = get_standard_info("macro_weld")
        print(info["name"])
        print(info["resolution"]["min_width"])
        ```
    """
    _, _, _, registry = _get_default_components()
    standard = registry.get(standard_id)
    return standard.to_dict()


# ---------------------------------------------------------------------------
# 结果输出
# ---------------------------------------------------------------------------

def save_results_to_json(
    results: list[IqaResult],
    output_path: str,
) -> None:
    """保存检测结果到JSON文件
    
    Args:
        results: 检测结果列表
        output_path: 输出文件路径
    
    Example:
        ```python
        results = await run_iqa_batch(image_paths)
        save_results_to_json(results, "iqa_results.json")
        ```
    """
    data = [r.to_dict() for r in results]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def print_summary(results: list[IqaResult]) -> None:
    """打印检测结果摘要
    
    Args:
        results: 检测结果列表
    
    Example:
        ```python
        results = await run_iqa_batch(image_paths)
        print_summary(results)
        # 输出:
        # 总计: 10张
        # 通过: 7张 (70%)
        # 需审查: 2张 (20%)
        # 拒绝: 1张 (10%)
        ```
    """
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