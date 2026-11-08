# -*- coding: utf-8 -*-
"""
结果浏览器的目录标签（实验室多目录，spec §4.4）。

多目录处理后浏览器**不合并**不同目录：顶部一排标签，每个有结果的目录一个，
点哪个就按单独打开那个目录的方式打开它（ask_batches=False，不弹批次清单）。

覆盖:
1. 三个目录两个有 report.db → 只有 2 个标签；只有 1 个 → 整排隐藏。
2. 点第二个标签 → open_directory(第二个, ask_batches=False)，该标签高亮。
3. 计数失败的目录仍有标签，文字只有目录名。
4. open_directory(ask_batches=False) 遇到多个批次直接全部载入，不弹清单；默认仍弹。
5. 主窗口 _auto_open_results：单目录先清空标签、open_directory 参数与原来相同；
   多目录先 set_folder_tabs(列表)，再以 ask_batches=False 打开第一个有结果的目录。

Folder tabs in the results browser (Labs multi-folder, spec §4.4): different
user folders are never merged; each folder with results gets a tab that opens
it exactly as if opened alone.
"""
import os
from types import SimpleNamespace
from typing import List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _make_result_folder(path: str, n_photos: int = 2) -> str:
    """
    建一个带最小真实 report.db 的已处理目录。

    参数:
    path (str): 目录路径（不存在则创建）
    n_photos (int): 写入的照片条数

    返回:
    str: 目录路径

    Create a processed folder with a minimal real report.db.
    """
    from tools.report_db import ReportDB
    os.makedirs(path, exist_ok=True)
    db = ReportDB(path)
    for i in range(n_photos):
        db.insert_photo({"filename": f"IMG_{i}", "current_path": f"IMG_{i}.JPG",
                         "original_path": f"IMG_{i}.JPG", "rating": 2, "has_bird": 1})
    db.close()
    return path


@pytest.fixture
def browser():
    """真实的空浏览器窗口 / a real, empty browser window."""
    import ui.results_browser_window as rbw
    win = rbw.ResultsBrowserWindow()
    yield win
    win.close()


@pytest.fixture
def three_folders(tmp_path) -> List[str]:
    """
    三个目录：A(3 张) 与 C(1 张) 有结果，B 没有。

    Three folders: A (3 photos) and C (1 photo) have results, B has none.
    """
    a = _make_result_folder(str(tmp_path / "鸟A"), 3)
    b = str(tmp_path / "B_empty")
    os.makedirs(b)
    c = _make_result_folder(str(tmp_path / "C"), 1)
    return [a, b, c]


def _record_open(monkeypatch, browser) -> List[Tuple[str, bool]]:
    """
    把 open_directory 换成记录器 / Replace open_directory with a recorder.
    """
    calls: List[Tuple[str, bool]] = []

    def _fake(directory: str, ask_batches: bool = True) -> None:
        calls.append((directory, ask_batches))
        browser._opened_folder = directory

    monkeypatch.setattr(browser, "open_directory", _fake)
    return calls


def test_only_folders_with_results_get_tabs(browser, three_folders):
    a, _b, c = three_folders
    browser.set_folder_tabs(three_folders)
    assert [p for p, _btn in browser._folder_tabs] == [a, c]
    assert not browser._folder_tab_area.isHidden()
    texts = [btn.text() for _p, btn in browser._folder_tabs]
    assert texts == ["鸟A · 3", "C · 1"]


def test_row_hidden_with_single_result_folder(browser, tmp_path):
    a = _make_result_folder(str(tmp_path / "A"))
    b = str(tmp_path / "B")
    os.makedirs(b)
    browser.set_folder_tabs([a, b])
    assert browser._folder_tab_area.isHidden()


def test_empty_list_hides_row(browser, three_folders):
    browser.set_folder_tabs(three_folders)
    browser.set_folder_tabs([])
    assert browser._folder_tab_area.isHidden()
    assert browser._folder_tabs == []


