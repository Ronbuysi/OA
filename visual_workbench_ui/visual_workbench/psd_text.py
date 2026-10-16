# -*- coding: utf-8 -*-
"""PSD 文字批量替换：按「精确整串」把指定文字图层里的内容换掉。

设计原则（对齐用户既有的 photoshop-batch-text-replace 做法，避免"乱换"）：

1. **精确整串匹配**，不是正则、不是关键词模糊匹配。
   jsx 里用 ``indexOf`` 判断、``split/join`` 替换 —— 与用户原本的脚本一致。
2. **只动 TEXT 图层**，其它图层一律不碰。
3. **查找内容必须来自真实扫描结果**（``scan_text_layers`` 的返回），
   由界面点选而不是手打：PSD 里常见全角冒号、波浪号变体、``\\r`` 软换行，
   手打几乎必错，而错一个字符就会"改 0 个图层"或误伤别处。
4. **先备份、改后独立校验**：备份原文件；改完用 psd-tools 直接读磁盘，
   对比结构计数与内容直方图，确认只有目标字符串变了。

执行通道（绕开失效的 Photoshop COM）：
``Photoshop.exe "<script.jsx>"`` —— 若 PS 已在运行，请求会被转发给**同一个实例**；
不新开进程、不需要 COM。见用户 skill 的实测记录。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Callable, Optional

# ══════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════
@dataclass
class TextLayerInfo:
    path: str          # 图层路径，如 /组 128/活动时间：10月1日-10月31日
    content: str


@dataclass
class TextScanResult:
    source: str
    layers: list[TextLayerInfo] = field(default_factory=list)
    top_layers: int = 0
    leaf_layers: int = 0
    error: Optional[str] = None

    @property
    def text_count(self) -> int:
        return len(self.layers)

    def unique_contents(self) -> list[tuple[str, int]]:
        """按内容去重统计，出现次数多的在前（界面用它给用户点选）。"""
        counter = Counter(item.content for item in self.layers)
        return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class ReplaceRule:
    old: str
    new: str


@dataclass
class ReplaceResult:
    source: str
    backup: Optional[str] = None
    changed: int = 0
    still_old: int = 0
    now_new: int = 0
    text_total: int = 0
    errors: list[str] = field(default_factory=list)
    saved: bool = False
    log_text: str = ""
    log_path: Optional[str] = None
    jsx_path: Optional[str] = None
    error: Optional[str] = None
    verify_ok: Optional[bool] = None
    verify_note: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.changed > 0 and self.still_old == 0


# ══════════════════════════════════════════════════════════════
#  Photoshop 可执行文件定位
# ══════════════════════════════════════════════════════════════
_CUSTOM_PS_EXE: Optional[Path] = None


def set_photoshop_exe(path: str | Path | None) -> Optional[Path]:
    """允许用户在设置或弹窗中手动指定 Photoshop.exe 路径（兼容便携版/非默认盘安装）。"""
    global _CUSTOM_PS_EXE
    if path and Path(path).is_file():
        _CUSTOM_PS_EXE = Path(path)
    else:
        _CUSTOM_PS_EXE = None
    return _CUSTOM_PS_EXE


def photoshop_exe_path() -> Optional[Path]:
    """跨机器查找 Photoshop.exe：手动指定 -> 环境变量 -> Windows 注册表 App Paths -> 多盘符 Adobe 目录。"""
    if _CUSTOM_PS_EXE and _CUSTOM_PS_EXE.is_file():
        return _CUSTOM_PS_EXE
    env_exe = os.environ.get("PHOTOSHOP_EXE", "").strip()
    if env_exe and Path(env_exe).is_file():
        return Path(env_exe)

    if os.name == "nt":
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe",
                    ) as key:
                        val, _ = winreg.QueryValueEx(key, "")
                        if val and Path(str(val).strip('"')).is_file():
                            return Path(str(val).strip('"'))
                except OSError:
                    continue
        except Exception:
            pass

    roots: list[Path] = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Adobe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Adobe",
    ]
    for drive in ("C", "D", "E", "F", "G"):
        for sub in ("Program Files/Adobe", "Program Files (x86)/Adobe", "Adobe"):
            candidate = Path(f"{drive}:/{sub}")
            if candidate not in roots:
                roots.append(candidate)

    for root_dir in roots:
        try:
            if not root_dir.is_dir():
                continue
            for exe in sorted(root_dir.glob("Adobe Photoshop*/Photoshop.exe"), reverse=True):
                if exe.is_file():
                    return exe
        except OSError:
            continue
    return None


def photoshop_is_running() -> bool:
    """PS 是否已在运行（决定 jsx 是转发给现有实例还是需要冷启动）。"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Photoshop.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return False
    return "photoshop.exe" in (out or "").lower()


