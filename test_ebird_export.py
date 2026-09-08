# -*- coding: utf-8 -*-
"""
eBird 观测记录导出（eBird Record Format CSV）测试。

格式依据 eBird 官方文档与模板：19 列固定顺序、**无表头**、字段内不得出现
引号、日期 M/D/YYYY、数量可为数字或 X。列错位会直接导致导入失败，因此列
顺序在测试里逐一钉死。

**为什么只给学名、不填通用名**（2026-09-07 真实导入验证得出）：
eBird 按**用户账号的显示语言**匹配通用名。同一个种在不同 locale 下名字不同：

    Myzomela obscura        en_US: Dusky Myzomela      en_AU: Dusky Honeyeater
    Oriolus flavocinctus    en_US: Green Oriole        en_AU: Yellow Oriole
    Myzomela erythrocephala en_US: Red-headed Myzomela en_AU: Red-headed Honeyeater

首次导入时填的是全球(Clements/en_US)名，结果在澳洲账号下这三个种全部被判
"unknown species"，而名字两地相同的十六个种全部成功——一一吻合。改为只填
学名后同一批数据导入成功（清单 S390924778）。

学名是分类学的唯一标识，不随 locale 变化，因此对任何国家的用户都稳。
eBird 导入后会按各自的语言显示对应的通用名。

Species are identified by scientific name only: eBird matches common names in
the account's display language, so a globally-correct common name is rejected
on e.g. an Australian account. Verified against a real import.
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


# ── 鸟名解析 / Species resolution ───────────────────────────────────────────

def test_known_species_is_identified_by_scientific_name_only():
    """
    已知鸟种只给学名，通用名一律留空。

    eBird 按账号显示语言匹配通用名：填全球名在澳洲账号下会被判 unknown
    （实测 Dusky Myzomela 被拒，该账号要的是 Dusky Honeyeater）。学名不随
    locale 变化，是唯一对所有用户都成立的标识。
    """
    from core.ebird_export import resolve_species

    s = resolve_species("澳洲中白鹭", "Plumed Egret")

    assert s.common_name == "", "通用名必须留空，否则非美式英语账号会导入失败"
    assert (s.genus, s.species) == ("Ardea", "plumifera")
    assert s.confident is True


def test_species_with_locale_dependent_name_still_works():
    """
    通用名因 locale 而异的鸟种（首次导入失败的正是这三个），只给学名即可。
    """
    from core.ebird_export import resolve_species

    for cn, en, expect in (
        ("暗摄蜜鸟", "Dusky Myzomela", ("Myzomela", "obscura")),
        ("绿鹂", "Green Oriole", ("Oriolus", "flavocinctus")),
        ("红头摄蜜鸟", "Red-headed Myzomela", ("Myzomela", "erythrocephala")),
    ):
        s = resolve_species(cn, en)
        assert s.common_name == "", f"{cn} 不该填通用名"
        assert (s.genus, s.species) == expect, f"{cn} 学名应为 {expect}"
        assert s.confident is True


def test_species_without_a_known_scientific_name_falls_back_to_common_name():
    """
    参考库里查不到学名时，退而填模型的英文名 —— 那是仅存的线索。

    这类记录 eBird 多半认不出，会让用户在导入界面手工选种；备注里写明来源
    以便核对。总比整行没有任何标识强。
    """
    from core.ebird_export import resolve_species

    s = resolve_species("查无此鸟", "No Such Bird Anywhere")

    assert (s.genus, s.species) == ("", "")
    assert s.common_name == "No Such Bird Anywhere", "无学名时用英文名兜底"
    assert s.confident is False
    assert "verify" in s.comment.lower()


def test_conflicting_chinese_and_english_names_are_flagged():
    """
    中英文名指向不同鸟种时标记待核对 —— 两个名字本应来自同一次识别，
    对不上说明记录有问题，不能默默挑一个。
    """
    from core.ebird_export import resolve_species

    # 「棕胸金鹃」= Chalcites minutillus，而 Shining Bronze Cuckoo
    # = Chalcites lucidus，是另一个种
    s = resolve_species("棕胸金鹃", "Shining Bronze Cuckoo")

    assert s.confident is False
    assert s.comment, "冲突时必须留下核对提示"


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

    species = [(e.species.genus, e.species.species) for e in lists[0].entries]
    assert ("Parus", "major") in species, "2★ 的大山雀应计入"
    assert ("Hirundo", "rustica") not in species, "1★ 的家燕不该计入"


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
    assert r[0] == ""                      # Common Name 留空（按学名匹配）
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
    assert first.startswith(",Hirundo,rustica,"), f"首行应是数据: {first!r}"


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

    assert (only_cn.genus, only_cn.species) == ("Hirundo", "rustica")
    assert (only_en.genus, only_en.species) == ("Hirundo", "rustica")
    assert only_cn.confident is True and only_en.confident is True


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


def test_species_missing_from_clements_still_resolves_by_scientific_name():
    """
    参考库 Clements 列为空的鸟种（有 105 个），照样能拿到学名并正常导出。

    白腹鹃鵙 Coracina papuensis 就是一例：曾因只看 Clements 通用名而被误判为
    「映射不确定」，改用学名后不再是问题。
    """
    from core.ebird_export import resolve_species

    s = resolve_species("白腹鹃鵙", "White-bellied Cuckooshrike")

    assert (s.genus, s.species) == ("Coracina", "papuensis")
    assert s.common_name == ""
    assert s.confident is True

