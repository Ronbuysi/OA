from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import preview_cache_dir


@dataclass(frozen=True)
class PreviewResult:
    path: str | None
    status: str
    summary: str = ""


def _cache_path(source: Path, suffix: str = ".jpg") -> Path:
    stat = source.stat()
    key = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    return preview_cache_dir() / f"{hashlib.sha256(key).hexdigest()}{suffix}"


def build_preview(path: str | Path, category: str, size: tuple[int, int] = (240, 180)) -> PreviewResult:
    source = Path(path)
    if not source.exists():
        return PreviewResult(None, "unavailable", "文件不存在")
    try:
        if category == "image":
            return _image_preview(source, size)
        if category == "psd":
            return _psd_preview(source, size)
        if category == "source":
            return _vector_preview(source, size)
        if category == "workorder":
            return _workbook_preview(source)
        if category == "pdf":
            return _pdf_preview(source, size)
    except Exception as exc:
        return PreviewResult(None, "failed", f"无法生成预览：{exc}")
    return PreviewResult(None, "icon", source.suffix.upper().lstrip(".") or "文件")


def _image_preview(source: Path, size: tuple[int, int]) -> PreviewResult:
    destination = _cache_path(source, ".png")
    if not destination.exists():
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", size, "#f4f6fa")
            left = (size[0] - image.width) // 2
            top = (size[1] - image.height) // 2
            canvas.paste(image, (left, top))
            canvas.save(destination, "PNG")
    return PreviewResult(str(destination), "ready")


PSD_COMPOSITE_MAX_BYTES = 300 * 1024 * 1024  # 只有没有内嵌合并图、必须逐层合成时才受这个体积限制


def _psd_preview(source: Path, size: tuple[int, int]) -> PreviewResult:
    destination = _cache_path(source, ".png")
    if destination.exists():
        return PreviewResult(str(destination), "ready")
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(source)
        image = psd.topil()  # 优先读 PSD 内嵌的合并图（“最大兼容性”数据），大文件也能秒出
        if image is None:
            if source.stat().st_size > PSD_COMPOSITE_MAX_BYTES:
                return PreviewResult(None, "failed", "PSD 没有内嵌预览且文件过大，暂不逐层合成")
            image = psd.composite()
        image = image.convert("RGB")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, "#f4f6fa")
        canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
        canvas.save(destination, "PNG")
        return PreviewResult(str(destination), "ready")
    except Exception as exc:
        return PreviewResult(None, "failed", f"PSD暂时无法预览：{exc}")


# Illustrator(.ai) 和 EPS 本质是 PDF 容器，PyMuPDF 可以直接渲染。
# CorelDRAW(.cdr)、C4D、AE、PR、InDesign 是各自私有的二进制格式，渲染不了，
# 只能退回显示扩展名占位。
RENDERABLE_VECTOR_EXTENSIONS = {".ai", ".eps"}


def _vector_preview(source: Path, size: tuple[int, int]) -> PreviewResult:
    if source.suffix.lower() not in RENDERABLE_VECTOR_EXTENSIONS:
        return PreviewResult(None, "icon", source.suffix.upper().lstrip(".") or "文件")
    destination = _cache_path(source, ".png")
    if destination.exists():
        return PreviewResult(str(destination), "ready")
    try:
        import fitz
        with fitz.open(source) as document:
            if not document.page_count:
                return PreviewResult(None, "failed", "源文件没有可渲染的页面")
            page = document[0]
            longest = max(page.rect.width, page.rect.height) or 1
            zoom = max(1.0, min(3.0, 900 / longest))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            image.thumbnail(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", size, "#f4f6fa")
            canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
            canvas.save(destination, "PNG")
    except Exception as exc:
        return PreviewResult(None, "failed", f"源文件暂时无法预览：{exc}")
    return PreviewResult(str(destination), "ready")


def _workbook_preview(source: Path) -> PreviewResult:
    from openpyxl import load_workbook
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        sheets = workbook.sheetnames
        first = workbook[sheets[0]] if sheets else None
        rows = []
        if first:
            for row in first.iter_rows(min_row=1, max_row=4, values_only=True):
                rows.append(" | ".join("" if value is None else str(value) for value in row[:6]))
    finally:
        workbook.close()
    summary = f"工作表：{', '.join(sheets[:5])}\n" + "\n".join(rows)
    return PreviewResult(None, "summary", summary)


def _pdf_preview(source: Path, size: tuple[int, int]) -> PreviewResult:
    import fitz
    destination = _cache_path(source, ".png")
    if not destination.exists():
        with fitz.open(source) as document:
            if not document.page_count:
                return PreviewResult(None, "failed", "PDF没有页面")
            pixmap = document[0].get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            image.thumbnail(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", size, "#f4f6fa")
            canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
            canvas.save(destination, "PNG")
    return PreviewResult(str(destination), "ready")


def open_file(path: str) -> None:
    if not path or not Path(path).exists():
        return
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", path])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path])


def reveal_file(path: str) -> None:
    target = Path(path)
    if os.name == "nt":
        import subprocess
        if target.is_dir():
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", "-R", str(target)])
    else:
        import subprocess
        folder = str(target if target.is_dir() else target.parent)
        subprocess.Popen(["xdg-open", folder])
