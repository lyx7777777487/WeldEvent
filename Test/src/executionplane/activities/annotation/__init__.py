"""Annotation Activity 子模块 — 调用外部标注 MCP server。

接法 A（裸函数模式），见 activity.py 文件头注释。
"""

from .activity import annotation_activity

ANNOTATION_ACTIVITY_NAME = "annotation"

__all__ = ["annotation_activity", "ANNOTATION_ACTIVITY_NAME"]