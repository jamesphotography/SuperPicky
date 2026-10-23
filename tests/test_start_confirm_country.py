# -*- coding: utf-8 -*-
"""
#145 开始选片确认框：可直接「更换国家…」，换完回到确认框显示新国家；
平铺布局或关闭「连拍归入文件夹」时，连拍说明不再声称会移动文件。

Regression tests for #145: the start-culling confirmation offers "Change
country…" and comes back showing the new country; the burst note no longer
claims files are moved when the layout is flat or burst folders are off.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from tools.i18n import get_i18n  # noqa: E402
from ui.custom_dialogs import StyledMessageBox  # noqa: E402
from ui.main_window import START_CONFIRM_CHANGE_COUNTRY, SuperPickyMainWindow  # noqa: E402


@pytest.fixture
def i18n():
    inst = get_i18n()
    original = inst.current_lang
    inst.switch_language("en_US")
    yield inst
    inst.switch_language(original)


# ---------- 连拍说明 / burst note ----------

def _burst(i18n, layout: str, group_folders: bool) -> str:
    fake = types.SimpleNamespace(i18n=i18n)
    cfg = types.SimpleNamespace(folder_layout=layout, burst_group_folders=group_folders, burst_min_count=4)
    return SuperPickyMainWindow._burst_note(fake, cfg)


def test_burst_note_moves_files_only_when_organizing(i18n):
    moving = _burst(i18n, "rating_first", True)
    assert moving == i18n.t("dialogs.note_burst", count=4)
    for layout, group in (("flat", True), ("rating_first", False), ("species_first", False)):
        text = _burst(i18n, layout, group)
        assert text == i18n.t("dialogs.note_burst_detect_only", count=4)
        assert text != moving


def test_detect_only_string_in_all_locales():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "locales"
    for name in ("en_US.json", "zh_CN.json", "zh_TW.json"):
        data = json.loads((root / name).read_text(encoding="utf-8"))
        assert "{count}" in data["dialogs"]["note_burst_detect_only"], name
        assert data["dialogs"]["change_country"], name


# ---------- 确认框里换国家 / change country from the confirmation ----------

def _run_confirm(i18n, monkeypatch, replies, birdid_on=True):
    """
    以假 self 跑确认流程：exec 依次返回 replies；记录每次弹框的按钮与正文、打开设置中心的次数。
    Run the confirmation with a stand-in self; exec returns `replies` in order.
    """
    shown = []
    queue = list(replies)

    def fake_exec(box):
        # 当场记下按钮文字：父窗口随函数结束释放后，弹框对象也会被删掉
        # Record button texts now: the dialog dies with its parent afterwards
        shown.append(_button_texts(box))
        return queue.pop(0)

    monkeypatch.setattr(StyledMessageBox, "exec", fake_exec)
    opened = []
    countries = iter(["Japan", "Germany", "Germany"])
    # 确认框的父窗口必须是 QWidget，用一个空白 QWidget 挂上所需属性
    # The dialog parent must be a QWidget, so attach the attributes to a bare one
    from PySide6.QtWidgets import QWidget
    fake = QWidget()
    fake.i18n = i18n
    fake._build_start_confirm_message = lambda: (f"Country: {next(countries)}", birdid_on)
    fake._open_settings_center = lambda page: opened.append(page)
    ok = SuperPickyMainWindow._confirm_start(fake)
    return ok, shown, opened


def _button_texts(box) -> list:
    from PySide6.QtWidgets import QPushButton
    return [b.text() for b in box.findChildren(QPushButton)]


def test_change_country_reopens_with_new_country(i18n, monkeypatch):
    ok, shown, opened = _run_confirm(i18n, monkeypatch, [START_CONFIRM_CHANGE_COUNTRY, StyledMessageBox.Yes])
    assert ok is True
    assert opened == ["birdid"]
    assert len(shown) == 2, "换完国家回到确认框 / back to the confirmation"
    assert i18n.t("dialogs.change_country") in shown[0]


def test_no_cancels(i18n, monkeypatch):
    ok, _, opened = _run_confirm(i18n, monkeypatch, [StyledMessageBox.No])
    assert ok is False and opened == []


def test_change_country_hidden_without_birdid(i18n, monkeypatch):
    ok, shown, _ = _run_confirm(i18n, monkeypatch, [StyledMessageBox.Yes], birdid_on=False)
    assert ok is True
    assert i18n.t("dialogs.change_country") not in shown[0]
