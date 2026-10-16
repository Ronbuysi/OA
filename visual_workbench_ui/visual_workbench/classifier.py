from __future__ import annotations

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"}
PSD_EXTENSIONS = {".psd", ".psb"}
# 设计源文件素材（非 PSD 的矢量/工程源文件）
SOURCE_EXTENSIONS = {".ai", ".eps", ".cdr", ".indd", ".aep", ".prproj", ".c4d"}
WORKORDER_EXTENSIONS = {".xls", ".xlsx"}


def classify_extension(extension: str) -> str:
    ext = extension.lower()
    if ext in PSD_EXTENSIONS:
        return "psd"
    if ext in SOURCE_EXTENSIONS:
        return "source"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in WORKORDER_EXTENSIONS:
        return "workorder"
    if ext == ".pdf":
        return "pdf"
    return "other"


# 文件库只收录这三类：设计源文件素材
RECORDED_CATEGORIES = {"psd", "source", "image"}
