"""IQA使用示例 — 如何使用IQA检测图片质量。

运行方式:
  ```bash
  python -m executionplane.examples.iqa_example
  ```
"""

import asyncio
from pathlib import Path

# 导入IQA接口
from executionplane.interface import (
    run_iqa,
    run_iqa_batch,
    run_iqa_folder,
    IqaResult,
    list_available_standards,
    get_standard_info,
    print_summary,
    save_results_to_json,
)


# ---------------------------------------------------------------------------
# 示例1: 单张图片检测
# ---------------------------------------------------------------------------

async def example_single_image():
    """单张图片质量检测示例"""
    
    # TODO: 替换为你的图片路径
    image_path = "/path/to/your/image.png"
    
    # 检查图片是否存在
    if not Path(image_path).exists():
        print(f"⚠️ 图片不存在: {image_path}")
        print("请修改 image_path 为你的实际图片路径")
        return
    
    print("\n" + "="*60)
    print("示例1: 单张图片质量检测")
    print("="*60)
    
    # 执行检测
    result = await run_iqa(image_path)
    
    # 打印结果
    print(f"\n图片路径: {result.image_path}")
    print(f"工作流ID: {result.workflow_id}")
    print(f"检测状态: {result.status}")
    print(f"路由决策: {result.route_decision}")
    print(f"置信度: {result.confidence:.2%}")
    
    print("\n各项检测结果:")
    print(f"  分辨率: {'✓ 通过' if result.resolution_passed else '✗ 未通过'}")
    print(f"  曝光: {'✓ 通过' if result.exposure_passed else '✗ 未通过'}")
    print(f"  对焦: {'✓ 通过' if result.focus_passed else '✗ 未通过'}")
    print(f"  完整性: {'✓ 通过' if result.completeness_passed else '✗ 未通过'}")
    
    if result.deep_vision_triggered:
        print(f"\nMLLM深度视觉:")
        print(f"  触发: 是")
        if result.deep_vision_anomalies:
            print(f"  发现异常: {result.deep_vision_anomalies}")
        if result.mllm_error:
            print(f"  错误: {result.mllm_error}")
    
    if result.error:
        print(f"\n错误: {result.error}")


# ---------------------------------------------------------------------------
# 示例2: 文件夹批量检测（推荐）
# ---------------------------------------------------------------------------

async def example_folder_detection():
    """文件夹批量检测示例 — 最简单的使用方式"""
    
    # TODO: 替换为你的图片文件夹路径
    folder_path = "/path/to/your/images/"
    
    # 检查文件夹是否存在
    if not Path(folder_path).exists():
        print(f"⚠️ 文件夹不存在: {folder_path}")
        print("请修改 folder_path 为你的实际图片文件夹路径")
        return
    
    print("\n" + "="*60)
    print("示例2: 文件夹批量检测")
    print("="*60)
    
    # 执行文件夹检测（自动扫描所有图片）
    results = await run_iqa_folder(folder_path)
    
    # 打印摘要
    print_summary(results)
    
    # 打印每张图片的结果
    print("\n详细结果:")
    for r in results:
        status_icon = "✓" if r.is_passed else "⚠" if r.needs_review else "✗"
        print(f"  {status_icon} {Path(r.image_path).name}: {r.route_decision} ({r.confidence:.0%})")
    
    # 保存结果到JSON
    output_path = "iqa_results.json"
    save_results_to_json(results, output_path)
    print(f"\n结果已保存到: {output_path}")


# ---------------------------------------------------------------------------
# 示例3: 递归检测子文件夹
# ---------------------------------------------------------------------------

