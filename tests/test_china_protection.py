# -*- coding: utf-8 -*-
"""
中国国家保护等级（core/china_protection.py）及其四处显示的测试。

覆盖：按名字查等级、只在中文界面给出文字、缺库/缺表降级；识别详情面板、
鸟名查询面板、后期工作区顶栏、分享报告在中英文界面下的显示与隐藏。
数据一律用临时参考库注入，不依赖真实 bird_reference.sqlite 的内容。

Tests for the China protection class lookup and its four display points
(detail panel, bird-name search, crop studio top bar, share report), with a
throwaway reference DB injected so real data never matters.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import china_protection as china  # noqa: E402

# 中华秋沙鸭=一级，红隼=二级；白头鹎不在名录 / Class I, Class II, unlisted
_ROWS = [
    ("中华秋沙鸭", "Mergus squamatus", 1),
    ("Scaly-sided Merganser", "Mergus squamatus", 1),
    ("Mergus squamatus", "Mergus squamatus", 1),
    ("红隼", "Falco tinnunculus", 2),
    ("Common Kestrel", "Falco tinnunculus", 2),
    ("Falco tinnunculus", "Falco tinnunculus", 2),
]


def _make_db(path, rows=_ROWS, with_table=True):
    """造一个只含 china_protection 表的参考库 / build a minimal reference DB."""
    con = sqlite3.connect(path)
    if with_table:
        con.execute("CREATE TABLE china_protection (name TEXT PRIMARY KEY, "
                    "scientific_name TEXT NOT NULL, level INTEGER NOT NULL)")
        con.executemany("INSERT INTO china_protection VALUES (?, ?, ?)", rows)
    else:
        con.execute("CREATE TABLE BirdCountInfo (id INTEGER)")
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def china_db(tmp_path):
    """
    注入临时参考库并在测试后清缓存，避免影响其它测试。
    Inject a throwaway DB; clear the module cache afterwards.
    """
    china.load_table(_make_db(tmp_path / "ref.sqlite"))
    yield
    china.reset_cache()


# ---------------------------------------------------------------- 查询 / lookup

def test_level_by_any_name(china_db):
    """中文名、英文名、学名都能查到；不在名录的为 None / Any name works."""
    assert china.get_level("中华秋沙鸭") == 1
    assert china.get_level(None, "Common Kestrel") == 2
    assert china.get_level("", "  ", "Falco tinnunculus") == 2
    assert china.get_level("白头鹎", "Light-vented Bulbul") is None
    assert china.get_level() is None


def test_first_matching_name_wins(china_db):
    """自定义中文名查不到时退到英文名 / Falls back to the next name."""
    assert china.get_level("我自己起的名字", "Scaly-sided Merganser") == 1


def test_label_only_in_chinese_ui():
    """简体、繁体给文字，英文界面一律 None / Labels for Chinese UIs only."""
    assert china.label(1, "zh_CN") == "国家一级"
    assert china.label(2, "zh_CN") == "国家二级"
    assert china.label(1, "zh_TW") == "國家一級"
    assert china.label(2, "zh_TW") == "國家二級"
    assert china.label(1, "en_US") is None
    assert china.label(None, "zh_CN") is None
    assert china.label(3, "zh_CN") is None
    assert china.is_shown_for("zh_TW") and not china.is_shown_for("en_US")


def test_colors():
    """一级红、二级橙，无等级为 None / Class colours."""
    assert china.color(1) == "#ef4444"
    assert china.color(2) == "#f59e0b"
    assert china.color(None) is None


def test_missing_db_or_table_degrades_to_empty(tmp_path):
    """
    缺库、旧库没有这张表：查询返回 None，不抛异常。
    A missing DB or table just yields no class, never an exception.
    """
    try:
        assert china.load_table(str(tmp_path / "nope.sqlite")) == {}
        assert china.get_level("中华秋沙鸭") is None
        assert china.load_table(_make_db(tmp_path / "old.sqlite", with_table=False)) == {}
        assert china.get_level("中华秋沙鸭") is None
    finally:
        china.reset_cache()


def test_real_reference_db_has_the_table():
    """
    随仓库发布的参考库里确实有这张表、且数量与 2021 名录相符（约 390 种）。
    The shipped reference DB carries the table with roughly the 2021 list's size.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "birdid", "data", "bird_reference.sqlite")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        counts = dict(con.execute(
            "SELECT level, COUNT(DISTINCT scientific_name) FROM china_protection GROUP BY level"))
        mergus = con.execute(
            "SELECT level FROM china_protection WHERE name = '中华秋沙鸭'").fetchone()
    finally:
        con.close()
    assert 80 <= counts[1] <= 100 and 280 <= counts[2] <= 320
    assert mergus == (1,)


# ---------------------------------------------------------------- 报告 / report

def _report_photo(cn, en):
    """report.db 风格的最小照片行 / minimal report.db-shaped row."""
    return {
        "filename": f"{en}.NEF", "current_path": f"/tmp/{en}.NEF", "temp_jpeg_path": None,
        "has_bird": 1, "rating": 3, "picked": 0, "is_flying": 0, "focus_status": None,
        "adj_sharpness": 300.0, "adj_topiq": 5.0, "head_sharp": 300.0, "nima_score": 5.0,
        "bird_species_cn": cn, "bird_species_en": en,
        "gbif_rarity_100": 10.0, "iucn_category": "LC",
        "iso": 640, "shutter_speed": "1/2000", "aperture": "5.6",
        "focal_length_35mm": 600, "camera_model": "NIKON Z 9", "lens_model": "Z 600",
        "date_time_original": "2026-10-31 06:12:00", "burst_id": None, "burst_position": None,
    }


