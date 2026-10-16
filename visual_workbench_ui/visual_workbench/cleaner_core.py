# -*- coding: utf-8 -*-
"""
xlsx 单元格特殊符号清理核心逻辑
- 多规则字符清洗（Windows 非法字符/全角变体/零宽不可见字符/控制字符/emoji 等）
- WPS 生成的 xlsx（非常规 styles.xml）兼容修复后读取
- 安全写回：.bak 备份 + 值级替换写回（保留原表结构、合并单元格、列宽、图片）
"""
import os
import re
import shutil
import zipfile
import datetime

from openpyxl import load_workbook

# ============ 清理规则定义 ============
# 每条规则: (id, 名称, 默认开启, 说明, 字符判定函数, 替换函数)

WIN_ILLEGAL = '\\/:*?"<>|'


def _is_win_illegal(ch: str) -> bool:
    return ch in WIN_ILLEGAL


def _is_fullwidth_punct(ch: str) -> bool:
    """全角标点变体（从设计命名角度常被误用的）"""
    return ch in '（）：＊？＜＞｜／＼＂'


def _is_zero_width(ch: str) -> bool:
    cp = ord(ch)
    # 零宽空格/连接符/方向标记/BOM/软连字符
    return cp in (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x00AD,
                  0x2060, 0x061C, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E)


def _is_control_char(ch: str) -> bool:
    cp = ord(ch)
    if ch in '\t\n\r':
        return False  # 保留正常换行制表
    return (cp < 0x20) or (0x7F <= cp <= 0x9F)


def _is_variation_selector(ch: str) -> bool:
    cp = ord(ch)
    return 0xFE00 <= cp <= 0xFE0F  # emoji 变体选择符（如 对勾️ 里的 VS16）


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) or cp in (0x2B50, 0x3030, 0x303D)


def _is_dingbat_arrow(ch: str) -> bool:
    cp = ord(ch)
    return cp in (0x2713, 0x2717, 0x2714, 0x2718)  # ✓✗对勾✘


RULES = [
    ("win_illegal", "Windows 非法字符", True,
     "\\ / : * ? \" < > | —— 保存文件必报错的 9 个符号",
     _is_win_illegal, lambda ch: "_"),
    ("fullwidth", "全角标点转半角", False,
     "（）：＊？ 等全角符号转成对应半角（仅在勾选时执行）",
     _is_fullwidth_punct, None),  # None = 专用映射
    ("zero_width", "零宽/不可见字符", True,
     "零宽空格、BOM、方向控制符等肉眼看不见的字符，直接删除",
     _is_zero_width, lambda ch: ""),
    ("control", "控制字符", True,
     "除换行/制表外的控制字符（常来自网页复制粘贴），直接删除",
     _is_control_char, lambda ch: ""),
    ("variation", "emoji 变体选择符", True,
     "对勾️ 后面那个隐形的 VS16，删除后文字显示不变",
     _is_variation_selector, lambda ch: ""),
    ("emoji", "emoji 表情符号", False,
     "表情符号（😀 🎯 等，勾选后替换为空，谨慎使用）",
     _is_emoji, lambda ch: ""),
    ("dingbat", "对勾 / 叉号类符号", False,
     "勾选后替换为空（这类符号部分系统兼容差）",
     _is_dingbat_arrow, lambda ch: ""),
]

FULLWIDTH_MAP = {
    '（': '(', '）': ')', '：': ':', '＊': '*', '？': '?',
    '＜': '<', '＞': '>', '｜': '|', '／': '/', '＼': '\\', '＂': '"',
}