async def example_recursive_detection():
    """递归检测子文件夹示例"""
    
    # TODO: 替换为你的图片文件夹路径
    folder_path = "/path/to/your/images/"
    
    if not Path(folder_path).exists():
        print("⚠️ 文件夹不存在，请修改路径")
        return
    
    print("\n" + "="*60)
    print("示例3: 递归检测子文件夹")
    print("="*60)
    
    # 递归检测所有子文件夹中的图片
    results = await run_iqa_folder(
        folder_path,
        recursive=True,  # 递归搜索
    )
    
    print(f"检测图片数: {len(results)}")
    print_summary(results)


# ---------------------------------------------------------------------------
# 示例4: 使用不同质量标准
# ---------------------------------------------------------------------------

async def example_different_standards():
    """使用不同质量标准的示例"""
    
    # TODO: 替换为你的图片路径
    image_path = "/path/to/your/image.png"
    
    if not Path(image_path).exists():
        print("⚠️ 图片不存在，请修改路径")
        return
    
    print("\n" + "="*60)
    print("示例4: 使用不同质量标准")
    print("="*60)
    
    # 列出可用标准
    standards = list_available_standards()
    print(f"\n可用标准: {standards}")
    
    # 使用不同标准检测
    for standard_id in standards[:2]:  # 只演示前2个
        print(f"\n使用标准: {standard_id}")
        
        # 获取标准信息
        info = get_standard_info(standard_id)
        print(f"  名称: {info['name']}")
        print(f"  分辨率要求: {info['resolution']['min_width']}x{info['resolution']['min_height']}")
        print(f"  对焦阈值: {info['focus']['laplacian_min']}")
        
        # 执行检测
        result = await run_iqa(image_path, standard_id=standard_id)
        print(f"  结果: {result.route_decision} ({result.confidence:.0%})")


# ---------------------------------------------------------------------------
# 示例5: 禁用MLLM（纯规则层）
# ---------------------------------------------------------------------------

async def example_without_mllm():
    """禁用MLLM的检测示例"""
    
    # TODO: 替换为你的图片文件夹路径
    folder_path = "/path/to/your/images/"
    
    if not Path(folder_path).exists():
        print("⚠️ 文件夹不存在，请修改路径")
        return
    
    print("\n" + "="*60)
    print("示例5: 禁用MLLM（纯规则层检测）")
    print("="*60)
    
    # 禁用MLLM检测（更快，<10ms/张）
    results = await run_iqa_folder(
        folder_path,
        mllm_enabled=False,
    )
    
    print_summary(results)
    print("延迟: <10ms/张（纯规则层）")


# ---------------------------------------------------------------------------
# 示例6: 并行检测（更快）
# ---------------------------------------------------------------------------

async def example_parallel_detection():
    """并行检测示例"""
    
    # TODO: 替换为你的图片文件夹路径
    folder_path = "/path/to/your/images/"
    
    if not Path(folder_path).exists():
        print("⚠️ 文件夹不存在，请修改路径")
        return
    
    print("\n" + "="*60)
    print("示例6: 并行检测（更快）")
    print("="*60)
    
    # 并行检测（多张图片同时处理）
    results = await run_iqa_folder(
        folder_path,
        parallel=True,  # 并行执行
    )
    
    print_summary(results)
    print("提示: 并行检测更快，但会占用更多CPU资源")


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

async def main():
    """运行所有示例"""
    
    print("\n" + "="*60)
    print("IQA图像质量检测示例")
    print("="*60)
    print("\n请先修改示例中的路径，然后运行")
    
    # 运行示例（取消注释以运行）
    
    # await example_single_image()
    # await example_folder_detection()      # 推荐：文件夹批量检测
    # await example_recursive_detection()
    # await example_different_standards()
    # await example_without_mllm()
    # await example_parallel_detection()
    
    print("\n提示:")
    print("  1. 修改 folder_path 为你的实际图片文件夹路径")
    print("  2. 取消注释相应的示例函数")
    print("  3. 运行: python -m executionplane.examples.iqa_example")
    print("\n最简单的使用方式:")
    print("  results = await run_iqa_folder('/path/to/images/')")


if __name__ == "__main__":
    asyncio.run(main())