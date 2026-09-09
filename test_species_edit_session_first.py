# -*- coding: utf-8 -*-
"""
改鸟种弹窗：本次拍到的鸟种优先。

弹窗原先打开就是一片空白，非要用户先打字。而改鸟种最常见的情形是「认成了隔壁
那种」——正确的那种当天多半也拍到了。所以搜索框还空着时先把本次拍到的鸟种列
出来，多数时候一点就中，省掉一次中文输入法切换。

搜索时同理：输入「鹭」，当天拍到的那几种鹭排在前面并标注「本次」，不必在二三
十个同属鸟里找。

关键约束：这只能是**默认视图**，不能变成过滤。整批从头认错时（4.6.0 的「整种
改」正是为此），正确鸟种一张都没认对过，绝不在本次列表里，搜全库的路必须一直
通着。

The species picker opens on the species you actually photographed this time,
since a misidentification is usually the bird next to it — which you probably
also shot. It stays a default view, never a filter: when a whole batch is
misidentified the right species is by definition absent from that list.
"""
import os
import sqlite3
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])

# 造库用的鸟：(中文名, 英文名, 拉丁名)
_BIRDS = [
    ("白鹭", "Little Egret", "Egretta garzetta"),
    ("池鹭", "Chinese Pond Heron", "Ardeola bacchus"),
    ("黄嘴白鹭", "Chinese Egret", "Egretta eulophotes"),
    ("中白鹭", "Intermediate Egret", "Ardea intermedia"),
    ("大白鹭", "Great Egret", "Ardea alba"),
    ("粉顶果鸠", "Rose-crowned Fruit Dove", "Ptilinopus regina"),
    ("棕胸金鹃", "Little Bronze Cuckoo", "Chrysococcyx minutillus"),
]


@pytest.fixture
def birdname_db(tmp_path, monkeypatch):
    """造一份最小 birdname.db 并让弹窗用它。"""
    path = str(tmp_path / "birdname.db")
    con = sqlite3.connect(path)
    # created_at 是真库里就有的列，_get_latest_version_id 按它排序取最新版本
    con.execute("CREATE TABLE versions (version_id INTEGER PRIMARY KEY,"
                " created_at TEXT)")
    con.execute("INSERT INTO versions VALUES (1, '2026-01-01')")
    con.execute(
        "CREATE TABLE birds (bird_id INTEGER PRIMARY KEY, chinese_name TEXT,"
        " english_name TEXT, latin_name TEXT, pinyin_name TEXT,"
        " abbreviation TEXT, version_id INTEGER)")
    for i, (cn, en, latin) in enumerate(_BIRDS, 1):
        con.execute("INSERT INTO birds VALUES (?,?,?,?,?,?,1)",
                    (i, cn, en, latin, "", ""))
    con.commit()
    con.close()

    import ui.bird_species_edit_dialog as bsed
    monkeypatch.setattr(bsed, "_get_birdname_db_path", lambda: path)
    return path


def _dialog(birdname_db, session_species=None, exclude=None):
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    return BirdSpeciesEditDialog(parent=None, session_species=session_species,
                                 exclude_species=exclude)


def _listed(dlg):
    """当前列表里的中文名，按显示顺序。"""
    return [c.bird_data["chinese_name"] for c in dlg._cards]


# ── 默认视图 / The opening view ─────────────────────────────────────────────

def test_opens_on_the_species_shot_this_time(birdname_db):
    """
    刚打开、还没打字，就该列出本次拍到的鸟种 —— 原先这里是一片空白。
    """
    dlg = _dialog(birdname_db, [("粉顶果鸠", 532), ("棕胸金鹃", 106)])
    try:
        assert _listed(dlg) == ["粉顶果鸠", "棕胸金鹃"]
    finally:
        dlg.deleteLater()


def test_session_list_keeps_the_given_order(birdname_db):
    """
    保持调用方给的顺序（张数降序）—— 拍得最多的最可能是要选的那种，
    重新按名字排会把它埋到中间。
    """
    dlg = _dialog(birdname_db, [("池鹭", 22), ("白鹭", 300), ("大白鹭", 1)])
    try:
        assert _listed(dlg) == ["池鹭", "白鹭", "大白鹭"]
    finally:
        dlg.deleteLater()


