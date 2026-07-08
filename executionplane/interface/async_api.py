"""IQA异步检测接口。"""

import asyncio
from datetime import datetime
from pathlib import Path

from ..activities.base import ActivityInput
from ..activities.iqa.activity import IqaActivity
from .config import _get_default_components
from .result import IqaResult, _convert_output_to_result


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
        control_point_id="CP0",
        workflow_context={"workflow_id": wf_id},
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
