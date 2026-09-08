# -*- coding: utf-8 -*-
"""
eBird 观测记录导出（eBird Record Format CSV）测试。

格式依据 eBird 官方文档与模板：19 列固定顺序、**无表头**、字段内不得出现
引号、日期 M/D/YYYY、数量可为数字或 X。列错位会直接导致导入失败，因此列
顺序在测试里逐一钉死。

鸟名映射是本功能的主要风险：模型的英文名约两成与 eBird(Clements) 命名不同
（Grey/Gray 拼写、分类学合并），直接填会被拒或收成别的鸟；而 avilist_map 里
少数低可信匹配本身是错的（Accipiter gentilis 苍鹰 → Common Ostrich 鸵鸟）。
错误的鸟名一旦填进 Common Name，eBird 会照收不误、用户看不到任何提示，等于
静默污染公共数据库。故低可信映射一律不填通用名、只给学名，让 eBird 自己去
匹配或提示——那才是用户能发现并修正的状态。

eBird Record Format: 19 fixed columns, no header row. Species naming is the
main hazard: an unreliable mapping silently records the wrong bird, so
low-confidence rows carry the scientific name only.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


# ── 列格式 / Column format ──────────────────────────────────────────────────

def test_column_order_matches_ebird_record_format():
    """
    19 列的名称与顺序必须与 eBird Record Format 模板逐字一致。

    eBird 明确说明：任何一列错位都会导致导入失败，且文件不带表头，
    因此顺序是唯一的对齐依据。
    """
    from core.ebird_export import EBIRD_COLUMNS

    assert EBIRD_COLUMNS == [
        "Common Name", "Genus", "Species", "Number", "Species Comments",
        "Location Name", "Latitude", "Longitude", "Date", "Start Time",
        "State/Province", "Country Code", "Protocol", "Number of Observers",
        "Duration", "All observations reported?", "Effort Distance Miles",
        "Effort area acres", "Submission Comments",
    ]


# ── 鸟名映射 / Species name resolution ──────────────────────────────────────

def test_confident_mapping_uses_ebird_common_name():
    """可信映射：填 eBird(Clements) 通用名 + 拆开的属名与种加词。"""
    from core.ebird_export import resolve_species

    s = resolve_species("澳洲中白鹭", "Plumed Egret")

    assert s.common_name == "Plumed Egret"
    assert (s.genus, s.species) == ("Ardea", "plumifera")
    assert s.confident is True
    assert s.comment == ""


def test_model_name_differing_from_ebird_is_translated():
    """
    模型英文名与 eBird 命名不同的（拼写差异/分类学变更），必须译成 eBird 的说法。

    直接填模型英文名会被 eBird 拒收——这正是必须走映射的原因。
    """
    from core.ebird_export import resolve_species

    s = resolve_species("新几内亚噪刺莺", "Grey Thornbill")

    assert s.common_name == "Gray Thornbill", "应译成 eBird 的美式拼写"
    assert s.confident is True


def test_unreliable_mapping_omits_common_name(monkeypatch):
    """
    低可信映射不得填通用名 —— 只给学名，并注明供人工核对。

    Accipiter gentilis(苍鹰) 在 avilist_map 里被错标成 Common Ostrich(鸵鸟)。
    填了它 eBird 会照收，用户看不到任何异常；只给学名则 eBird 会自行匹配或
    提示，错误才是可见、可修的。
    """
    from core.ebird_export import resolve_species

    s = resolve_species("苍鹰", "Northern Goshawk")

    assert s.common_name == "", f"低可信映射不得填通用名，实际 {s.common_name!r}"
    assert (s.genus, s.species) == ("Accipiter", "gentilis")
    assert s.confident is False
    assert "Northern Goshawk" in s.comment, "备注里要保留模型原始识别，供核对"


def test_unknown_species_still_exported_with_whatever_is_known():
    """
    完全查不到的鸟种也要导出（用户要求全部导出，到 eBird 网页端再修），
    但同样不能编造通用名。
    """
    from core.ebird_export import resolve_species

    s = resolve_species("查无此鸟", "No Such Bird Anywhere")

    assert s.common_name == ""
    assert s.confident is False
    assert "No Such Bird Anywhere" in s.comment


# ── 清单分组 / Checklist grouping ───────────────────────────────────────────

def _photo(filename, cn, en, date_time, rating=3, lat=None, lon=None):
    return {"filename": filename, "bird_species_cn": cn, "bird_species_en": en,
            "date_time_original": date_time, "rating": rating,
            "gps_latitude": lat, "gps_longitude": lon}


def test_photos_are_grouped_into_one_checklist_per_date():
    """一天一份清单 —— eBird 的一份清单对应一次外出。"""
    from core.ebird_export import build_checklists

    photos = [
        _photo("A", "家燕", "Barn Swallow", "2026-09-04 07:30:00"),
        _photo("B", "家燕", "Barn Swallow", "2026-09-04 09:00:00"),
        _photo("C", "大山雀", "Great Tit", "2026-09-05 06:15:00"),
    ]

    lists = build_checklists(photos, "Hasties Swamp")

    assert [c.date for c in lists] == ["9/4/2026", "9/5/2026"]


def test_same_species_appears_once_per_checklist_with_count_one():
    """同一天同一鸟种只出一行，数量固定 1。"""
    from core.ebird_export import build_checklists

    photos = [
        _photo("A", "家燕", "Barn Swallow", "2026-09-04 07:30:00"),
        _photo("B", "家燕", "Barn Swallow", "2026-09-04 09:00:00"),
        _photo("C", "家燕", "Barn Swallow", "2026-09-04 10:00:00"),
    ]

    lists = build_checklists(photos, "Hasties Swamp")

    assert len(lists) == 1
    assert len(lists[0].entries) == 1
    assert lists[0].entries[0].count == "1"


def test_low_rated_photos_are_excluded():
    """只统计 2★ 以上；低星多为虚焦/误识，不进观测记录。"""
    from core.ebird_export import build_checklists

    photos = [
        _photo("A", "家燕", "Barn Swallow", "2026-09-04 07:30:00", rating=1),
        _photo("B", "大山雀", "Great Tit", "2026-09-04 08:00:00", rating=2),
    ]

    lists = build_checklists(photos, "X")

    names = [e.species.common_name or e.species.genus for e in lists[0].entries]
    assert "Great Tit" in names
    assert all("Swallow" not in n for n in names)


def test_photos_without_species_or_date_are_skipped():
    """没识别出鸟种、或没有拍摄时间的照片无法构成观测记录。"""
    from core.ebird_export import build_checklists

    photos = [
        _photo("A", None, None, "2026-09-04 07:30:00"),
        _photo("B", "家燕", "Barn Swallow", None),
        _photo("C", "大山雀", "Great Tit", "2026-09-04 08:00:00"),
    ]

    lists = build_checklists(photos, "X")

    assert len(lists) == 1 and len(lists[0].entries) == 1


def test_start_time_is_the_earliest_photo_of_that_day():
    """开始时间取当天最早一张的拍摄时间。"""
    from core.ebird_export import build_checklists

    photos = [
        _photo("A", "家燕", "Barn Swallow", "2026-09-04 09:30:00"),
        _photo("B", "大山雀", "Great Tit", "2026-09-04 06:05:00"),
    ]

    lists = build_checklists(photos, "X")

    assert lists[0].start_time == "6:05"


def test_coordinates_use_the_median_of_that_day():
    """坐标取当天照片 GPS 的中位数，避免个别离群点把位置带偏。"""
    from core.ebird_export import build_checklists

    photos = [
        _photo("A", "家燕", "Barn Swallow", "2026-09-04 07:00:00", lat=-17.1, lon=145.4),
        _photo("B", "大山雀", "Great Tit", "2026-09-04 08:00:00", lat=-17.3, lon=145.6),
        _photo("C", "绿鹂", "Green Oriole", "2026-09-04 09:00:00", lat=-17.2, lon=145.5),
    ]

    lists = build_checklists(photos, "X")

    assert lists[0].latitude == "-17.2"
    assert lists[0].longitude == "145.5"


def test_missing_gps_leaves_coordinates_blank():
    """没有 GPS 就留空 —— 不能瞎猜位置，用户填的地点名足以让 eBird 定位。"""
    from core.ebird_export import build_checklists

    photos = [_photo("A", "家燕", "Barn Swallow", "2026-09-04 07:00:00")]

    lists = build_checklists(photos, "X")

    assert lists[0].latitude == "" and lists[0].longitude == ""


# ── CSV 输出 / CSV output ───────────────────────────────────────────────────

def test_csv_rows_have_no_header_and_fixed_fields():
    """
    输出无表头（eBird 要求），每行 19 个字段，固定值按协议填好。
    """
    from core.ebird_export import build_checklists, to_rows

    photos = [_photo("A", "家燕", "Barn Swallow", "2026-09-04 07:30:00",
                     lat=-17.2, lon=145.5)]
    rows = to_rows(build_checklists(photos, "Hasties Swamp"))

    assert len(rows) == 1
    r = rows[0]
    assert len(r) == 19
    assert r[0] == "Barn Swallow"          # Common Name
    assert (r[1], r[2]) == ("Hirundo", "rustica")
    assert r[3] == "1"                     # Number
    assert r[5] == "Hasties Swamp"         # Location Name
    assert (r[6], r[7]) == ("-17.2", "145.5")
    assert r[8] == "9/4/2026"              # Date
    assert r[9] == "7:30"                  # Start Time
    assert r[12] == "Incidental"           # Protocol
    assert r[13] == "1"                    # Number of Observers
    assert r[15] == "N"                    # All observations reported?
    assert r[14] == "" and r[16] == "" and r[17] == ""   # Duration / Effort


def test_quotes_are_stripped_from_free_text():
    """
    eBird 导入无法处理字段内的引号，必须先剔除（官方文档明确要求）。
    """
    from core.ebird_export import build_checklists, to_rows

    photos = [_photo("A", "家燕", "Barn Swallow", "2026-09-04 07:30:00")]
    rows = to_rows(build_checklists(photos, 'The "Big" Swamp'))

    assert '"' not in rows[0][5], f"地点名里的引号未剔除: {rows[0][5]!r}"


def test_written_file_has_no_header_row(tmp_path):
    """落盘的 CSV 第一行就是数据，不能有列名。"""
    from core.ebird_export import build_checklists, to_rows, write_csv

    photos = [_photo("A", "家燕", "Barn Swallow", "2026-09-04 07:30:00")]
    out = str(tmp_path / "ebird.csv")

    write_csv(out, to_rows(build_checklists(photos, "X")))

    first = open(out, encoding="utf-8").readline()
    assert "Common Name" not in first, "不得写表头"
    assert first.startswith("Barn Swallow"), f"首行应是数据: {first!r}"


def test_conflicting_chinese_and_english_names_are_not_trusted():
    """
    中文名与英文名指向不同鸟种时，不得挑一个信 —— 只给学名并标记待核对。

    两个名字本应来自同一次识别，对不上说明数据有问题（历史记录、手工改名
    改了一半等）。此时任选一个填进 Common Name，就是在 eBird 上静默记录一个
    可能错误的鸟种；留给用户核对才是安全的。
    """
    from core.ebird_export import resolve_species

    # 「棕胸金鹃」= Chalcites minutillus，而 Shining Bronze Cuckoo
    # = Chalcites lucidus，是另一个种
    s = resolve_species("棕胸金鹃", "Shining Bronze Cuckoo")

    assert s.common_name == "", f"名称冲突时不得填通用名，实际 {s.common_name!r}"
    assert s.confident is False


def test_single_available_name_is_used_when_the_other_is_missing():
    """只有中文名或只有英文名时（不同语言环境处理的批次），照常解析。"""
    from core.ebird_export import resolve_species

    only_cn = resolve_species("家燕", None)
    only_en = resolve_species(None, "Barn Swallow")

    assert only_cn.common_name == "Barn Swallow" and only_cn.confident is True
    assert only_en.common_name == "Barn Swallow" and only_en.confident is True


# ── 浏览器入口 / Browser entry point ────────────────────────────────────────

import os as _os
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _browser_with_birds(root: str):
    """建一个含两天、若干鸟种的库并打开浏览器。"""
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    specs = [
        ("A", "家燕", "Barn Swallow", "2026:09:04 07:30:00", 3, -17.1, 145.4),
        ("B", "家燕", "Barn Swallow", "2026:09:04 08:00:00", 3, -17.2, 145.5),
        ("C", "大山雀", "Great Tit", "2026:09:04 09:00:00", 2, -17.15, 145.45),
        ("D", "绿鹂", "Green Oriole", "2026:09:05 06:30:00", 3, None, None),
        ("E", "家燕", "Barn Swallow", "2026:09:04 10:00:00", 1, None, None),  # 1★ 排除
    ]
    for fn, cn, en, dt, rating, lat, lon in specs:
        rel = f"{cn}/3星_优选/{fn}.NEF"
        p = _os.path.join(root, rel)
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        db.insert_photo({"filename": fn, "current_path": rel, "original_path": rel,
                         "bird_species_cn": cn, "bird_species_en": en,
                         "date_time_original": dt, "rating": rating,
                         "gps_latitude": lat, "gps_longitude": lon})
    db.close()
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    return win


def test_browser_exports_ebird_csv(tmp_path, qt_app, monkeypatch):
    """
    浏览器里点「导出 eBird」→ 填地点名 → 选路径 → 生成可导入的 CSV。
    """
    from PySide6.QtWidgets import QInputDialog, QFileDialog
    import ui.results_browser_window as rbw

    root = str(tmp_path / "batch")
    _os.makedirs(root, exist_ok=True)
    out = str(tmp_path / "ebird.csv")

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Hasties Swamp", True)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out, "")))
    shown = []
    monkeypatch.setattr(rbw.StyledMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")))

    win = _browser_with_birds(root)
    try:
        win._export_ebird()
    finally:
        win.close()

    assert _os.path.exists(out), "应生成 CSV"
    import csv as _csv
    rows = list(_csv.reader(open(out, encoding="utf-8")))
    assert rows[0][0] != "Common Name", "不得有表头"
    assert all(len(r) == 19 for r in rows), "每行必须 19 列"
    # 9/4 两种(家燕、大山雀) + 9/5 一种(绿鹂)；1★ 的家燕不计
    assert len(rows) == 3, f"应导出 3 行，实际 {len(rows)}"
    assert {r[8] for r in rows} == {"9/4/2026", "9/5/2026"}
    assert all(r[5] == "Hasties Swamp" for r in rows)
    assert all(r[3] == "1" for r in rows), "数量固定 1"
    assert shown, "完成后应给出反馈"


def test_browser_export_cancelled_at_location_writes_nothing(tmp_path, qt_app, monkeypatch):
    """在填地点名那步取消 → 不生成任何文件。"""
    from PySide6.QtWidgets import QInputDialog, QFileDialog

    root = str(tmp_path / "batch")
    _os.makedirs(root, exist_ok=True)
    out = str(tmp_path / "never.csv")

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out, "")))

    win = _browser_with_birds(root)
    try:
        win._export_ebird()
    finally:
        win.close()

    assert not _os.path.exists(out)


def test_browser_export_warns_when_nothing_qualifies(tmp_path, qt_app, monkeypatch):
    """没有 2★以上带鸟种的照片时，明确提示而不是生成空文件。"""
    from PySide6.QtWidgets import QInputDialog, QFileDialog
    from tools.report_db import ReportDB
    import ui.results_browser_window as rbw

    root = str(tmp_path / "empty")
    _os.makedirs(root, exist_ok=True)
    db = ReportDB(root)
    db.insert_photo({"filename": "X", "current_path": "其他鸟类/1星_普通/X.NEF",
                     "original_path": "X.NEF", "rating": 1})
    db.close()

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Somewhere", True)))
    out = str(tmp_path / "none.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out, "")))
    warned = []
    monkeypatch.setattr(rbw.StyledMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a)))

    import ui.results_browser_window as rbw2
    win = rbw2.ResultsBrowserWindow()
    win.open_directory(root)
    try:
        win._export_ebird()
    finally:
        win.close()

    assert warned, "应提示无可导出内容"
    assert not _os.path.exists(out), "不得生成空文件"