# ══════════════════════════════════════════════════════════════
#  只读扫描：列出全部文字图层内容
# ══════════════════════════════════════════════════════════════
def scan_text_layers(source: str | Path) -> TextScanResult:
    """只读扫描，列出所有 TEXT 图层的内容与路径，并给出结构计数。"""
    src = Path(source)
    result = TextScanResult(source=str(src))
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(str(src))
    except Exception as exc:
        result.error = f"无法打开 PSD：{exc}"
        return result

    try:
        result.top_layers = sum(1 for _ in psd)

        def walk(layers, prefix: str) -> None:
            for layer in layers:
                name = str(getattr(layer, "name", "") or "")
                path = f"{prefix}/{name}"
                try:
                    if layer.is_group():
                        walk(layer, path)
                        continue
                except Exception:
                    continue
                result.leaf_layers += 1
                if getattr(layer, "kind", "") == "type":
                    text = getattr(layer, "text", "") or ""
                    if isinstance(text, str):
                        result.layers.append(TextLayerInfo(path=path, content=text))

        walk(psd, "")
    except Exception as exc:
        result.error = f"扫描图层失败：{exc}"
    return result


# ══════════════════════════════════════════════════════════════
#  jsx 生成
# ══════════════════════════════════════════════════════════════
def _js_str(text: str) -> str:
    """把 Python 字符串安全地变成 JS 字符串字面量。

    用 JSON 转义（JSON 字符串是 JS 字符串的子集），并强制 ASCII：
    ExtendScript 对非 ASCII 的处理依赖文件编码，全转成 \\uXXXX 就彻底没有编码风险。
    `\\r`（PSD 里多行文字的软换行）会被正确转义成 \\r。
    """
    return json.dumps(text, ensure_ascii=True)