def test_click_second_tab_opens_without_asking(browser, three_folders, monkeypatch):
    a, _b, c = three_folders
    calls = _record_open(monkeypatch, browser)
    browser._opened_folder = a
    browser.set_folder_tabs(three_folders)
    assert browser._folder_tabs[0][1].isChecked()
    browser._folder_tabs[1][1].click()
    assert calls == [(c, False)]
    assert browser._folder_tabs[1][1].isChecked()
    assert not browser._folder_tabs[0][1].isChecked()


def test_count_failure_still_gives_basename_tab(browser, three_folders, monkeypatch):
    import tools.merged_report_db as mrd

    def _boom(dirs):
        raise RuntimeError("count failed")

    monkeypatch.setattr(mrd, "summarize_directories", _boom)
    browser.set_folder_tabs(three_folders)
    assert [btn.text() for _p, btn in browser._folder_tabs] == ["鸟A", "C"]


def test_unreadable_count_shows_basename(browser, three_folders, monkeypatch):
    """summarize 返回 photos=None（库读不出）时只显示目录名 / None count → name only."""
    import tools.merged_report_db as mrd
    real = mrd.summarize_directories

    def _partial(dirs):
        out = real(dirs)
        out[0]["photos"] = None
        return out

    monkeypatch.setattr(mrd, "summarize_directories", _partial)
    browser.set_folder_tabs(three_folders)
    assert [btn.text() for _p, btn in browser._folder_tabs] == ["鸟A", "C · 1"]


def test_open_directory_without_asking_loads_all_batches(browser, tmp_path, monkeypatch):
    parent = str(tmp_path / "trip")
    d1 = _make_result_folder(os.path.join(parent, "day1"))
    d2 = _make_result_folder(os.path.join(parent, "day2"))
    asked: list = []
    monkeypatch.setattr(browser, "_ask_which_batches",
                        lambda processed: asked.append(processed) or [])
    browser.open_directory(parent, ask_batches=False)
    assert asked == []
    assert sorted(browser._sub_dirs) == sorted([d1, d2])
    assert browser._is_merged


def test_open_directory_default_still_asks(browser, tmp_path, monkeypatch):
    parent = str(tmp_path / "trip")
    _make_result_folder(os.path.join(parent, "day1"))
    _make_result_folder(os.path.join(parent, "day2"))
    asked: list = []
    monkeypatch.setattr(browser, "_ask_which_batches",
                        lambda processed: asked.append(processed) or [])
    browser.open_directory(parent)
    assert len(asked) == 1


def test_real_tab_click_switches_folder(browser, three_folders):
    """真实切换：库换成第二个目录，标题与照片数跟上 / real switch reloads the DB."""
    a, _b, c = three_folders
    browser.open_directory(a)
    browser.set_folder_tabs(three_folders)
    assert len(browser._all_photos) == 3
    browser._folder_tabs[1][1].click()
    assert browser._directory == c
    assert len(browser._all_photos) == 1
    assert browser._folder_tabs[1][1].isChecked()


@pytest.fixture
def parent_and_plain(tmp_path) -> Tuple[str, str, List[str]]:
    """
    批次模式的父目录（day1 两张、day2 五张，自己没有 report.db）+ 一个普通已处理目录。
    A batch-mode parent (day1: 2, day2: 5, no own report.db) plus a plain processed folder.
    """
    parent = str(tmp_path / "trip")
    d1 = _make_result_folder(os.path.join(parent, "day1"), 2)
    d2 = _make_result_folder(os.path.join(parent, "day2"), 5)
    plain = _make_result_folder(str(tmp_path / "plain"), 4)
    return parent, plain, [d1, d2]


def test_parent_with_processed_subfolders_gets_summed_tab(browser, parent_and_plain, monkeypatch):
    parent, plain, _subs = parent_and_plain
    assert not os.path.exists(os.path.join(parent, ".superpicky", "report.db"))
    browser.set_folder_tabs([parent, plain])
    assert [p for p, _btn in browser._folder_tabs] == [parent, plain]
    assert [btn.text() for _p, btn in browser._folder_tabs] == ["trip · 7", "plain · 4"]
    calls = _record_open(monkeypatch, browser)
    browser._opened_folder = plain
    browser._folder_tabs[0][1].click()
    assert calls == [(parent, False)]