def test_report_badge_only_in_chinese(china_db):
    """
    报告：鸟种块带上等级；中文报告画「国家一级」徽标，英文报告不画。
    The species block carries the class; only Chinese reports draw the badge.
    """
    from core.report_export import aggregate, build_html

    data = aggregate([_report_photo("中华秋沙鸭", "Scaly-sided Merganser"),
                      _report_photo("白头鹎", "Light-vented Bulbul")])
    levels = {b.name_cn: b.china_level for b in data.species}
    assert levels == {"中华秋沙鸭": 1, "白头鹎": None}

    zh = build_html(data, {}, is_zh=True)
    en = build_html(data, {}, is_zh=False)
    assert '<span class="cnprot">国家一级</span>' in zh
    assert 'class="cnprot"' not in en
    assert zh.count('class="cnprot"') == 1


# ---------------------------------------------------------------- 界面 / UI

@pytest.fixture
def qapp():
    """共享 QApplication / shared QApplication."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _detail_panel(lang):
    """按指定语言构造详情面板 / build a DetailPanel in the given language."""
    from tools.i18n import get_i18n
    from ui.detail_panel import DetailPanel
    i18n = get_i18n()
    i18n.switch_language(lang)
    return DetailPanel(i18n)


def _photo(cn, en):
    return {"filename": "a.NEF", "bird_species_cn": cn, "bird_species_en": en,
            "rating": 3, "has_bird": 1}


def test_detail_panel_row_in_chinese(qapp, china_db):
    """
    中文界面：国家保护行可见、显示等级；换到不在名录的鸟种时回到「—」，不残留。
    Chinese UI: row visible with the class; an unlisted species resets to "—".
    """
    panel = _detail_panel("zh_CN")
    try:
        keys = [k for k, _ in panel._meta_rows]
        assert keys.index("browser.meta_iucn") + 1 == keys.index("browser.meta_china_protection")
        panel.set_current_photo(_photo("红隼", "Common Kestrel"))
        assert panel._meta_form.isRowVisible(panel._val_china)
        assert panel._val_china.text() == "国家二级"
        panel.set_current_photo(_photo("白头鹎", "Light-vented Bulbul"))
        assert panel._val_china.text() == "—"
    finally:
        panel.close()


def test_detail_panel_row_hidden_in_english(qapp, china_db):
    """英文界面：整行隐藏 / English UI hides the whole row."""
    panel = _detail_panel("en_US")
    try:
        panel.set_current_photo(_photo("红隼", "Common Kestrel"))
        assert not panel._meta_form.isRowVisible(panel._val_china)
    finally:
        panel.close()


def test_birdname_search_row(qapp, china_db):
    """
    鸟名查询：中文界面建行并按学名查到等级；英文界面不建这一行。
    Bird-name search: the row exists only in Chinese UIs and resolves by Latin name.
    """
    from tools.i18n import get_i18n
    from ui.birdname_search_widget import BirdNameSearchWidget

    get_i18n().switch_language("zh_CN")
    w = BirdNameSearchWidget()
    try:
        w._show_detail({"chinese_name": "中华秋沙鸭", "english_name": "Scaly-sided Merganser",
                        "latin_name": "Mergus squamatus"})
        assert w.detail_china_label.text() == "国家一级"
        w._show_detail({"chinese_name": "白头鹎", "english_name": "Light-vented Bulbul",
                        "latin_name": "Pycnonotus sinensis"})
        assert w.detail_china_label.text() == "—"
    finally:
        w.close()

    get_i18n().switch_language("en_US")
    w = BirdNameSearchWidget()
    try:
        assert w.detail_china_label is None
        w._show_detail({"chinese_name": "中华秋沙鸭", "english_name": "Scaly-sided Merganser",
                        "latin_name": "Mergus squamatus"})
    finally:
        w.close()


def _crop_studio_pill(lang, cn, en):
    """
    构造后期工作区并取顶栏的国家保护 pill 文字（没有则为 None）。
    Build a CropStudio and return the China-protection pill text, or None.
    """
    from PySide6.QtWidgets import QApplication, QLabel
    from tools.i18n import get_i18n
    from ui.crop_studio import CropStudio

    i18n = get_i18n()
    i18n.switch_language(lang)
    w = CropStudio({"filename": "a.NEF", "current_path": "/nonexist/a.NEF",
                    "bird_species_cn": cn, "bird_species_en": en, "rating": 3}, i18n)
    try:
        pill = w.findChild(QLabel, "cropStudioChinaProtection")
        return pill.text() if pill is not None else None
    finally:
        # 等后台线程结束再关，避免 QThread 被回收时仍在运行
        # Join the worker before closing so no QThread is destroyed mid-run
        w._worker.wait(5000)
        QApplication.processEvents()
        w.close()


def test_crop_studio_pill(qapp, china_db):
    """
    后期工作区顶栏：中文界面且在名录里才有 pill；繁体用台湾用字。
    Crop studio: pill only in Chinese UIs for listed species; zh_TW uses Traditional.
    """
    assert _crop_studio_pill("zh_CN", "中华秋沙鸭", "Scaly-sided Merganser") == "国家一级"
    assert _crop_studio_pill("zh_TW", "红隼", "Common Kestrel") == "國家二級"
    assert _crop_studio_pill("zh_CN", "白头鹎", "Light-vented Bulbul") is None
    assert _crop_studio_pill("en_US", "中华秋沙鸭", "Scaly-sided Merganser") is None