_JSX_TEMPLATE = Template("""// Auto-generated by Visual Workbench - PSD text replace
// Exact whole-string match + split/join (no regex). TEXT layers only.
// Run it in Photoshop: File > Scripts > Browse...
#target photoshop

var LOG_PATH   = $log_path;
var FILE_PATH  = $file_path;
var TARGET_DOC = $target_doc;
var SAVE_DOC   = $save_doc;
var RULES = [$rules];

var lf = new File(LOG_PATH);
lf.encoding = "UTF-8";
lf.open("w");
function L(s) { lf.writeln(s); }

function main() {
    var t0 = new Date().getTime();
    L("=== START " + new Date().toString() + " ===");
    L("PS_VERSION=" + app.version);
    L("DOCS_AT_START=" + app.documents.length);
    for (var i = 0; i < app.documents.length; i++) {
        L("  OPEN_DOC[" + i + "]=" + app.documents[i].name);
    }

    var prevActiveName = "";
    try { prevActiveName = app.activeDocument.name; } catch (e) {}
    var prevDD = null;
    try { prevDD = app.displayDialogs; app.displayDialogs = DialogModes.NO; } catch (e) {}
    L("PREV_ACTIVE_DOC=" + prevActiveName);

    // 1) 定位目标文档：优先按完整路径比对已打开的文档（避免不同目录下同名 PSD 被误改）
    var f = new File(FILE_PATH);
    var doc = null;
    for (var j = 0; j < app.documents.length; j++) {
        try {
            if (app.documents[j].fullName && app.documents[j].fullName.fsName == f.fsName) {
                doc = app.documents[j];
                break;
            }
        } catch (e) {}
    }
    var openedByScript = 0;
    if (doc == null) {
        L("NOT_OPEN_OPENING_FILE SRC_EXISTS=" + f.exists);
        if (!f.exists) { L("ABORT: source file not found"); lf.close(); return "ABORT_NOFILE"; }
        doc = app.open(f);
        openedByScript = 1;
    } else {
        L("REUSED_OPEN_DOC");
    }
    L("OPENED_BY_SCRIPT=" + openedByScript);
    L("DOC=" + doc.name + "  " + doc.width + "x" + doc.height + "  mode=" + doc.mode);
    L("TOP_LAYERS=" + doc.layers.length);

    // 2) 递归遍历全部图层（含隐藏组），只对 TEXT 图层做精确整串替换
    var changed = 0, totalText = 0, errs = [], leafCount = 0;
    function walk(layers, pathStr) {
        for (var i = 0; i < layers.length; i++) {
            var lyr = layers[i];
            var cur = pathStr + "/" + lyr.name;
            try {
                if (lyr.typename === "LayerSet") {
                    walk(lyr.layers, cur);
                } else {
                    leafCount++;
                    if (lyr.kind == LayerKind.TEXT) {
                        totalText++;
                        var c = lyr.textItem.contents;
                        if (c) {
                            var updated = c;
                            for (var r = 0; r < RULES.length; r++) {
                                var o = RULES[r].old, n = RULES[r]["new"];
                                if (o && updated.indexOf(o) >= 0) {
                                    updated = updated.split(o).join(n);
                                }
                            }
                            if (updated != c) {
                                lyr.textItem.contents = updated;
                                changed++;
                                L("CHANGED[" + changed + "] " + cur);
                            }
                        }
                    }
                }
            } catch (e) {
                errs.push(cur + "  ::  " + e);
            }
        }
    }
    walk(doc.layers, "");

    L("---");
    L("LEAF_TOTAL=" + leafCount);
    L("TEXT_TOTAL=" + totalText);
    L("CHANGED=" + changed);
    L("ERRORS=" + errs.length);
    for (var k = 0; k < errs.length; k++) { L("  ERR " + errs[k]); }

    // 3) 复核：还有多少条旧的、多少条新的
    var stillOld = 0, nowNew = 0;
    function check(layers) {
        for (var i = 0; i < layers.length; i++) {
            var lyr = layers[i];
            try {
                if (lyr.typename === "LayerSet") { check(lyr.layers); }
                else if (lyr.kind == LayerKind.TEXT) {
                    var c = lyr.textItem.contents;
                    if (c) {
                        for (var r = 0; r < RULES.length; r++) {
                            if (RULES[r].old && c.indexOf(RULES[r].old) >= 0) stillOld++;
                            if (RULES[r]["new"] && c.indexOf(RULES[r]["new"]) >= 0) nowNew++;
                        }
                    }
                }
            } catch (e) {}
        }
    }
    check(doc.layers);
    L("---");
    L("VERIFY_STILL_OLD=" + stillOld);
    L("VERIFY_NOW_NEW=" + nowNew);

    // 4) 只保存目标文档；若是由脚本新打开的文档，保存后自动关闭释放内存，防止批量处理撑爆 PS 暂存盘
    var saveMsg = "";
    if (SAVE_DOC) {
        var t1 = new Date().getTime();
        try {
            app.activeDocument = doc;
            doc.save();
            saveMsg = "SAVE_OK";
        } catch (e) {
            saveMsg = "SAVE_ERR: " + e;
        }
        L(saveMsg + "  ms=" + (new Date().getTime() - t1));
        if (openedByScript == 1 && saveMsg == "SAVE_OK") {
            try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e) {}
        }
    } else {
        saveMsg = "SAVE_SKIPPED";
        L(saveMsg);
    }

    // 5) 还原使用者环境
    try { if (prevDD !== null) app.displayDialogs = prevDD; } catch (e) {}
    if (prevActiveName !== "" && prevActiveName !== TARGET_DOC) {
        try {
            for (var m = 0; m < app.documents.length; m++) {
                if (app.documents[m].name == prevActiveName) {
                    app.activeDocument = app.documents[m]; break;
                }
            }
        } catch (e) {}
    }

    L("TOTAL_MS=" + (new Date().getTime() - t0));
    L("=== END ===");
    lf.close();
    return "CHANGED=" + changed;
}
main();
""")