def test_parent_tab_falls_back_to_name_when_a_batch_count_fails(browser, parent_and_plain,
                                                                monkeypatch):
    import tools.merged_report_db as mrd
    parent, plain, subs = parent_and_plain
    real = mrd.summarize_directories

    def _partial(dirs):
        out = real(dirs)
        for e in out:
            if e["path"] == subs[0]:
                e["photos"] = None
        return out

    monkeypatch.setattr(mrd, "summarize_directories", _partial)
    browser.set_folder_tabs([parent, plain])
    assert [btn.text() for _p, btn in browser._folder_tabs] == ["trip", "plain · 4"]


def test_real_parent_tab_opens_all_its_batches(browser, parent_and_plain):
    parent, plain, subs = parent_and_plain
    browser.open_directory(plain)
    browser.set_folder_tabs([parent, plain])
    browser._folder_tabs[0][1].click()
    assert sorted(browser._sub_dirs) == sorted(subs)
    assert len(browser._all_photos) == 7
    assert browser._folder_tabs[0][1].isChecked()


class _FakeBrowser:
    """记录调用顺序的浏览器替身 / Browser stand-in recording call order."""

    def __init__(self) -> None:
        self.calls: list = []

    def set_folder_tabs(self, folders: List[str]) -> None:
        self.calls.append(("tabs", list(folders)))

    def open_directory(self, directory: str, ask_batches: bool = True) -> None:
        self.calls.append(("open", directory, ask_batches))

    def showMaximized(self) -> None:
        pass

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:
        pass


class _FakeOpenRecorder(_FakeBrowser):
    """只接受单参调用的替身：验证单目录调用方式与原来完全一致。"""

    def open_directory(self, *args, **kwargs) -> None:
        self.calls.append(("open", args, kwargs))


def _fake_main(browser, multi: bool, folders: List[str], directory: str = "") -> SimpleNamespace:
    """
    _auto_open_results 只用到这几个成员 / the members _auto_open_results touches.
    """
    return SimpleNamespace(
        _results_browser=browser,
        directory_path=directory,
        _is_multi_mode=lambda: multi,
        _multi_folders=lambda: list(folders),
        hide=lambda: None,
    )


def test_main_single_folder_clears_tabs_and_opens_as_before(tmp_path):
    from ui.main_window import SuperPickyMainWindow
    d = _make_result_folder(str(tmp_path / "single"))
    fake = _FakeOpenRecorder()
    SuperPickyMainWindow._auto_open_results(_fake_main(fake, False, [d], d))
    assert fake.calls == [("tabs", []), ("open", (d,), {})]


def test_main_multi_sets_tabs_then_opens_first_with_results(three_folders):
    from ui.main_window import SuperPickyMainWindow
    a, b, c = three_folders
    fake = _FakeBrowser()
    # 第一个没有结果：应打开第二个有结果的 / first has no results → open the next one
    order = [b, a, c]
    SuperPickyMainWindow._auto_open_results(_fake_main(fake, True, order, b))
    assert fake.calls == [("tabs", order), ("open", a, False)]


def test_main_multi_respects_explicit_path(three_folders):
    """队列收尾传入的 path 有结果时优先打开它 / an explicit path with results wins."""
    from ui.main_window import SuperPickyMainWindow
    a, b, c = three_folders
    fake = _FakeBrowser()
    SuperPickyMainWindow._auto_open_results(_fake_main(fake, True, [a, b, c]), c)
    assert fake.calls == [("tabs", [a, b, c]), ("open", c, False)]


def test_main_multi_counts_parent_with_subfolder_results(tmp_path, parent_and_plain):
    """列表首项是批次模式父目录：它算有结果，被打开 / a batch parent counts as having results."""
    from ui.main_window import SuperPickyMainWindow
    parent, plain, _subs = parent_and_plain
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    fake = _FakeBrowser()
    order = [empty, parent, plain]
    SuperPickyMainWindow._auto_open_results(_fake_main(fake, True, order))
    assert fake.calls == [("tabs", order), ("open", parent, False)]
