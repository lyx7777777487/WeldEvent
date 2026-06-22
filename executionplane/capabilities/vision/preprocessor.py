"""图像预处理 Pipeline — IQA Agent 内部使用。

IQA 在执行规则层之前，需要对原始图像做轻量预处理:
  1. 格式解码: 支持 numpy array / 文件路径 / bytes / URL
  2. 有效性校验: 非空、未损坏、合法维度
  3. 颜色空间转换: 统一为 RGB 或 Gray
  4. 基础元数据提取: size / channels / dtype / bit_depth
  5. 可选缩放: 归一化到标准分辨率（用于跨图比较）

设计模式: Pipeline（链式处理），每步可独立替换/跳过。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# 预处理结果
# ---------------------------------------------------------------------------

class ImageFormat(str, Enum):
    """支持的输入格式。"""
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    BMP = "bmp"
    RAW = "raw"           # 原始字节流
    NUMPY = "numpy"       # 已解码的 numpy array
    UNKNOWN = "unknown"


@dataclass
class ImageMetadata:
    """图像基础元数据 — 预处理阶段提取。"""
    width: int = 0
    height: int = 0
    channels: int = 0
    dtype: str = ""
    bit_depth: int = 8
    format: ImageFormat = ImageFormat.UNKNOWN
    file_size_bytes: int = 0
    color_space: str = ""              # RGB / GRAY / RGBA / ...
    is_valid: bool = False
    error: str = ""


@dataclass
class PreprocessedImage:
    """预处理后的图像输出。"""
    image: NDArray[np.uint8] | None = None   # 处理后的图像数组
    metadata: ImageMetadata = field(default_factory=ImageMetadata)
    original_image: NDArray[np.uint8] | None = None  # 原始备份
    pipeline_steps_applied: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 预处理步骤接口
# ---------------------------------------------------------------------------

class PreprocessStep(ABC):
    """单步预处理的抽象。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """步骤名称，用于日志和追踪。"""
        ...

    @abstractmethod
    async def apply(
        self, image: NDArray[np.uint8] | None, metadata: ImageMetadata,
        params: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.uint8] | None, ImageMetadata]:
        """执行预处理步骤。
        
        Returns:
            (处理后图像, 更新后的元数据)。
            若图像无效返回 (None, metadata_with_error)。
        """
        ...

    async def skip(self, image: NDArray[np.uint8], metadata: ImageMetadata) -> bool:
        """是否应跳过此步骤。默认不跳过。"""
        return False


# ---------------------------------------------------------------------------
# 具体步骤实现
# ---------------------------------------------------------------------------

class DecodeStep(PreprocessStep):
    """步骤1: 格式解码 — 将各种输入源统一为 numpy array。

    支持的输入类型 (通过 params 指定):
      - "image_path": 文件路径
      - "image_array": 已有的 numpy array
      - "image_bytes": 原始 bytes (PNG/JPEG/TIFF/BMP)
      - "image_url": 远程 URL (预留)
    """

    @property
    def name(self) -> str:
        return "decode"

    async def apply(
        self,
        image: NDArray[np.uint8] | None,
        metadata: ImageMetadata,
        params: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.uint8] | None, ImageMetadata]:
        params = params or {}

        # 优先级: numpy array > 文件路径 > bytes > 无
        if "image_array" in params:
            arr = params["image_array"]
            if isinstance(arr, np.ndarray):
                decoded = arr.astype(np.uint8)
                metadata.format = ImageFormat.NUMPY
                metadata.is_valid = True
                self._fill_metadata(decoded, metadata)
                return decoded, metadata

        if "image_path" in params:
            decoded = await self._decode_from_file(params["image_path"], metadata)
            if decoded is not None:
                return decoded, metadata
            # 解码失败，标记错误
            metadata.is_valid = False
            metadata.error = f"无法解码图像文件: {params['image_path']}"
            return None, metadata

        if "image_bytes" in params:
            decoded = await self._decode_from_bytes(params["image_bytes"], metadata)
            if decoded is not None:
                return decoded, metadata
            metadata.is_valid = False
            metadata.error = "无法从 bytes 解码图像"
            return None, metadata

        # 如果已经传入了 image（非 None），直接使用
        if image is not None:
            metadata.is_valid = True
            self._fill_metadata(image, metadata)
            return image, metadata

        # 完全无输入
        metadata.is_valid = False
        metadata.error = "未提供任何图像输入 (需要 image_path/image_array/image_bytes)"
        return None, metadata

    async def _decode_from_file(
        self, path: str, metadata: ImageMetadata
    ) -> NDArray[np.uint8] | None:
        try:
            import cv2
            img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            if img is None:
                return None
            img = img.astype(np.uint8)

            # 推断格式
            ext = path.lower().split(".")[-1] if "." in path else ""
            format_map = {"png": ImageFormat.PNG, "jpg": ImageFormat.JPEG,
                         "jpeg": ImageFormat.JPEG, "tif": ImageFormat.TIFF,
                         "tiff": ImageFormat.TIFF, "bmp": ImageFormat.BMP}
            metadata.format = format_map.get(ext, ImageFormat.UNKNOWN)

            self._fill_metadata(img, metadata)
            return img
        except ImportError:
            # 无 OpenCV 时尝试 PIL
            try:
                from PIL import Image as PILImage
                pil_img = PILImage.open(path)
                img = np.array(pil_img).astype(np.uint8)
                metadata.format = ImageFormat(pil_img.format.lower()) if pil_img.format else ImageFormat.UNKNOWN
                self._fill_metadata(img, metadata)
                return img
            except Exception:
                return None
        except Exception:
            return None

    async def _decode_from_bytes(
        self, data: bytes, metadata: ImageMetadata
    ) -> NDArray[np.uint8] | None:
        try:
            import io
            from PIL import Image as PILImage
            pil_img = PILImage.open(io.BytesIO(data))
            img = np.array(pil_img).astype(np.uint8)
            metadata.format = ImageFormat(pil_img.format.lower()) if pil_img.format else ImageFormat.RAW
            metadata.file_size_bytes = len(data)
            self._fill_metadata(img, metadata)
            return img
        except Exception:
            return None

    @staticmethod
    def _fill_metadata(image: NDArray[np.uint8], meta: ImageMetadata) -> None:
        if image.ndim == 2:
            meta.height, meta.width = image.shape
            meta.channels = 1
            meta.color_space = "GRAY"
        elif image.ndim == 3:
            meta.height, meta.width, meta.channels = image.shape
            cs_map = {1: "GRAY", 2: "GA", 3: "RGB", 4: "RGBA"}
            meta.color_space = cs_map.get(meta.channels, f"C{meta.channels}")
        meta.dtype = str(image.dtype)
        meta.bit_depth = 8 * image.itemsize