def test_species_being_changed_is_left_out(birdname_db):
    """
    正在改的那一种不列 —— 把 X 改成 X 毫无意义，点了还会白跑一趟整批移动。
    """
    dlg = _dialog(birdname_db, [("白鹭", 30), ("池鹭", 22)], exclude=["白鹭"])
    try:
        assert _listed(dlg) == ["池鹭"]
    finally:
        dlg.deleteLater()


def test_unknown_session_species_are_skipped(birdname_db):
    """
    本次鸟种在鸟名库里查不到时跳过它，其余照常列出 —— 不能因为一个查不到的
    名字就让整个列表空掉，更不能写入一个库里不存在的鸟名。
    """
    dlg = _dialog(birdname_db, [("查无此鸟", 5), ("白鹭", 30)])
    try:
        assert _listed(dlg) == ["白鹭"]
    finally:
        dlg.deleteLater()


def test_without_session_species_it_opens_empty_as_before(birdname_db):
    """不传本次鸟种时保持原样（空列表 + 提示语），老调用点不受影响。"""
    dlg = _dialog(birdname_db, None)
    try:
        assert _listed(dlg) == []
    finally:
        dlg.deleteLater()


# ── 搜索 / Searching ────────────────────────────────────────────────────────

def test_search_puts_this_shoot_first(birdname_db):
    """
    搜「鹭」时，本次拍到的鹭排在前面 —— 否则用户还是要在同属的一堆鸟里挑。
    """
    dlg = _dialog(birdname_db, [("白鹭", 30), ("池鹭", 22)])
    try:
        dlg._search("鹭")
        names = _listed(dlg)

        assert set(names[:2]) == {"白鹭", "池鹭"}, f"本次的两种应排最前：{names}"
        assert {"中白鹭", "大白鹭", "黄嘴白鹭"} <= set(names), \
            f"全库结果不能被过滤掉：{names}"
    finally:
        dlg.deleteLater()


def test_search_still_reaches_species_not_shot_this_time(birdname_db):
    """
    整批从头认错时，正确鸟种一张都没拍「对」过 —— 它必须仍能被搜到选中。
    这是本功能不能做成过滤的原因。
    """
    dlg = _dialog(birdname_db, [("粉顶果鸠", 532)])
    try:
        dlg._search("大白鹭")

        assert "大白鹭" in _listed(dlg)
    finally:
        dlg.deleteLater()


def test_clearing_the_box_returns_to_the_session_list(birdname_db):
    """把搜索框清空，回到本次鸟种列表，而不是回到空白。"""
    dlg = _dialog(birdname_db, [("白鹭", 30)])
    try:
        dlg._search("果鸠")
        assert _listed(dlg) == ["粉顶果鸠"]

        dlg._on_text_changed("")

        assert _listed(dlg) == ["白鹭"]
    finally:
        dlg.deleteLater()


# ── 标记 / The badge ────────────────────────────────────────────────────────

def test_session_species_are_marked_in_search_results(birdname_db):
    """搜索结果里本次拍到的那几种带标记，否则排在前面也看不出为什么。"""
    dlg = _dialog(birdname_db, [("白鹭", 30)])
    try:
        dlg._search("鹭")
        badges = {c.bird_data["chinese_name"]: c.badge_text() for c in dlg._cards}

        assert badges["白鹭"], "本次拍到的应有标记"
        assert not badges["大白鹭"], "没拍到的不该有标记"
    finally:
        dlg.deleteLater()


def test_session_view_shows_photo_counts(birdname_db):
    """默认视图里每种标出张数，用户据此判断哪个才是他要改成的那种。"""
    dlg = _dialog(birdname_db, [("白鹭", 30)])
    try:
        assert "30" in dlg._cards[0].badge_text()
    finally:
        dlg.deleteLater()


# ── 调用方怎么算「本次鸟种」/ How the browser builds the list ────────────────

