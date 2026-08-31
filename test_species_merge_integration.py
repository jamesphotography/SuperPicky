# -*- coding: utf-8 -*-
"""
整种合并的端到端集成测试：真实 ReportDB + 真实文件，只替换掉三处交互
（鸟种搜索弹窗、确认框、结果框）和 folder_layout 配置读取。

覆盖接线正确性——这是纯函数单测看不到的部分：_all_photos 存的是相对路径
必须先解析、db_key 要和 DB 的主键对得上、合并后要重新读库刷新界面。

End-to-end test with a real ReportDB and real files; only the dialogs and
the layout config are stubbed.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """钉住中文：断言里是中文星等目录名。Pin zh_CN for folder-name assertions."""
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


class _FakeEditDialog:
    """替身鸟种搜索弹窗：直接返回选定的「中白鹭」。Stub species picker."""

    selected_cn = "中白鹭"
    selected_en = "Intermediate Egret"
    selected_latin = "Ardea intermedia"

    def __init__(self, parent=None):
        pass

    def exec(self):
        return QDialog.Accepted


class _LayoutOverrideConfig:
    """
    只覆盖 folder_layout，其余属性透传真实配置（只读，不写回）。

    不能整个替换成空壳：filter_panel 等控件构造时也会读配置。

    Override only folder_layout; delegate everything else to the real config.
    """

    folder_layout = "species-first"

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _build_browser(root: str, monkeypatch):
    """建好磁盘目录 + DB，返回加载完毕的浏览器窗口和结果框收到的文本。"""
    from tools.report_db import ReportDB
    import ui.bird_species_edit_dialog as edit_mod
    import ui.custom_dialogs as dialogs_mod
    import advanced_config as cfg_mod
    import ui.results_browser_window as rbw

    _touch(os.path.join(root, "白鹭", "3星_优选", "A.NEF"))
    _touch(os.path.join(root, "白鹭", "2星_良好", "B.NEF"))
    _touch(os.path.join(root, "苍鹭", "3星_优选", "C.NEF"))

    db = ReportDB(root)
    for name, rel, rating, cn, en in (
        ("A.NEF", os.path.join("白鹭", "3星_优选", "A.NEF"), 3, "白鹭", "Little Egret"),
        ("B.NEF", os.path.join("白鹭", "2星_良好", "B.NEF"), 2, "白鹭", "Little Egret"),
        ("C.NEF", os.path.join("苍鹭", "3星_优选", "C.NEF"), 3, "苍鹭", "Grey Heron"),
    ):
        db.insert_photo({
            "filename": name, "current_path": rel, "rating": rating,
            "has_bird": 1, "bird_species_cn": cn, "bird_species_en": en,
        })
    db.close() if hasattr(db, "close") else None

    reported: list = []
    monkeypatch.setattr(edit_mod, "BirdSpeciesEditDialog", _FakeEditDialog)
    _real_cfg = cfg_mod.get_advanced_config()
    monkeypatch.setattr(
        cfg_mod, "get_advanced_config", lambda: _LayoutOverrideConfig(_real_cfg)
    )
    monkeypatch.setattr(
        dialogs_mod.StyledMessageBox, "question",
        staticmethod(lambda *a, **k: dialogs_mod.StyledMessageBox.Yes),
    )
    monkeypatch.setattr(
        dialogs_mod.StyledMessageBox, "information",
        staticmethod(lambda parent, title, message, *a, **k: reported.append(message)),
    )

    window = rbw.ResultsBrowserWindow()
    window._load_single(root)
    return window, reported


def test_merge_moves_every_photo_of_species_and_reports(tmp_path, monkeypatch):
    """整种合并：同种两张全部搬到新鸟种目录，别的鸟种不受影响，并给出报告。"""
    root = str(tmp_path)
    window, reported = _build_browser(root, monkeypatch)
    try:
        egret = next(
            window._resolve_photo_paths(p) for p in window._all_photos
            if p["filename"] == "A.NEF"
        )

        window._on_merge_species_requested(egret)

        # 文件真的搬了
        assert os.path.exists(os.path.join(root, "中白鹭", "3星_优选", "A.NEF"))
        assert os.path.exists(os.path.join(root, "中白鹭", "2星_良好", "B.NEF"))
        assert not os.path.isdir(os.path.join(root, "白鹭"))
        # 别的鸟种没被牵连
        assert os.path.exists(os.path.join(root, "苍鹭", "3星_优选", "C.NEF"))

        # DB 更新，且 current_path 跟着走
        by_name = {p["filename"]: p for p in window._db.get_all_photos()}
        assert by_name["A.NEF"]["bird_species_cn"] == "中白鹭"
        assert by_name["B.NEF"]["bird_species_cn"] == "中白鹭"
        assert by_name["C.NEF"]["bird_species_cn"] == "苍鹭"
        assert by_name["A.NEF"]["current_path"] == os.path.join("中白鹭", "3星_优选", "A.NEF")

        # 结果报告说了搬了 2 张
        assert reported and "2" in reported[0]
    finally:
        window.close()
