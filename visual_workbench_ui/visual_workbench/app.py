from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import organizer, psd_text
from .config import database_path
from .repository import Repository
from .ui.main_window import MainWindow
from .ui.splash import StartupSplash
from .ui.wheel import install_wheel_policy


def build_application(argv, data_dir: Path | None = None):
    app = QApplication.instance() or QApplication(argv)
    # 全应用统一滚轮步长：不然列表类控件一格会滚成「一整页」
    install_wheel_policy(app)
    db_path = (data_dir / "workbench.sqlite3") if data_dir else database_path()
    repository = Repository(db_path)
    # 归档目标位置可以在设置页里改，存在 settings 表里
    organizer.set_dest_drive(repository.get_archive_dest())
    psd_text.set_photoshop_exe(repository.get_setting('photoshop_exe', '') or None)
    window = MainWindow(repository)
    return app, window


def main(argv=None):
    app, window = build_application(sys.argv if argv is None else argv)
    use_splash = (
        window.repository.get_setting("startup_splash", "1") != "0"
        and os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"
    )
    if use_splash:
        splash = StartupSplash(on_finished=window.reveal_with_fade)
        window._startup_splash = splash
        splash.start()
    else:
        window.show()
    return app.exec()