def _session_of(photos):
    from ui.results_browser_window import ResultsBrowserWindow

    class _Win:
        _all_photos = photos

    return ResultsBrowserWindow._session_species(_Win())


def test_session_species_are_counted_and_sorted_by_count():
    """按张数降序 —— 拍得最多的最可能是要改成的那种，放最上面。"""
    photos = ([{"bird_species_cn": "白鹭"}] * 3
              + [{"bird_species_cn": "池鹭"}] * 7
              + [{"bird_species_cn": "大白鹭"}])

    assert _session_of(photos) == [("池鹭", 7), ("白鹭", 3), ("大白鹭", 1)]


def test_unidentified_photos_do_not_become_a_species():
    """没识别出鸟种的照片不产生条目（空串、None 都不算）。"""
    photos = [{"bird_species_cn": "白鹭"}, {"bird_species_cn": ""},
              {"bird_species_cn": None}, {}]

    assert _session_of(photos) == [("白鹭", 1)]


def test_ties_are_ordered_by_name_so_the_list_is_stable():
    """
    张数相同的按名字排 —— 否则同一批照片每次打开弹窗顺序都可能不一样，
    用户刚记住的位置就没了。

    「按名字」是按字符序（中文即 Unicode 码点，不是拼音）；这里只要求它稳定
    且与输入顺序无关，具体谁在前不重要。
    """
    a = _session_of([{"bird_species_cn": "池鹭"}, {"bird_species_cn": "白鹭"}])
    b = _session_of([{"bird_species_cn": "白鹭"}, {"bird_species_cn": "池鹭"}])

    assert a == b, f"顺序应与输入无关：{a} vs {b}"
    assert [name for name, _ in a] == sorted(["白鹭", "池鹭"])


def test_session_species_survive_the_result_limit(tmp_path, monkeypatch):
    """
    本次鸟种必须挤进搜索结果，哪怕库里同名词的鸟远超返回上限。

    搜索 SQL 有 LIMIT 50。搜「莺」这类宽泛的字，库里有几百种，本次拍到的那几
    种若排在第 50 名之后就压根不会被取回来——置顶也就无从谈起，用户搜一个宽
    泛的词反而更找不到自己刚拍的鸟。所以优先级必须写进 SQL 的排序里，不能只
    在取回结果之后重排。

    The result limit must not drop this shoot's species: with hundreds of
    matches, sorting after the fetch is too late — the priority has to live in
    the query's ORDER BY.
    """
    path = str(tmp_path / "many.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE versions (version_id INTEGER PRIMARY KEY,"
                " created_at TEXT)")
    con.execute("INSERT INTO versions VALUES (1, '2026-01-01')")
    con.execute(
        "CREATE TABLE birds (bird_id INTEGER PRIMARY KEY, chinese_name TEXT,"
        " english_name TEXT, latin_name TEXT, pinyin_name TEXT,"
        " abbreviation TEXT, version_id INTEGER)")
    # 120 种「…莺」，本次拍到的那种名字排在很后面
    for i in range(120):
        con.execute("INSERT INTO birds VALUES (?,?,?,?,?,?,1)",
                    (i + 1, f"{i:03d}号莺", f"Warbler {i}", f"Genus sp{i}", "", ""))
    con.execute("INSERT INTO birds VALUES (999,'紫冠细尾鹩莺','Purple-crowned"
                " Fairywren','Malurus coronatus','','',1)")
    con.commit()
    con.close()

    import ui.bird_species_edit_dialog as bsed
    monkeypatch.setattr(bsed, "_get_birdname_db_path", lambda: path)
    dlg = bsed.BirdSpeciesEditDialog(
        parent=None, session_species=[("紫冠细尾鹩莺", 562)])
    try:
        dlg._search("莺")
        names = _listed(dlg)

        assert "紫冠细尾鹩莺" in names, \
            f"本次鸟种被结果上限挤掉了（共 {len(names)} 行）"
        assert names[0] == "紫冠细尾鹩莺", f"且应排在最前：{names[:3]}"
    finally:
        dlg.deleteLater()
