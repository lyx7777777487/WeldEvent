"""可视化叠加层 - 把 bbox / mask / 推理结果画到图上再送 LLM。

解决语义理解的痛点: 原来只送原图 + 文本描述推理结果, LLM 难以精准判断
"标签与图像内容是否匹配"。叠加后 LLM 看到的是标注了框/掩码的图, 能做更
准确的区域级判断。

支持的叠加类型:
  - bbox 框 + 标签文字 (带颜色区分缺陷类型)
  - mask 半透明叠加
  - 推理结果 (SAM/检测器输出的 suspected_regions)
  - 前景/背景边界高亮
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    from numpy.typing import NDArray  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None
    np = None
    NDArray = Any  # type: ignore

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE_FOR_LLM = 1024

# 缺陷类型 -> 颜色 (BGR)
_LABEL_COLORS = {
    "气孔": (0, 255, 255),
    "裂纹": (0, 0, 255),
    "咬边": (0, 165, 255),
    "夹渣": (255, 0, 0),
    "未熔合": (255, 0, 255),
    "焊瘤": (0, 255, 0),
    "default": (128, 128, 128),
}


def _color_for(label: str) -> tuple[int, int, int]:
    """按缺陷类型选颜色, 未知用灰色。"""
    for key, color in _LABEL_COLORS.items():
        if key in label:
            return color
    return _LABEL_COLORS["default"]


def draw_bbox_overlay(
    image: NDArray,
    boxes: list[dict[str, Any]],
) -> NDArray:
    """在图上画 bbox 框 + 标签文字。

    Args:
        image: BGR 图像 (H, W, C)
        boxes: [{"bbox": [x,y,w,h] or [x1,y1,x2,y2], "label": "气孔", "confidence": 0.9}]

    Returns:
        画好框的图像副本 (不改原图)
    """
    if cv2 is None:
        return image
    overlay = image.copy()
    h, w = overlay.shape[:2]

    for box_info in boxes:
        box = box_info.get("bbox") or box_info.get("box")
        label = box_info.get("label", "")
        confidence = box_info.get("confidence")

        if not box or len(box) < 4:
            continue

        x, y, a, b = [float(v) for v in box[:4]]
        # 兼容 [x,y,w,h] / [x1,y1,x2,y2]
        bw = a - x if a > x and a <= w else a
        bh = b - y if b > y and b <= h else b
        x_i = max(0, min(w - 1, int(round(x))))
        y_i = max(0, min(h - 1, int(round(y))))
        bw_i = max(0, min(w - x_i, int(round(bw))))
        bh_i = max(0, min(h - y_i, int(round(bh))))

        color = _color_for(label)

        # 画框
        cv2.rectangle(overlay, (x_i, y_i), (x_i + bw_i, y_i + bh_i), color, 2)

        # 标签文字 (带背景)
        text = label
        if confidence is not None:
            text = f"{label} {confidence:.0%}"
        font_scale = 0.5
        thickness = 1
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        cv2.rectangle(overlay, (x_i, y_i - th - 4), (x_i + tw + 2, y_i), color, -1)
        cv2.putText(overlay, text, (x_i + 1, y_i - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness)

    return overlay


def draw_mask_overlay(
    image: NDArray,
    masks: list[dict[str, Any]],
    alpha: float = 0.4,
) -> NDArray:
    """在图上叠加半透明 mask。

    Args:
        image: BGR 图像
        masks: [{"mask": [[0,1,0,...],...]] 或 {"segmentation": "...", "label": "气孔"}]
        alpha: 叠加透明度

    Returns:
        叠加后的图像
    """
    if cv2 is None or np is None:
        return image
    overlay = image.copy()
    h, w = overlay.shape[:2]

    for mask_info in masks:
        mask = mask_info.get("mask") or mask_info.get("segmentation")
        label = mask_info.get("label", "")
        if mask is None:
            continue

        # 尝试转 numpy
        if isinstance(mask, list):
            try:
                mask_arr = np.array(mask, dtype=np.uint8)
            except Exception:
                continue
        elif isinstance(mask, np.ndarray):
            mask_arr = mask.astype(np.uint8)
        else:
            continue

        if mask_arr.shape != (h, w):
            # resize mask 到图像大小
            mask_arr = cv2.resize(mask_arr, (w, h), interpolation=cv2.INTER_NEAREST)

        color = _color_for(label)
        colored = np.zeros_like(overlay)
        colored[:] = color
        colored_mask = cv2.bitwise_and(colored, colored, mask=mask_arr)
        overlay = cv2.addWeighted(overlay, 1.0, colored_mask, alpha, 0)

    return overlay


def render_annotated_image(
    img_path: Path,
    annotations: Any = None,
    model_inference_regions: list[dict] | None = None,
) -> NDArray | None:
    """读取图片 + 叠加标注/推理结果, 返回可视化后的图像。

    Args:
        img_path: 图片路径
        annotations: 单图标注 (dict 或 list), 支持 bbox/mask
        model_inference_regions: 模型推理的疑似区域 [{"bbox":..., "label":..., "confidence":...}]

    Returns:
        叠加后的 BGR 图像, 或 None (读取失败)
    """
    if cv2 is None:
        return None
    img = cv2.imread(str(img_path))
    if img is None:
        return None

    boxes = []
    masks = []

    # 从标注提取 bbox/mask
    if annotations:
        items = _iter_annotation_items(annotations)
        for item in items:
            if "bbox" in item:
                boxes.append({"bbox": item["bbox"], "label": item.get("label", ""),
                              "confidence": item.get("confidence")})
            if "mask" in item or "segmentation" in item:
                masks.append({"mask": item.get("mask") or item.get("segmentation"),
                              "label": item.get("label", "")})

    # 从模型推理提取疑似区域
    if model_inference_regions:
        for region in model_inference_regions:
            if "bbox" in region:
                boxes.append({"bbox": region["bbox"], "label": region.get("label", "疑似"),
                              "confidence": region.get("confidence")})

    if masks:
        img = draw_mask_overlay(img, masks)
    if boxes:
        img = draw_bbox_overlay(img, boxes)

    return img


def encode_image_for_llm(
    image: NDArray | None,
    img_path: Path | None = None,
) -> str | None:
    """把图像 (可能是叠加后的) 编码为 base64 data URL, 缩放到合理尺寸。

    Args:
        image: BGR 图像 (叠加后), 如果 None 则尝试从 img_path 读取原图
        img_path: 备用路径

    Returns:
        data:image/jpeg;base64,... 或 None
    """
    if cv2 is None:
        return None

    if image is None and img_path is not None:
        image = cv2.imread(str(img_path))
    if image is None:
        return None

    h, w = image.shape[:2]
    if max(h, w) > MAX_IMAGE_SIZE_FOR_LLM:
        scale = MAX_IMAGE_SIZE_FOR_LLM / max(h, w)
        image = cv2.resize(image, (int(w * scale), int(h * scale)))

    _, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    b64 = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _iter_annotation_items(annotation: Any) -> list[dict]:
    """兼容多种标注格式, 提取 item 列表。"""
    if isinstance(annotation, dict):
        # 单个标注
        if "bbox" in annotation or "mask" in annotation or "segmentation" in annotation:
            return [annotation]
        # 批量标注 (bboxes/annotations/regions)
        for key in ("bboxes", "boxes", "annotations", "regions", "items"):
            value = annotation.get(key)
            if isinstance(value, list):
                return [i for i in value if isinstance(i, dict)]
        return []
    if isinstance(annotation, list):
        return [i for i in annotation if isinstance(i, dict)]
    return []
