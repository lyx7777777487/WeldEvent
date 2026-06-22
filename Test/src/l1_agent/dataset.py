"""dataset — 数据集元数据摘要，给 LLM 当上下文。

不让 LLM 看图本身（那是 cognitiveplane 视觉模式的事），只看元数据。
见 Test/docs/05-open-questions.md Q6。
"""

from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def summarize_dataset(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"count": 0, "format": "unknown", "sample_paths": [], "size_hint": "empty", "error": f"路径不存在: {path}"}

    if p.is_file():
        files = [p]
    else:
        files = sorted(
            f for f in p.rglob("*") if f.suffix.lower() in _IMAGE_EXTS
        )

    count = len(files)
    fmt = files[0].suffix.lower().lstrip(".") if files else "unknown"
    sample = [str(f) for f in files[:3]]
    if count == 0:
        hint = "empty"
    elif count < 10:
        hint = "small"
    elif count < 100:
        hint = "medium"
    else:
        hint = "large"

    return {
        "count": count,
        "format": fmt,
        "sample_paths": sample,
        "size_hint": hint,
    }