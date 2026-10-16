from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "VisualWorkbench"


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif os.name == "posix" and os.environ.get("XDG_DATA_HOME"):
        base = Path(os.environ["XDG_DATA_HOME"])
    elif os.name == "posix" and Path.home().joinpath("Library").exists():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    candidates = [base / APP_NAME, Path.cwd() / ".visual-workbench-data"]
    for result in candidates:
        try:
            result.mkdir(parents=True, exist_ok=True)
            return result
        except OSError:
            continue
    raise OSError("无法创建工作台本地数据目录")


def database_path() -> Path:
    return app_data_dir() / "workbench.sqlite3"


def preview_cache_dir() -> Path:
    result = app_data_dir() / "previews"
    result.mkdir(parents=True, exist_ok=True)
    return result


def user_known_folders() -> dict[str, Path]:
    """跨机器获取用户常用目录（桌面/下载/文档/图片），支持 Windows 目录重定向（如改到 D 盘或 OneDrive）。"""
    home = Path.home()
    defaults = {
        "Desktop": home / "Desktop",
        "Downloads": home / "Downloads",
        "Documents": home / "Documents",
        "Pictures": home / "Pictures",
    }
    if os.name != "nt":
        return defaults
    try:
        import winreg
        reg_map = {
            "Desktop": "Desktop",
            "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
            "Documents": "Personal",
            "Pictures": "My Pictures",
        }
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            for name, reg_name in reg_map.items():
                try:
                    raw, _ = winreg.QueryValueEx(key, reg_name)
                    if raw:
                        expanded = Path(os.path.expandvars(str(raw)))
                        if expanded.exists():
                            defaults[name] = expanded
                except OSError:
                    continue
    except Exception:
        pass
    return defaults