def build_jsx(
    jsx_path: str | Path,
    *,
    log_path: str | Path,
    psd_path: str | Path,
    rules: list[ReplaceRule],
    save: bool = True,
) -> Path:
    """生成替换脚本。

    `rules` 里的 old/new 是**完整字符串**，jsx 内用 indexOf + split/join 精确替换。
    文件按 UTF-8 **带 BOM** 写入（ExtendScript 没有 BOM 会按 ANSI/GBK 解析，
    中文会变乱码导致匹配不到 —— 这是踩过的坑）。
    """
    psd = Path(psd_path)
    # 用字符串拼接而不是 f-string —— 里面全是 JS 花括号，写成 f-string 极易出错
    rules_js = ", ".join(
        '{"old": ' + _js_str(r.old) + ', "new": ' + _js_str(r.new) + "}"
        for r in rules
    )
    body = _JSX_TEMPLATE.substitute(
        log_path=_js_str(str(Path(log_path)).replace("\\", "/")),
        file_path=_js_str(str(psd).replace("\\", "/")),
        target_doc=_js_str(psd.name),
        save_doc="true" if save else "false",
        rules=rules_js,
    )
    out = Path(jsx_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(body)
    return out


# ══════════════════════════════════════════════════════════════
#  执行
# ══════════════════════════════════════════════════════════════
def run_script(jsx_path: str | Path, exe: Optional[Path] = None) -> subprocess.Popen:
    """把 jsx 递给 Photoshop。优先尝试通过 PowerShell COM 直接唤醒运行中的 PS 执行，失败再退回 CLI。"""
    jsx_abs = str(Path(jsx_path).resolve())
    if os.name == "nt" and photoshop_is_running():
        try:
            escaped = jsx_abs.replace("'", "''")
            ps_cmd = (
                f"$app = New-Object -ComObject Photoshop.Application; "
                f"$null = $app.DoJavaScriptFile('{escaped}')"
            )
            return subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
    exe = exe or photoshop_exe_path()
    if exe is None:
        raise FileNotFoundError("没找到 Photoshop.exe")
    return subprocess.Popen(
        [str(exe), jsx_abs],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _read_log(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def wait_for_log(
    log_path: str | Path,
    *,
    timeout: float = 3600,
    started_within: Optional[float] = None,
    on_log: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """轮询日志直到出现 ``=== END ===``。返回 (是否跑完, 日志全文)。

    ``started_within`` 给"自动递交"用：PS 的 File 是带缓冲的，但脚本开头的
    ``=== START ===`` 会立刻落盘；超过这个秒数日志还不存在，就认为没送进去。
    手动模式（等用户在 PS 里点菜单）传 None —— 不设启动时限，一直等到超时。
    """
    log = Path(log_path)
    deadline = time.time() + timeout
    probe_deadline = (time.time() + started_within) if started_within else None
    last_len = -1
    while time.time() < deadline:
        if should_cancel and should_cancel():
            return False, _read_log(log)
        text = _read_log(log)
        if text:
            if "=== END ===" in text:
                return True, text
            # 日志攒到 4096 字节才 flush，只有长度变了才回调，避免刷屏
            if on_log and len(text) != last_len:
                last_len = len(text)
                on_log(text)
        elif probe_deadline and time.time() > probe_deadline:
            return False, ""      # 自动递交没生效
        time.sleep(0.6)
    return False, _read_log(log)


def _parse_log(text: str) -> dict:
    """从日志里抠出关键计数。"""
    out: dict[str, object] = {}
    for key in ("TEXT_TOTAL", "CHANGED", "LEAF_TOTAL", "TOP_LAYERS",
                "VERIFY_STILL_OLD", "VERIFY_NOW_NEW", "ERRORS", "TOTAL_MS"):
        m = re.search(rf"^{key}=(-?\d+)", text, re.MULTILINE)
        if m:
            out[key.lower()] = int(m.group(1))
    out["errors"] = re.findall(r"^  ERR (.+)$", text, re.MULTILINE)
    # 注意行尾还有 "  ms=1600"，不能用 $ 收尾
    m = re.search(r"^(SAVE_OK|SAVE_ERR[^\n]*|SAVE_SKIPPED)", text, re.MULTILINE)
    out["save"] = m.group(1) if m else ""
    return out


def verify_replacement(source: str | Path, rules: list[ReplaceRule],
                       before: Optional[TextScanResult] = None) -> tuple[bool, str]:
    """改完独立校验：不信脚本自己的报告，直接读磁盘上的文件。

    检查：① 旧串剩 0 条 ② 新串出现数 ≥ 命中数 ③ 结构计数与改前一致。
    """
    after = scan_text_layers(source)
    if after.error:
        return False, f"校验读取失败：{after.error}"
    olds = [r.old for r in rules if r.old]
    remaining = [i for i in after.layers if any(o in i.content for o in olds)]
    if remaining:
        return False, f"仍有 {len(remaining)} 个图层含旧文案"
    if before is not None:
        if (before.top_layers, before.leaf_layers, before.text_count) != (
                after.top_layers, after.leaf_layers, after.text_count):
            return False, (
                "图层结构变了："
                f"改前 {before.top_layers}/{before.leaf_layers}/{before.text_count}"
                f" → 改后 {after.top_layers}/{after.leaf_layers}/{after.text_count}")
    return True, "旧文案已清零，图层结构未变"


# ══════════════════════════════════════════════════════════════
#  编排
# ══════════════════════════════════════════════════════════════
def replace_text_in_psd(
    source: str | Path,
    rules: list[ReplaceRule],
    *,
    work_dir: str | Path,
    backup: bool = True,
    save: bool = True,
    try_auto: bool = True,
    auto_probe_timeout: float = 20,
    wait_timeout: float = 3600,
    on_stage: Optional[Callable[[str], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_need_manual: Optional[Callable[[str, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ReplaceResult:
    """对一个 PSD 执行批量文字替换。

    流程：改前快照 → 备份 → 生成 jsx →（可自动递交）→ 等日志 → 独立校验。

    **关于执行通道**：``Photoshop.exe script.jsx`` 在 PS **已经在运行时并不会把脚本
    转发进去**（新进程会直接退出，2026-10-13 实测）。用户原本用通的方式是在 PS 里
    「文件 > 脚本 > 浏览」手动运行。所以这里两种都支持：
    先试一次自动递交（``auto_probe_timeout`` 秒内日志没出现就放弃），
    失败则回调 ``on_need_manual(jsx_path, log_path)`` 让界面提示用户手动运行，
    然后**继续监听日志** —— 用户一跑完，这里会自动收结果并做校验。
    """
    src = Path(source)
    result = ReplaceResult(source=str(src))
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    def stage(text: str) -> None:
        if on_stage:
            on_stage(text)

    if not src.is_file():
        result.error = "文件不存在"
        return result
    valid_rules = [r for r in rules if r.old]
    if not valid_rules:
        result.error = "没有有效的替换规则（「查找内容」不能为空）"
        return result

    # 改前快照：既用于备份前确认可读，也用于改后比对结构
    stage("读取改前状态…")
    before = scan_text_layers(src)
    if before.error:
        result.error = before.error
        return result

    if backup:
        stage("备份原文件…")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = src.with_name(f"{src.name}.bak_{stamp}")
        try:
            shutil.copy2(src, target)
            if target.stat().st_size != src.stat().st_size:
                raise OSError("备份大小与原文件不一致")
            result.backup = str(target)
        except OSError as exc:
            result.error = f"备份失败，已中止（未做任何修改）：{exc}"
            return result

    # 文件名保持纯 ASCII：ExtendScript 的 File 处理非 ASCII 路径不可靠
    # （用户原本能跑通的那份 jsx 日志路径就是全英文的）
    stem = re.sub(r"[^0-9A-Za-z]+", "_", src.stem).strip("_")[:32] or "psd"
    short_hash = hashlib.sha256(str(src.resolve()).encode("utf-8")).hexdigest()[:8]
    jsx = work / f"replace_{stem}_{short_hash}.jsx"
    log = work / f"replace_{stem}_{short_hash}.log"
    try:
        log.unlink()
    except OSError:
        pass
    build_jsx(jsx, log_path=log, psd_path=src, rules=valid_rules, save=save)
    result.jsx_path = str(jsx)
    result.log_path = str(log)

    # ① 先试自动递交（PS 没在运行时冷启动也能跑）
    moved_forward = False
    if try_auto:
        exe = photoshop_exe_path()
        if exe is None:
            result.error = "没找到 Photoshop，请确认已安装 Adobe Photoshop"
            return result
        stage("尝试自动递交给 Photoshop…")
        try:
            run_script(jsx, exe)
            done, text = wait_for_log(
                log, timeout=auto_probe_timeout, started_within=auto_probe_timeout,
                on_log=on_log, should_cancel=should_cancel)
            moved_forward = bool(text)
        except Exception as exc:
            stage(f"自动递交失败（{exc}），改为手动运行")
        if not moved_forward and should_cancel and should_cancel():
            return result

    # ② 自动不行 → 交给用户手动在 PS 里运行，但继续盯着日志
    if not moved_forward and on_need_manual:
        on_need_manual(str(jsx), str(log))
        if should_cancel and should_cancel():
            return result
        stage("等待你在 Photoshop 中运行脚本…")
        done, text = wait_for_log(
            log, timeout=wait_timeout, started_within=None,
            on_log=on_log, should_cancel=should_cancel)
        if not text:
            result.error = ("等待超时：没有读到脚本日志。请确认已在 Photoshop 里"
                            "「文件 > 脚本 > 浏览」运行了脚本。")
            return result
    elif moved_forward:
        stage("Photoshop 已开始执行（自动通道）…")
        done, text = wait_for_log(
            log, timeout=wait_timeout, started_within=None,
            on_log=on_log, should_cancel=should_cancel)
    else:
        # 没给 on_need_manual 又不允许自动 → 无法推进
        result.error = "没有可用的执行通道"
        return result

    result.log_text = text
    parsed = _parse_log(text)
    result.text_total = int(parsed.get("text_total", 0) or 0)
    result.changed = int(parsed.get("changed", 0) or 0)
    result.still_old = int(parsed.get("verify_still_old", 0) or 0)
    result.now_new = int(parsed.get("verify_now_new", 0) or 0)
    result.errors = list(parsed.get("errors", []) or [])
    result.saved = str(parsed.get("save", "")).startswith("SAVE_OK")

    if not done:
        result.error = "等待超时：Photoshop 可能仍在处理，请稍后手动确认文件状态"
        return result

    if save and result.saved:
        stage("独立校验改后文件…")
        ok, note = verify_replacement(src, valid_rules, before)
        result.verify_ok, result.verify_note = ok, note
    else:
        result.verify_ok, result.verify_note = None, "未保存，跳过磁盘校验"
    return result
