"""验收 2.3: ≥10 张真实焊缝图片端到端测试 (plan §11.4 阶段 2 验收 2.3).

要求:
  - ≥10 张真实焊缝图片
  - ≥2 张无缺陷
  - ≥2 张多缺陷
  - 每张图过完整链路: POST /api/v1/chat/upload → ImageStore → ReAct (DeepSeek)
    → analyze_image(image_id) → ImageStore 取原图 → Volc vision_complete → 中文分析

测试策略:
  当前只有 unlabeled/ 角焊原图 (未标注), 不预设期望缺陷类型.
  断言改为 "vision 返回有效中文分析" 而非 "检测到特定缺陷":
    1. HTTP 200
    2. tier == react_function_calling (DeepSeek 主循环真实跑通)
    3. tools_used 含 analyze_image (LLM 自主决定调工具)
    4. reply 非空且含中文 (vision 返回有效分析)
    5. reply 长度 ≥ 100 字 (vision 给出结构化分析, 不是一句话)

可选标注目录 (defect_free/, multi_defect/) 预留给后续标注数据.

运行成本:
  12 张图 × 真实 DeepSeek + Volc vision API ≈ 13 分钟, ~$0.1.
  默认 skip, 仅在设置 WELDEVENT_RUN_ACCEPTANCE=1 时运行:
    WELDEVENT_RUN_ACCEPTANCE=1 pytest cognitiveplane/tests/test_interaction/test_acceptance_2_3_real_weld_images.py -v -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cognitiveplane.app import create_app


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "weld_images"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# 验收 2.3 硬性要求
MIN_TOTAL_IMAGES = 10

# 真实 API 调用成本高, 默认 skip; 设 WELDEVENT_RUN_ACCEPTANCE=1 才跑.
_RUN_ACCEPTANCE = os.getenv("WELDEVENT_RUN_ACCEPTANCE", "0") == "1"

acceptance_only = pytest.mark.skipif(
    not _RUN_ACCEPTANCE,
    reason="验收测试需真实 API 调用 (~13min, ~$0.1). 设 WELDEVENT_RUN_ACCEPTANCE=1 启用.",
)


def _collect_images(subdir: str) -> list[Path]:
    """收集指定子目录下所有图片文件 (按文件名排序, 测试可复现)."""
    folder = FIXTURES_DIR / subdir
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _all_images() -> list[Path]:
    """合并所有子目录的图片 (unlabeled + defect_free + single_defect + multi_defect)."""
    all_imgs: list[Path] = []
    for subdir in ("unlabeled", "defect_free", "single_defect", "multi_defect"):
        all_imgs.extend(_collect_images(subdir))
    return all_imgs


@pytest.fixture(scope="module")
def client() -> TestClient:
    """共享 app 实例 — 启动一次, 跑所有图片."""
    app = create_app()
    return TestClient(app)


@pytest.fixture(scope="module")
def available_images() -> list[Path]:
    """所有可用图片. 若总数 < 10, 测试跳过 (不 FAIL, 因为图片需用户提供)."""
    imgs = _all_images()
    if len(imgs) < MIN_TOTAL_IMAGES:
        pytest.skip(
            f"验收 2.3 要求 ≥{MIN_TOTAL_IMAGES} 张真实焊缝图片, "
            f"当前 {len(imgs)} 张. 请把图片放到 {FIXTURES_DIR}/unlabeled/"
        )
    return imgs


class TestAcceptance23RealWeldImages:
    """验收 2.3: 真实焊缝图片端到端测试."""

    def test_image_count_meets_acceptance(self, available_images: list[Path]) -> None:
        """验收 2.3 硬性要求: ≥10 张真实焊缝图片. 不调 API, 快速验证."""
        assert len(available_images) >= MIN_TOTAL_IMAGES, (
            f"图片数量 {len(available_images)} < {MIN_TOTAL_IMAGES}"
        )

    @acceptance_only
    @pytest.mark.asyncio
    async def test_each_image_e2e_analyze(
        self, client: TestClient, available_images: list[Path]
    ) -> None:
        """每张图过完整链路: upload → ReAct → analyze_image → vision_complete.

        断言:
          1. HTTP 200
          2. tier == react_function_calling
          3. tools_used 含 analyze_image
          4. reply 非空且含中文
          5. reply 长度 ≥ 100 字 (结构化分析)
        """
        failures: list[str] = []
        for img_path in available_images:
            label = f"{img_path.parent.name}/{img_path.name}"
            with open(img_path, "rb") as f:
                img_bytes = f.read()

            resp = client.post(
                "/api/v1/chat/upload",
                data={
                    "message": "请分析这张焊缝图片的质量,识别缺陷并给出质量等级评估。",
                    "operator_id": "op-acceptance-23",
                },
                files=[("files", (img_path.name, img_bytes, "image/jpeg"))],
            )

            # 1. HTTP 200
            if resp.status_code != 200:
                failures.append(f"{label}: HTTP {resp.status_code} - {resp.text[:200]}")
                continue

            data = resp.json()
            reply = data.get("reply", "") or ""
            tier = data.get("tier", "")
            tools_used = data.get("tools_used", []) or []

            # 2. tier
            if tier != "react_function_calling":
                failures.append(
                    f"{label}: tier={tier} (期望 react_function_calling)"
                )

            # 3. analyze_image 被调用
            if "analyze_image" not in tools_used:
                failures.append(
                    f"{label}: tools_used={tools_used} (期望含 analyze_image)"
                )

            # 4. reply 非空 + 含中文
            if not reply:
                failures.append(f"{label}: reply 为空")
            elif not any("一" <= ch <= "鿿" for ch in reply):
                failures.append(f"{label}: reply 无中文字符: {reply[:100]}")

            # 5. reply 长度 (vision 结构化分析通常 ≥ 100 字)
            if len(reply) < 100:
                failures.append(
                    f"{label}: reply 过短 ({len(reply)} 字): {reply[:100]}"
                )

            # 进度日志 (pytest -s 可见)
            print(
                f"\n[{label}] tier={tier} tools={tools_used} "
                f"reply_len={len(reply)}"
            )

        assert not failures, (
            f"{len(failures)}/{len(available_images)} 张图片测试失败:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    @acceptance_only
    def test_defect_free_subset_if_present(self, client: TestClient) -> None:
        """可选: 若有 defect_free/ 标注目录 (≥2 张), 验证模型不报严重缺陷.

        验收 2.3 要求 ≥2 张无缺陷. 当前只有 unlabeled/, 此测试在
        有标注数据时才跑, 否则 skip.
        """
        defect_free = _collect_images("defect_free")
        if len(defect_free) < 2:
            pytest.skip("defect_free/ 未标注或不足 2 张, 跳过")

        severe_keywords = ["未熔合", "未焊透", "裂纹", "IV级", "不合格"]
        false_positives: list[str] = []
        for img_path in defect_free:
            with open(img_path, "rb") as f:
                img_bytes = f.read()
            resp = client.post(
                "/api/v1/chat/upload",
                data={"message": "请分析这张焊缝图片的质量。", "operator_id": "op-df"},
                files=[("files", (img_path.name, img_bytes, "image/jpeg"))],
            )
            reply = resp.json().get("reply", "")
            hits = [kw for kw in severe_keywords if kw in reply]
            if hits:
                false_positives.append(
                    f"{img_path.name}: 误报 {hits}"
                )

        assert not false_positives, (
            f"无缺陷图被误报为严重缺陷:\n"
            + "\n".join(f"  - {f}" for f in false_positives)
        )

    @acceptance_only
    def test_multi_defect_subset_if_present(self, client: TestClient) -> None:
        """可选: 若有 multi_defect/ 标注目录 (≥2 张), 验证模型检测到 ≥2 类缺陷.

        验收 2.3 要求 ≥2 张多缺陷. 当前只有 unlabeled/, 此测试在
        有标注数据时才跑, 否则 skip.
        """
        multi_defect = _collect_images("multi_defect")
        if len(multi_defect) < 2:
            pytest.skip("multi_defect/ 未标注或不足 2 张, 跳过")

        defect_keywords = ["气孔", "夹渣", "裂纹", "咬边", "未熔合", "未焊透", "焊瘤", "飞溅"]
        under_detected: list[str] = []
        for img_path in multi_defect:
            with open(img_path, "rb") as f:
                img_bytes = f.read()
            resp = client.post(
                "/api/v1/chat/upload",
                data={"message": "请分析这张焊缝图片的质量。", "operator_id": "op-md"},
                files=[("files", (img_path.name, img_bytes, "image/jpeg"))],
            )
            reply = resp.json().get("reply", "")
            hits = [kw for kw in defect_keywords if kw in reply]
            if len(hits) < 2:
                under_detected.append(
                    f"{img_path.name}: 仅检测到 {hits} (期望 ≥2 类缺陷)"
                )

        assert not under_detected, (
            f"多缺陷图检测不足:\n"
            + "\n".join(f"  - {u}" for u in under_detected)
        )