# -*- coding: utf-8 -*-
"""设置页：外观（深浅色、磨砂背景、启动动画开关与预览）、数据维护、归档位置、关于。"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .. import maintenance, organizer
from ..config import app_data_dir, database_path, preview_cache_dir
from ..repository import Repository
from .icons import icon as painted_icon
from .layout import page_body
from . import styles as S
from .styles import get_theme

APP_VERSION = "v1.3 Studio Pro"


class _CleanupSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _CleanupWorker(QRunnable):
    """后台执行缓存清理：要删的文件多时删除本身也要花时间，别卡住界面。"""

    def __init__(self, db_path: Path, plan: maintenance.CleanupPlan):
        super().__init__()
        self.db_path = db_path
        self.plan = plan
        self.signals = _CleanupSignals()

    @Slot()
    def run(self):
        try:
            removed, rows, backup = maintenance.apply_cleanup(self.db_path, self.plan)
            self._emit(self.signals.done, (removed, rows, backup))
        except Exception as exc:
            self._emit(self.signals.failed, str(exc))

    @staticmethod
    def _emit(signal, payload):
        try:
            signal.emit(payload)
        except RuntimeError:
            pass


def _section(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("GlassCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(24, 18, 24, 18)
    layout.setSpacing(10)
    head = QLabel(title)
    head.setObjectName("CardTitle")
    layout.addWidget(head)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName("HintLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
    return card, layout


def _stat_line(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("BodyLabel")
    return label


class SettingsPage(QWidget):
    def __init__(self, repository: Repository, thread_pool, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.thread_pool = thread_pool
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = page_body(self, spacing=16, margins=(32, 26, 32, 24), scrollable=True)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel("外观与启动动画、数据维护、归档位置")
        subtitle.setObjectName("PageSubtitle")
        header.addWidget(subtitle)
        root.addLayout(header)

        root.addWidget(self._build_appearance())
        root.addWidget(self._build_data())
        root.addWidget(self._build_archive())
        root.addWidget(self._build_about())
        root.addStretch()

    def _build_appearance(self) -> QFrame:
        card, layout = _section(
            "外观与启动体验",
            "浅色适合白天，深色晚上看久了不累。磨砂背景会借用 Windows 的系统材质"
            "（Win11 用 Mica，Win10 退回 Acrylic），让窗口半透明透出桌面。",
        )
        row = QHBoxLayout()
        row.setSpacing(12)
        self.theme_label = _stat_line("")
        row.addWidget(self.theme_label)
        row.addStretch()
        self.theme_button = QPushButton("")
        self.theme_button.setObjectName("PrimaryButton")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._switch_theme)
        row.addWidget(self.theme_button)
        layout.addLayout(row)

        blur_row = QHBoxLayout()
        blur_row.setSpacing(12)
        self.backdrop_label = _stat_line("")
        blur_row.addWidget(self.backdrop_label)
        blur_row.addStretch()
        self.backdrop_button = QPushButton("")
        self.backdrop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.backdrop_button.clicked.connect(self._toggle_backdrop)
        blur_row.addWidget(self.backdrop_button)
        layout.addLayout(blur_row)

        splash_row = QHBoxLayout()
        splash_row.setSpacing(10)
        self.splash_label = _stat_line("")
        splash_row.addWidget(self.splash_label)
        splash_row.addStretch()
        self.preview_splash_button = QPushButton("预览启动动画")
        self.preview_splash_button.setObjectName("GhostAccentButton")
        self.preview_splash_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_splash_button.clicked.connect(self._preview_splash)
        splash_row.addWidget(self.preview_splash_button)
        self.splash_button = QPushButton("")
        self.splash_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.splash_button.clicked.connect(self._toggle_splash)
        splash_row.addWidget(self.splash_button)
        layout.addLayout(splash_row)
        return card

    def _build_data(self) -> QFrame:
        card, layout = _section(
            "数据维护",
            "预览缓存只放缩略图，删掉不影响原文件（下次浏览会自动重建）。"
            "源文件已经不在磁盘上的那份缓存属于纯垃圾，会被清掉。",
        )
        self.data_label = _stat_line("")
        layout.addWidget(self.data_label)
        self.cache_label = _stat_line("")
        layout.addWidget(self.cache_label)

        row = QHBoxLayout()
        row.setSpacing(10)
        refresh_button = QPushButton("刷新统计")
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.clicked.connect(self.refresh)
        row.addWidget(refresh_button)
        self.clean_button = QPushButton("清理预览缓存")
        self.clean_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clean_button.clicked.connect(self._clean_cache)
        row.addWidget(self.clean_button)
        backup_button = QPushButton("备份数据库")
        backup_button.setObjectName("PrimaryButton")
        backup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        backup_button.clicked.connect(self._backup_database)
        row.addWidget(backup_button)
        open_button = QPushButton("打开数据目录")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(self._open_data_dir)
        row.addWidget(open_button)
        row.addStretch()
        layout.addLayout(row)
        return card

    def _build_archive(self) -> QFrame:
        card, layout = _section(
            "归档位置",
            "「AI 归档」页把散落的设计文件挪到哪里。默认沿用你的 G 盘习惯；"
            "换了盘或者用了移动硬盘就在这里改。",
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        self.dest_input = QLineEdit()
        self.dest_input.setPlaceholderText(str(organizer.DEFAULT_DEST_DRIVE))
        row.addWidget(self.dest_input, 1)
        browse = QPushButton("浏览…")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._choose_dest)
        row.addWidget(browse)
        save = QPushButton("保存")
        save.setObjectName("PrimaryButton")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save_dest)
        row.addWidget(save)
        reset = QPushButton("恢复默认")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset_dest)
        row.addWidget(reset)
        layout.addLayout(row)
        self.dest_hint = _stat_line("")
        layout.addWidget(self.dest_hint)
        return card

    def _build_about(self) -> QFrame:
        card, layout = _section("关于")
        layout.addWidget(_stat_line(f"电商视觉工作台 {APP_VERSION}"))
        layout.addWidget(_stat_line("所有数据都存在本机，不上传任何地方。"))
        self.data_dir_label = _stat_line("")
        self.data_dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.data_dir_label)
        return card

    def refresh(self):
        theme = get_theme()
        self.theme_label.setText(f"当前：{'深色模式' if theme == 'dark' else '浅色模式'}")
        if theme == "dark":
            self.theme_button.setText("  切换到浅色")
            self.theme_button.setIcon(painted_icon("sun", "#FFFFFF", 16))
        else:
            self.theme_button.setText("  切换到深色")
            self.theme_button.setIcon(painted_icon("moon", "#FFFFFF", 16))

        backdrop_on = bool(getattr(self.window(), "_backdrop_enabled", False))
        self.backdrop_label.setText(
            "窗口磨砂背景：已开启（借系统材质，窗口半透明）" if backdrop_on
            else "窗口磨砂背景：未开启"
        )
        self.backdrop_button.setText("关闭" if backdrop_on else "开启")

        splash_on = self.repository.get_setting("startup_splash", "1") != "0"
        self.splash_label.setText(
            "程序启动流光动画：已开启（点击动画可随时跳过）" if splash_on
            else "程序启动流光动画：已关闭（直接打开主窗口）"
        )
        self.splash_button.setText("关闭" if splash_on else "开启")

        try:
            with self.repository._connection() as db:
                total = db.execute(
                    "SELECT COUNT(*) AS n FROM files WHERE is_available = 1"
                ).fetchone()["n"]
        except Exception:
            total = 0
        files, size = maintenance.cache_usage(preview_cache_dir())
        self.data_label.setText(f"素材 {total:,} 个 · 数据库 {maintenance.fmt_size(self._db_size())}")
        self.cache_label.setText(
            f"预览缓存 {files:,} 个文件，占用 {maintenance.fmt_size(size)}"
        )

        dest = organizer.DEST_DRIVE
        self.dest_input.setText(self.repository.get_archive_dest() or "")
        exists = Path(dest).exists()
        self.dest_hint.setText(
            f"当前归档到：{dest} {'（可访问）' if exists else '（当前访问不到，插上盘或改位置）'}"
        )
        self.dest_hint.setStyleSheet(
            f"font-size:12px;background:transparent;border:0;"
            f"color:{S._TEXT_TERTIARY if exists else S._DANGER};"
        )
        self.data_dir_label.setText(f"数据目录：{app_data_dir()}")

    @staticmethod
    def _db_size() -> int:
        path = database_path()
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def refresh_theme(self):
        self.refresh()

    def _switch_theme(self):
        window = self.window()
        target = "light" if get_theme() == "dark" else "dark"
        if hasattr(window, "apply_theme"):
            window.apply_theme(target)
        self.refresh()

    def _toggle_backdrop(self):
        window = self.window()
        if not hasattr(window, "set_backdrop"):
            return
        want = not self.repository.get_window_backdrop()
        applied = window.set_backdrop(want)
        if want and not applied:
            self.repository.set_window_backdrop(False)
            QMessageBox.information(
                self, "这台机器用不了磨砂背景",
                "磨砂背景要借 Windows 的系统背景材质（Win11 的 Mica 或 Win10 的 Acrylic），"
                "当前系统没提供。\n\n已保持普通不透明背景，其它功能不受影响。",
            )
        else:
            self.repository.set_window_backdrop(applied)
        self.refresh()

    def _toggle_splash(self):
        current = self.repository.get_setting("startup_splash", "1") != "0"
        self.repository.set_setting("startup_splash", "0" if current else "1")
        self.refresh()

    def _preview_splash(self):
        window = self.window()
        if hasattr(window, "preview_startup_splash"):
            window.preview_startup_splash()

    def _clean_cache(self):
        plan = maintenance.build_cleanup_plan(database_path(), preview_cache_dir())
        if plan.blocked:
            QMessageBox.warning(self, "已中止", plan.blocked_reason)
            return
        if not plan.dead_cache and not plan.dead_rows:
            QMessageBox.information(
                self, "没什么可清理的",
                f"预览缓存共 {plan.cache_files:,} 个文件（{maintenance.fmt_size(plan.cache_bytes)}），\n"
                "都是还有用的，无需清理。",
            )
            return
        answer = QMessageBox.question(
            self, "清理预览缓存",
            f"将删除 {len(plan.dead_cache):,} 个已经没用的预览文件，"
            f"释放约 {maintenance.fmt_size(plan.dead_bytes)}。\n"
            f"并清理数据库里 {len(plan.dead_rows):,} 条对应记录"
            "（清理前会自动备份数据库）。\n\n"
            "这些都是源文件已经不在磁盘上的记录，删掉不影响任何现有素材。\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.clean_button.setEnabled(False)
        self.cache_label.setText("正在清理，请稍候…")
        worker = _CleanupWorker(database_path(), plan)
        worker.signals.done.connect(self._on_cleaned)
        worker.signals.failed.connect(self._on_clean_failed)
        self.thread_pool.start(worker)

    def _on_cleaned(self, payload):
        removed, rows, backup = payload
        self.clean_button.setEnabled(True)
        self.refresh()
        detail = f"已删除 {removed:,} 个预览文件，清理 {rows:,} 条记录。"
        if backup:
            detail += f"\n\n清理前的数据库已备份到：\n{backup}"
        QMessageBox.information(self, "清理完成", detail)

    def _on_clean_failed(self, message: str):
        self.clean_button.setEnabled(True)
        self.refresh()
        QMessageBox.warning(self, "清理失败", message)

    def _backup_database(self):
        try:
            backup = maintenance.backup_database(database_path())
        except Exception as exc:
            QMessageBox.warning(self, "备份失败", str(exc))
            return
        QMessageBox.information(
            self, "备份完成",
            f"数据库已备份到：\n{backup}\n\n"
            f"大小 {maintenance.fmt_size(backup.stat().st_size)}。\n"
            "这个文件可以整体复制走，换电脑时放回同名位置即可。",
        )

    def _open_data_dir(self):
        from ..preview import reveal_file
        path = str(app_data_dir())
        os.makedirs(path, exist_ok=True)
        reveal_file(path)

    def _choose_dest(self):
        selected = QFileDialog.getExistingDirectory(
            self, "选择归档目标文件夹", self.dest_input.text() or str(organizer.DEST_DRIVE)
        )
        if selected:
            self.dest_input.setText(selected)
            self._save_dest()

    def _save_dest(self):
        text = self.dest_input.text().strip()
        self.repository.set_archive_dest(text or None)
        organizer.set_dest_drive(text or None)
        self.refresh()
        QMessageBox.information(
            self, "已保存",
            f"归档位置已改为：{organizer.DEST_DRIVE}",
        )

    def _reset_dest(self):
        self.repository.set_archive_dest(None)
        organizer.set_dest_drive(None)
        self.refresh()
        QMessageBox.information(
            self, "已恢复默认", f"归档位置恢复为：{organizer.DEST_DRIVE}",
        )