class ValidateStep(PreprocessStep):
    """步骤2: 有效性校验 — 检查图像是否可用。"""

    def __init__(
        self,
        min_size: int = 32,
        max_dimension: int = 16384,
        allowed_channels: set[int] | None = None,
    ):
        self._min_size = min_size
        self._max_dim = max_dimension
        self._allowed_channels = allowed_channels or {1, 3, 4}

    @property
    def name(self) -> str:
        return "validate"

    async def apply(
        self,
        image: NDArray[np.uint8] | None,
        metadata: ImageMetadata,
        params: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.uint8] | None, ImageMetadata]:
        if image is None:
            metadata.is_valid = False
            metadata.error = "图像为空"
            return None, metadata

        h, w = image.shape[:2]

        if h < self._min_size or w < self._min_size:
            metadata.is_valid = False
            metadata.error = f"图像尺寸过小 ({w}x{h} < {self._min_size}x{self._min_size})"
            return None, metadata

        if h > self._max_dim or w > self._max_dim:
            metadata.is_valid = False
            metadata.error = f"图像尺寸过大 ({w}x{h} > {self._max_dim}x{self._max_dim})"
            return None, metadata

        channels = image.shape[2] if image.ndim == 3 else 1
        if channels not in self._allowed_channels:
            metadata.is_valid = False
            metadata.error = f"不支持的通道数: {channels}"
            return None, metadata

        # 检查全黑/全白
        if image.ndim == 3:
            gray_mean = float(np.mean(image))
        else:
            gray_mean = float(np.mean(image))

        if gray_mean < 1.0:
            metadata.is_valid = False
            metadata.error = "图像可能为全黑"
            return None, image, metadata  # type: ignore[return-value]

        if gray_mean > 254.0:
            metadata.is_valid = False
            metadata.error = "图像可能为全白"
            return None, image, metadata  # type: ignore[return-value]

        metadata.is_valid = True
        return image, metadata


class ColorConvertStep(PreprocessStep):
    """步骤3: 颜色空间转换 — 统一为目标颜色空间。"""

    def __init__(self, target: str = "RGB"):
        """
        Args:
            target: 目标颜色空间 ("RGB", "GRAY", "RGBA")
        """
        self._target = target.upper()

    @property
    def name(self) -> str:
        return f"color_convert_to_{self._target}"

    async def apply(
        self,
        image: NDArray[np.uint8] | None,
        metadata: ImageMetadata,
        params: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.uint8] | None, ImageMetadata]:
        if image is None:
            return None, metadata

        channels = image.shape[2] if image.ndim == 3 else 1

        # 已经是目标格式
        current = "GRAY" if image.ndim == 2 else {1: "GRAY", 3: "RGB", 4: "RGBA"}.get(channels, "?")
        if current == self._target:
            return image, metadata

        converted = self._convert(image, channels)
        if converted is not None:
            metadata.color_space = self._target
            new_ch = 1 if self._target == "GRAY" else len(self._target)
            metadata.channels = new_ch
        return converted or image, metadata

    def _convert(
        self, image: NDArray[np.uint8], from_channels: int
    ) -> NDArray[np.uint8] | None:
        try:
            import cv2
            if self._target == "GRAY":
                if from_channels in (3, 4):
                    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY if from_channels == 3 else cv2.COLOR_RGBA2GRAY)
                elif from_channels == 1:
                    return image
            elif self._target == "RGB":
                if from_channels == 4:
                    return cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
                elif from_channels == 1:
                    return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
                elif from_channels == 3:
                    return image
            elif self._target == "RGBA":
                if from_channels == 3:
                    return cv2.cvtColor(image, cv2.COLOR_RGB2RGBA)
                elif from_channels == 1:
                    return cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA)
        except ImportError:
            pass

        # Fallback: numpy 转换
        if self._target == "GRAY" and from_channels == 3:
            return (0.299 * image[:, :, 0].astype(float)
                  + 0.587 * image[:, :, 1].astype(float)
                  + 0.114 * image[:, :, 2].astype(float)).astype(np.uint8)
        if self._target == "RGB" and from_channels == 1:
            return np.stack([image, image, image], axis=-1)

        return None


