# -*- coding: utf-8 -*-
"""
改星级后内存缓存必须同步 / The in-memory caches must follow a rating change.

对应现网缺陷（2026-09-19 发现，与改鸟种的缓存缺陷同源）：在结果浏览器里把一张
照片从 3★ 改成 1★ 后导出报告，报告的星级分布里它**仍然算作 3★**。

根因：`_on_rating_changed` 只就地改了 `_filtered_photos` 里的那一条，另外两份
缓存没动。而 HTML 报告读的是 `_all_photos`（`aggregate` 的 `by_rating` 直接数
这份列表的 rating），显示列表每次重建又从 `_raw_filtered_photos` 拷贝——于是
报告的星级统计、以及重建后网格上的星标，都会退回改之前的值。

星级是报告最显眼的一组数字（总张数 / 各星级 / 精选），错了比鸟名错更难发现：
界面上明明是 1★，报告里却记着 3★，两边都「看起来正常」。

Regression: a rating change patched only the visible list, so the exported
report's star distribution (read from `_all_photos`) kept the pre-edit rating,
and a display-list rebuild (read from `_raw_filtered_photos`) restored it on
screen as well.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """钉住 i18n 为中文：目录命名跟随全局 i18n 单例 / Pin i18n to zh_CN."""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _join_worker_threads(timeout: float = 8.0) -> None:
    """等改星级起的后台线程（写 EXIF / 移动文件）跑完 / Wait for workers."""
    import threading
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t is not threading.current_thread() and t.daemon and t.is_alive()
                 and t.name.startswith("Thread-")]
        if not alive:
            return
        time.sleep(0.02)


def _browser_with_photos(root: str, burst: bool = False):
    """
    建一个真实 ResultsBrowserWindow。

    burst=False：一张 3★ 的已整理照片；
    burst=True ：一个三张的 3★ 连拍组（burst_ 目录）。
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    if burst:
        rels = [f"白喉抚蜜鸟/3星_优选/burst_001/DSC_300{i}.NEF" for i in range(3)]
    else:
        rels = ["白喉抚蜜鸟/3星_优选/DSC_1234.NEF"]
    for rel in rels:
        abs_path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        open(abs_path, "w").close()
        db.insert_photo({
            "filename": os.path.splitext(os.path.basename(rel))[0],
            "current_path": rel, "original_path": rel,
            "bird_species_cn": "白喉抚蜜鸟",
            "bird_species_en": "White-throated Honeyeater",
            "rating": 3, "has_bird": 1,
        })
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    return win


def test_all_photos_cache_follows_a_rating_change(tmp_path, monkeypatch):
    """改完星级，`_all_photos` 里的那条必须是新星级——报告读的就是它。"""
    win = _browser_with_photos(str(tmp_path))
    try:
        photo = win._filtered_photos[0]

        win._on_rating_changed(photo, 1)

        assert [p["rating"] for p in win._all_photos] == [1], (
            "_all_photos 没跟着改星级——HTML 报告与 eBird 导出都读它"
        )
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


def test_exported_report_counts_the_new_rating(tmp_path, monkeypatch):
    """
    用户实际可见的症状：报告的星级分布必须按新星级计数。

    走报告真正的入口数据（`_all_photos` → aggregate），钉住用户看到的数字。
    """
    from core.report_export import aggregate

    win = _browser_with_photos(str(tmp_path))
    try:
        photo = win._filtered_photos[0]

        win._on_rating_changed(photo, 1)
        _join_worker_threads()

        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        by_rating = aggregate(rows, include_gps=False).by_rating

        assert by_rating[1] == 1, "报告没把这张算进 1★"
        assert by_rating[3] == 0, "报告仍把这张算作改之前的 3★"
    finally:
        _join_worker_threads()
        win.close()


def test_display_list_rebuild_keeps_the_new_rating(tmp_path, monkeypatch):
    """
    重建显示列表后，网格里仍须是新星级。

    `_update_display_list` 每次都从 `_raw_filtered_photos` 拷贝重建，用户点一下
    连拍组的展开/收起就会走到这里。
    """
    win = _browser_with_photos(str(tmp_path))
    try:
        photo = win._filtered_photos[0]
        win._on_rating_changed(photo, 1)

        win._update_display_list()

        assert [p["rating"] for p in win._filtered_photos] == [1], (
            "重建显示列表后退回了旧星级"
        )
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


def test_rating_change_does_not_spread_to_the_burst_group(tmp_path, monkeypatch):
    """
    改星级**不得**扩散到同一连拍组的其他成员。

    这是改星级与改鸟种的关键区别：改鸟种是整组一起改（同一组照片分属不同鸟种
    没有意义，core 也确实整组写库），改星级则是逐张的——组内挑出一张好的给
    3★、其余留 1★ 正是选片的日常。缓存补丁若照搬改鸟种那套按 burst_id 匹配，
    用户给一张升星会把整组一起升掉，而库里只改了一条，界面与库当场分叉。

    A rating change must stay on its own photo; only species changes are
    group-wide.
    """
    win = _browser_with_photos(str(tmp_path), burst=True)
    try:
        # 展开连拍组，才能逐张操作 / Expand the burst to address members
        burst_id = win._raw_filtered_photos[0]["burst_id"]
        assert burst_id is not None, "测试前提：这是一个连拍组"
        win._expanded_bursts.add(burst_id)
        win._update_display_list()
        assert len(win._filtered_photos) == 3

        target = win._filtered_photos[0]
        win._on_rating_changed(target, 1)

        ratings = sorted(p["rating"] for p in win._all_photos)
        assert ratings == [1, 3, 3], f"改星级扩散到了整组：{ratings}"
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()
