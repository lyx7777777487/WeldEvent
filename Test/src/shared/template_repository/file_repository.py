"""FileTemplateRepository — 文件存储的 WorkflowTemplate 仓库。

跨进程共享：L1 写盘 → worker 读盘。无需 in-memory 实例共享。
与 controlplane/adapter/template_loader.py 的 repository 钩子无缝衔接。

存储格式: {root_dir}/{template_id}@{template_version}.json
内容: WorkflowTemplate 的 dict 表示（asdict 后 _make_json_safe 的产物）。

第一版不做并发锁（L1 单进程写、worker 单进程读，文件系统原子写够用）。
不做版本列表查询（L1 知道自己 save 了什么版本）。
"""

import json
import os
from pathlib import Path
from typing import Protocol


class TemplateRepository(Protocol):
    """与 controlplane/adapter/template_loader.py 期望的 repository 接口对齐。"""

    async def load(self, template_id: str, template_version: str) -> dict | None: ...

    async def save(
        self, template_id: str, template_version: str, template: dict
    ) -> None: ...


class FileTemplateRepository:
    """文件 repo 实现。

    Usage::
        repo = FileTemplateRepository(root_dir="templates")
        await repo.save("annotation_v1", "1", template_dict)
        loaded = await repo.load("annotation_v1", "1")
    """

    def __init__(self, root_dir: str | os.PathLike = "templates"):
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    async def load(self, template_id: str, template_version: str) -> dict | None:
        path = self._path_for(template_id, template_version)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    async def save(
        self, template_id: str, template_version: str, template: dict
    ) -> None:
        path = self._path_for(template_id, template_version)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # 原子替换

    def _path_for(self, template_id: str, template_version: str) -> Path:
        safe_id = template_id.replace("/", "_").replace(" ", "_")
        return self._root / f"{safe_id}@{template_version}.json"