class ResizeStep(PreprocessStep):
    """步骤4(可选): 缩放到标准分辨率。"""

    def __init__(self, target_width: int | None = None, target_height: int | None = None):
        self._tw = target_width
        self._th = target_height

    @property
    def name(self) -> str:
        return f"resize_{self._tw or 'auto'}x{self._th or 'auto'}"

    async def skip(self, image: NDArray[np.uint8], metadata: ImageMetadata) -> bool:
        """目标尺寸未设置时自动跳过。"""
        return self._tw is None and self._th is None

    async def apply(
        self,
        image: NDArray[np.uint8] | None,
        metadata: ImageMetadata,
        params: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.uint8] | None, ImageMetadata]:
        if image is None:
            return None, metadata

        h, w = image.shape[:2]
        tw = self._tw or w
        th = self._th or h

        if w == tw and h == th:
            return image, metadata

        try:
            import cv2
            resized = cv2.resize(image, (tw, th), interpolation=cv2.INTER_AREA if w > tw else cv2.INTER_LINEAR)
            metadata.width = tw
            metadata.height = th
            return resized, metadata
        except ImportError:
            # 简单 numpy 缩放（质量较差但可用）
            from numpy import arange
            row_indices = (arange(th) * h / th).astype(int)
            col_indices = (arange(tw) * w / tw).astype(int)
            if image.ndim == 3:
                resized = image[row_indices][:, col_indices]  # type: ignore[index]
            else:
                resized = image[row_indices][:, col_indices]  # type: ignore[index]
            metadata.width = tw
            metadata.height = th
            return resized, metadata


# ---------------------------------------------------------------------------
# 预处理 Pipeline 编排器
# ---------------------------------------------------------------------------

class ImagePreprocessingPipeline:
    """图像预处理流水线 — 串联多个 PreprocessStep。

    Usage::
        pipeline = ImagePreprocessingPipeline([
            DecodeStep(),
            ValidateStep(min_size=64),
            ColorConvertStep(target="RGB"),
            ResizeStep(target_width=2048, target_height=1536),
        ])
        result = await pipeline.run(params={"image_path": "/path/to/img.png"})
        if result.metadata.is_valid:
            image = result.image  # 可用于后续处理
    """

    def __init__(self, steps: list[PreprocessStep] | None = None):
        self._steps: list[PreprocessStep] = steps or []

    def add_step(self, step: PreprocessStep) -> "ImagePreprocessingPipeline":
        self._steps.append(step)
        return self

    async def run(
        self,
        params: dict[str, Any] | None = None,
        existing_image: NDArray[np.uint8] | None = None,
    ) -> PreprocessedImage:
        """执行完整预处理流水线。

        Args:
            params: 输入参数字典（传递给各步骤）
            existing_image: 已有图像（跳过解码步骤）

        Returns:
            PreprocessedImage 包含处理后的图像和元数据。
        """
        metadata = ImageMetadata()
        current: NDArray[np.uint8] | None = existing_image
        original: NDArray[np.uint8] | None = None
        applied_steps: list[str] = []

        for step in self._steps:
            # 检查是否跳过
            if current is not None and await step.skip(current, metadata):
                continue

            # 执行步骤
            current, metadata = await step.apply(current, metadata, params)
            applied_steps.append(step.name)

            # 记录原始图像（在第一步之后）
            if original is None and current is not None:
                original = current.copy()

            # 如果某步返回无效图像，提前终止
            if current is None and not metadata.is_valid:
                break

        return PreprocessedImage(
            image=current,
            metadata=metadata,
            original_image=original,
            pipeline_steps_applied=applied_steps,
        )

    @classmethod
    def default_iqa_pipeline(cls) -> "ImagePreprocessingPipeline":
        """创建 IQA 默认预处理流水线。

        步骤: 解码 → 校验 → 转RGB → 不缩放(IQA用原始分辨率判断)
        """
        return cls(steps=[
            DecodeStep(),
            ValidateStep(min_size=64, max_dimension=16384),
            ColorConvertStep(target="RGB"),
        ])

    @classmethod
    def default_iqa_strict_pipeline(cls) -> "ImagePreprocessingPipeline":
        """严格版 IQA 流水线（强制缩放到标准分辨率）。"""
        return cls(steps=[
            DecodeStep(),
            ValidateStep(min_size=64, max_dimension=16384),
            ColorConvertStep(target="RGB"),
            ResizeStep(target_width=2048, target_height=1536),
        ])
