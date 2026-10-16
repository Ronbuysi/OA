"""Capture isolated UI states for visual review without touching real libraries."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from visual_workbench.repository import Repository
from visual_workbench.ui.main_window import MainWindow


def settle(app: QApplication, seconds: float = 0.5) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.processEvents()
        time.sleep(0.02)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
    with tempfile.TemporaryDirectory(prefix="vw_ui_qa_") as temp:
        isolated = Path(temp)
        repo = Repository(isolated / "workbench.sqlite3")
        repo.set_setting("startup_splash", "0")
        repo.set_setting("theme", args.theme)
        repo.set_setting("product_library_root", str(isolated / "products"))
        repo.set_archive_dest(str(isolated / "archive"))
        window = MainWindow(repo)
        window.resize(args.width, args.height)
        window.show()
        settle(app)
        for name, index in (("home", 0), ("assets", 1), ("products", 2),
                            ("psd_tool", 8), ("psd_text", 9)):
            window.switch_page(index)
            settle(app, 0.35)
            path = args.out / f"{name}_{args.theme}_{args.width}x{args.height}.png"
            window.grab().save(str(path))
            print(path)
        window.close()


if __name__ == "__main__":
    main()
