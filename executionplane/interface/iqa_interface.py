"""接口模块 — 用户使用入口。

提供简洁的API供用户调用:
  - run_iqa(): 单张图片质量检测
  - run_iqa_batch(): 批量图片质量检测
  - run_iqa_folder(): 文件夹图片质量检测
"""

from .iqa_interface import (
    run_iqa,
    run_iqa_batch,
    run_iqa_folder,
    run_iqa_sync,
    run_iqa_batch_sync,
    run_iqa_folder_sync,
    IqaResult,
    configure_mllm,
    register_standard,
    list_available_standards,
    get_standard_info,
    save_results_to_json,
    print_summary,
)

__all__ = [
    "run_iqa",
    "run_iqa_batch",
    "run_iqa_folder",
    "run_iqa_sync",
    "run_iqa_batch_sync",
    "run_iqa_folder_sync",
    "IqaResult",
    "configure_mllm",
    "register_standard",
    "list_available_standards",
    "get_standard_info",
    "save_results_to_json",
    "print_summary",
]