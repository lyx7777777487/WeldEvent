"""IQA结果输出与持久化。"""

import json

from .result import IqaResult


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