def clean_text(text: str, enabled_rules: set) -> tuple[str, list]:
    """清洗一个字符串。返回 (新文本, [(规则id, 原字符或描述, 计数), ...])"""
    if not text or not isinstance(text, str):
        return text, []
    changes = []

    # OOXML 转义序列（_x000B_ 等）：Excel/WPS 保存控制字符的实际存储格式，
    # openpyxl 读取后以字面文本出现，是最常见的"隐形脏字符"来源
    if "control" in enabled_rules:
        esc_hits = re.findall(r'_x[0-9A-Fa-f]{4}_', text)
        if esc_hits:
            # 真实控制字符(0x00-0x1F)对应的转义 → 删除；其余(如_x0041_='A')还原为原字符
            def _unescape(m):
                cp = int(m.group(1), 16)
                return '' if cp < 0x20 or cp in (0x7F,) else chr(cp)
            text = re.sub(r'_x([0-9A-Fa-f]{4})_', _unescape, text)
            changes.append(("ooxml_escape", "OOXML 转义序列", len(esc_hits)))

    # 全角转半角是映射式，单独处理
    if "fullwidth" in enabled_rules:
        n = sum(1 for c in text if c in FULLWIDTH_MAP)
        if n:
            for k, v in FULLWIDTH_MAP.items():
                text = text.replace(k, v)
            changes.append(("fullwidth", "全角标点", n))

    # 字符级规则：一趟扫描
    char_rules = [r for r in RULES if r[0] in enabled_rules and r[0] != "fullwidth"]
    if char_rules:
        out = []
        counters = {r[0]: 0 for r in char_rules}
        repl_map = {r[0]: r[5] for r in char_rules}
        check_map = {r[0]: r[4] for r in char_rules}
        order = [r[0] for r in char_rules]  # 按规则优先级，先匹配先得
        for ch in text:
            hit = None
            for rid in order:
                if check_map[rid](ch):
                    hit = rid
                    break
            if hit is None:
                out.append(ch)
            else:
                counters[hit] += 1
                rep = repl_map[hit](ch)
                if rep:
                    out.append(rep)
        for rid, n in counters.items():
            if n:
                name = next(r[1] for r in RULES if r[0] == rid)
                changes.append((rid, name, n))
        text = "".join(out)

    # 收尾：首尾空格规范化（把清理后留下的多余空格压缩）
    if text != text.strip():
        changes.append(("trim", "首尾空格", 1))
        text = text.strip()
    return text, changes


def fix_wps_styles(path: str) -> str | None:
    """
    修复 WPS 生成的 xlsx 非标准 styles.xml（openpyxl 报 Fill() takes no arguments 时）。
    返回修复后的临时文件路径；无需修复返回 None。
    """
    wb = None
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        return None
    except Exception:
        pass
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
    # 尝试修复
    tmp = path + ".__fix__.xlsx"
    minimal_styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
    )
    wb_check = None
    try:
        with zipfile.ZipFile(path, 'r') as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.namelist():
                data = zin.read(item)
                if item == 'xl/styles.xml':
                    data = minimal_styles.encode('utf-8')
                zout.writestr(item, data)
        # 验证可读
        wb_check = load_workbook(tmp, read_only=True, data_only=True)
        return tmp
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        if wb_check is not None:
            try:
                wb_check.close()
            except Exception:
                pass


def _is_formula_cell(cell, value) -> bool:
    """判断单元格是否为 Excel 公式（避免把 =SUM(A1:A10)/2 中的 : / " > < 误当非法字符替换成下划线）。"""
    if getattr(cell, "data_type", None) == "f":
        return True
    return isinstance(value, str) and value.startswith("=")


def scan_workbook(path: str, enabled_rules: set) -> dict:
    """扫描文件，返回统计结果（不修改文件，自动跳过公式单元格）。"""
    fixed = fix_wps_styles(path)
    src = fixed or path
    wb = None
    try:
        wb = load_workbook(src, data_only=False)
        results = []
        total_cells = total_hits = 0
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if _is_formula_cell(cell, v):
                        continue
                    if isinstance(v, str) and v:
                        total_cells += 1
                        cleaned, changes = clean_text(v, enabled_rules)
                        if changes:
                            total_hits += 1
                            results.append({
                                "sheet": ws.title,
                                "cell": cell.coordinate,
                                "original": v,
                                "cleaned": cleaned,
                                "changes": changes,
                            })
        return {"file": path, "results": results,
                "total_text_cells": total_cells, "dirty_cells": total_hits}
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
        if fixed and os.path.exists(fixed):
            os.remove(fixed)


def clean_workbook(path: str, enabled_rules: set, backup: bool = False) -> dict:
    """清理并保存文件。backup=True（或触发 WPS 样式降级修复）时自动先备份为 .bak。"""
    scan = scan_workbook(path, enabled_rules)
    if not scan["results"]:
        return {"file": path, "changed": False, "hits": 0, "backup": None}

    fixed = fix_wps_styles(path)
    backup_path = None
    if backup or fixed is not None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path + f".bak_{stamp}.xlsx"
        shutil.copy2(path, backup_path)

    src = fixed or path
    wb = None
    try:
        wb = load_workbook(src)  # 非 read_only，保留样式结构
        n_replaced = 0
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if _is_formula_cell(cell, v):
                        continue
                    if isinstance(v, str) and v:
                        cleaned, _ = clean_text(v, enabled_rules)
                        if cleaned != v:
                            cell.value = cleaned
                            n_replaced += 1
        wb.save(path)
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
        if fixed and os.path.exists(fixed):
            os.remove(fixed)
    return {"file": path, "changed": True, "hits": n_replaced, "backup": backup_path}
