"""IQA全局配置与质量标准管理。"""

from typing import Any

from ..capabilities.mllm_provider import MllmProvider
from ..capabilities.mock_providers import MockMllmProvider
from ..capabilities.numpy_cv_checker import NumpyCVRuleChecker
from ..config.quality_standard import QualityStandard, QualityStandardRegistry
from ..weldmap.in_memory import InMemoryWeldMapClient


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
