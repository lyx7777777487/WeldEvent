"""文件处理模块 — 根据文件类型路由到不同的处理器。

支持的文件类型:
  - 图片 (jpg/png/tif/bmp) → 多模态模型分析
  - ZIP 压缩包 → 解压后批量处理图片
  - PDF → 文本提取 + LLM 分析
  - Word (docx) → 文本提取 + LLM 分析
  - Excel/CSV → 数据解析 + 统计分析
  - 视频 (mp4/avi) → 抽帧 + 多模态分析
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cognitiveplane.capability.provider import LLMResponse


class FileType(str, Enum):
    IMAGE = "image"
    ZIP = "zip"
    PDF = "pdf"
    DOCX = "docx"
    EXCEL = "excel"
    CSV = "csv"
    VIDEO = "video"
    UNKNOWN = "unknown"


@dataclass
class UploadedFile:
    """上传的原始文件（来自 multipart/form-data）。"""
    filename: str
    content_type: str
    size: int
    data: bytes


@dataclass
class ParsedFile:
    """解析后的文件。"""
    filename: str
    file_type: FileType
    mime_type: str
    size_bytes: int
    # 图片类型: base64 data URL
    image_data: str | None = None
    # 文本类型: 提取的文本内容
    text_content: str | None = None
    # 数据类型: 解析后的结构化数据
    structured_data: list[dict] | None = None
    # ZIP: 包含的子文件列表
    children: list["ParsedFile"] | None = None


@dataclass
class FileProcessResult:
    """文件处理结果。"""
    files: list[ParsedFile] = field(default_factory=list)
    summary: str = ""
    image_urls: list[str] = field(default_factory=list)
    text_context: str = ""


def _detect_file_type(filename: str, mime_type: str) -> FileType:
    """根据文件名和MIME类型判断文件类型。"""
    ext = os.path.splitext(filename)[1].lower()
    mime_type = (mime_type or "").lower()

    if mime_type.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"):
        return FileType.IMAGE
    if ext == ".zip" or mime_type == "application/zip":
        return FileType.ZIP
    if ext == ".pdf" or mime_type == "application/pdf":
        return FileType.PDF
    if ext == ".docx" or mime_type in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",):
        return FileType.DOCX
    if ext in (".xlsx", ".xls") or mime_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ):
        return FileType.EXCEL
    if ext == ".csv" or mime_type == "text/csv":
        return FileType.CSV
    if ext in (".mp4", ".avi", ".mov", ".mkv") or mime_type.startswith("video/"):
        return FileType.VIDEO
    return FileType.UNKNOWN


def _parse_data_url(data_url: str) -> tuple[str, str, bytes]:
    """解析 data URL，返回 (mime_type, filename, raw_bytes)。"""
    # 格式: data:<mime>;base64,<data> 或 data:<mime>;name=<filename>;base64,<data>
    if not data_url.startswith("data:"):
        raise ValueError(f"Invalid data URL format")

    header, _, encoded = data_url.partition(",")
    # header: data:<mime>;base64 或 data:<mime>;name=<filename>;base64
    meta = header[5:]  # 去掉 "data:"
    parts = meta.split(";")

    mime_type = parts[0] if parts else "application/octet-stream"
    filename = ""

    for part in parts[1:]:
        if part.startswith("name="):
            filename = part[5:]
        # base64 是最后一个标记，跳过

    raw_bytes = base64.b64decode(encoded)
    if not filename:
        ext = mimetypes.guess_extension(mime_type) or ".bin"
        filename = f"upload{ext}"

    return mime_type, filename, raw_bytes


class FileHandler:
    """统一文件处理器 — 根据文件类型路由到不同的处理逻辑。

    支持两种输入:
      - process_uploaded_files: 接收 UploadedFile 对象（multipart 上传，推荐）
      - process_files: 接收 base64 data URL 列表（兼容旧接口）
    """

    def __init__(self, llm_provider=None) -> None:
        self._llm = llm_provider

    async def process_uploaded_files(
        self,
        uploaded_files: list[UploadedFile],
        user_message: str = "",
    ) -> FileProcessResult:
        """处理 multipart 上传的文件（推荐方式，无大小限制）。"""
        result = FileProcessResult()
        all_images: list[str] = []
        all_text_parts: list[str] = []

        for uf in uploaded_files:
            try:
                file_type = _detect_file_type(uf.filename, uf.content_type)

                # 图片: 转 base64 data URL 供多模态模型使用
                image_data_url = None
                if file_type == FileType.IMAGE:
                    b64 = base64.b64encode(uf.data).decode()
                    image_data_url = f"data:{uf.content_type};base64,{b64}"

                parsed = await self._process_single(
                    uf.filename, file_type, uf.content_type, uf.data, image_data_url
                )
                result.files.append(parsed)

                if parsed.image_data:
                    all_images.append(parsed.image_data)
                if parsed.children:
                    for child in parsed.children:
                        if child.image_data:
                            all_images.append(child.image_data)
                if parsed.text_content:
                    all_text_parts.append(f"【{uf.filename}】\n{parsed.text_content}")

            except Exception as e:
                all_text_parts.append(f"【文件处理失败】{uf.filename}: {e}")

        result.image_urls = all_images
        result.text_context = "\n\n".join(all_text_parts)

        parts = []
        if all_images:
            parts.append(f"{len(all_images)}张图片")
        text_files = [f for f in result.files if f.text_content]
        if text_files:
            parts.append(f"{len(text_files)}个文档")
        data_files = [f for f in result.files if f.structured_data]
        if data_files:
            parts.append(f"{len(data_files)}个数据文件")
        result.summary = "、".join(parts) if parts else "无有效文件"

        return result

    async def process_files(
        self,
        file_data_urls: list[str],
        user_message: str = "",
    ) -> FileProcessResult:
        """处理上传的文件列表，返回统一结果。"""
        result = FileProcessResult()
        all_images: list[str] = []
        all_text_parts: list[str] = []

        for data_url in file_data_urls:
            try:
                mime_type, filename, raw_bytes = _parse_data_url(data_url)
                file_type = _detect_file_type(filename, mime_type)

                parsed = await self._process_single(
                    filename, file_type, mime_type, raw_bytes, data_url
                )
                result.files.append(parsed)

                # 收集图片
                if parsed.image_data:
                    all_images.append(parsed.image_data)
                if parsed.children:
                    for child in parsed.children:
                        if child.image_data:
                            all_images.append(child.image_data)

                # 收集文本
                if parsed.text_content:
                    all_text_parts.append(f"【{filename}】\n{parsed.text_content}")

            except Exception as e:
                all_text_parts.append(f"【文件处理失败】{filename}: {e}")

        result.image_urls = all_images
        result.text_context = "\n\n".join(all_text_parts)

        # 生成摘要
        parts = []
        images_count = len(all_images)
        if images_count:
            parts.append(f"{images_count}张图片")
        text_files = [f for f in result.files if f.text_content]
        if text_files:
            parts.append(f"{len(text_files)}个文档")
        data_files = [f for f in result.files if f.structured_data]
        if data_files:
            parts.append(f"{len(data_files)}个数据文件")
        result.summary = "、".join(parts) if parts else "无有效文件"

        return result

    async def _process_single(
        self,
        filename: str,
        file_type: FileType,
        mime_type: str,
        raw_bytes: bytes,
        data_url: str,
    ) -> ParsedFile:
        """处理单个文件。"""
        parsed = ParsedFile(
            filename=filename,
            file_type=file_type,
            mime_type=mime_type,
            size_bytes=len(raw_bytes),
        )

        if file_type == FileType.IMAGE:
            parsed.image_data = data_url

        elif file_type == FileType.ZIP:
            parsed.children = self._handle_zip(raw_bytes)
            parsed.text_content = f"ZIP压缩包，包含 {len(parsed.children)} 个文件"

        elif file_type == FileType.PDF:
            parsed.text_content = self._handle_pdf(raw_bytes)

        elif file_type == FileType.DOCX:
            parsed.text_content = self._handle_docx(raw_bytes)

        elif file_type == FileType.EXCEL:
            parsed.structured_data, parsed.text_content = self._handle_excel(raw_bytes)

        elif file_type == FileType.CSV:
            parsed.structured_data, parsed.text_content = self._handle_csv(raw_bytes)

        elif file_type == FileType.VIDEO:
            parsed.text_content = f"视频文件 {filename}（{len(raw_bytes)} 字节），需抽帧处理"

        else:
            parsed.text_content = f"不支持的文件类型: {mime_type}"

        return parsed

    # ── ZIP ──

    def _handle_zip(self, raw_bytes: bytes) -> list[ParsedFile]:
        """解压ZIP，提取图片和文档。"""
        children: list[ParsedFile] = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for name in zf.namelist():
                    if name.startswith("__MACOSX") or name.endswith("/"):
                        continue
                    entry_bytes = zf.read(name)
                    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    ft = _detect_file_type(name, mime)

                    if ft == FileType.IMAGE:
                        b64 = base64.b64encode(entry_bytes).decode()
                        ext = os.path.splitext(name)[1].lower()
                        mime_guess = mimetypes.guess_type(name)[0] or "image/jpeg"
                        children.append(ParsedFile(
                            filename=os.path.basename(name),
                            file_type=ft,
                            mime_type=mime_guess,
                            size_bytes=len(entry_bytes),
                            image_data=f"data:{mime_guess};base64,{b64}",
                        ))
                    elif ft in (FileType.PDF, FileType.DOCX):
                        child = ParsedFile(
                            filename=os.path.basename(name),
                            file_type=ft,
                            mime_type=mime,
                            size_bytes=len(entry_bytes),
                        )
                        if ft == FileType.PDF:
                            child.text_content = self._handle_pdf(entry_bytes)
                        elif ft == FileType.DOCX:
                            child.text_content = self._handle_docx(entry_bytes)
                        children.append(child)
        except zipfile.BadZipFile:
            pass
        return children

    # ── PDF ──

    def _handle_pdf(self, raw_bytes: bytes) -> str:
        """提取PDF文本内容。"""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            texts = []
            for page in doc:
                texts.append(page.get_text())
            doc.close()
            return "\n".join(texts)[:8000]  # 限制长度
        except ImportError:
            return "[PDF文本提取需要安装 PyMuPDF: pip install PyMuPDF]"
        except Exception as e:
            return f"[PDF解析失败: {e}]"

    # ── DOCX ──

    def _handle_docx(self, raw_bytes: bytes) -> str:
        """提取Word文档文本内容。"""
        try:
            from docx import Document
            doc = Document(io.BytesIO(raw_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            tables_text = []
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text for cell in row.cells]
                    tables_text.append(" | ".join(cells))
            return "\n".join(paragraphs + tables_text)[:8000]
        except ImportError:
            return "[Word文档提取需要安装 python-docx: pip install python-docx]"
        except Exception as e:
            return f"[Word解析失败: {e}]"

    # ── Excel ──

    def _handle_excel(self, raw_bytes: bytes) -> tuple[list[dict], str]:
        """解析Excel文件。"""
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw_bytes), read_only=True)
            all_rows: list[dict] = []
            text_parts: list[str] = []

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue
                headers = [str(h or f"col{i}") for i, h in enumerate(rows[0])]
                text_parts.append(f"=== 工作表: {sheet_name} ===")
                text_parts.append(" | ".join(headers))

                for row in rows[1:50]:  # 最多50行
                    row_dict = {headers[i]: str(v or "") for i, v in enumerate(row) if i < len(headers)}
                    all_rows.append(row_dict)
                    text_parts.append(" | ".join(str(v or "") for v in row))

            wb.close()
            return all_rows, "\n".join(text_parts)[:8000]
        except ImportError:
            return [], "[Excel解析需要安装 openpyxl: pip install openpyxl]"
        except Exception as e:
            return [], f"[Excel解析失败: {e}]"

    # ── CSV ──

    def _handle_csv(self, raw_bytes: bytes) -> tuple[list[dict], str]:
        """解析CSV文件。"""
        import csv
        try:
            text = raw_bytes.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            rows = []
            text_parts = []
            for i, row in enumerate(reader):
                if i >= 50:
                    break
                rows.append(dict(row))
                text_parts.append(" | ".join(str(v) for v in row.values()))
            return rows, "\n".join(text_parts)[:8000]
        except Exception as e:
            return [], f"[CSV解析失败: {e}]"
