"""IQA同步检测接口（方便非async环境使用）。"""

import asyncio

from .async_api import run_iqa, run_iqa_batch, run_iqa_folder
from .result import IqaResult


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
