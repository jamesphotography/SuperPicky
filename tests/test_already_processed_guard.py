# -*- coding: utf-8 -*-
"""
#161 防误覆盖回归测试：已有选片结果的目录点「开始」时不再静默全量重跑。

覆盖：
- 拦截弹窗的 取消 / 查看结果 / 重置并重选 三个分支；
- 「重置并重选」必须经二次确认（会丢失结果浏览器里的手动修改）；
- 快速复原完成后自动开始一次全量选片，且那一次跳过拦截（快速复原保留 report.db，
  不跳过就会死循环在拦截弹窗上）；标记只生效一次；
- 有文件没移回原位时不自动开始。

#161 regression tests: Start on an already-culled folder no longer re-culls
silently. Covers the guard dialog branches, the mandatory second confirmation
before "reset & re-cull", the one-shot auto-start after the quick restore
(which keeps report.db, so the guard must be skipped exactly once), and no
auto-start when some files could not be moved back.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.report_db import ReportDB  # noqa: E402


def _seed_db(directory: Path, names) -> None:
    """
    在 directory/.superpicky/report.db 写入若干条已选片记录。

    参数:
    directory (Path): 目录
    names (Iterable[str]): 照片主文件名

    Seed report.db under ``directory`` with already-culled rows.
    """
    db = ReportDB(str(directory))
    for name in names:
        db.insert_photo({
            "filename": name,
            "original_path": f"{name}.NEF",
            "current_path": f"3星_优选/{name}.NEF",
            "has_bird": 1,
            "rating": 3,
        })
    db.close()


def _scan(directory: str):
    """构造单目录预扫描结果，替代 _prescan / Fake one-folder pre-scan result."""
    from core.recursive_scanner import ScannedDirectory

    return [ScannedDirectory(path=os.path.abspath(directory), depth=0,
                             photo_count=1, video_count=0, video_files=())]


def _make_window(directory: Path):
    """
    构造主窗口并把与本测试无关的交互点全部 stub 掉。

    参数:
    directory (Path): 当前目录

    返回:
    tuple: (窗口, 启动记录, 打开结果记录, 复原记录)

    Build the main window with unrelated interaction points stubbed out.
    """
    from ui.main_window import SuperPickyMainWindow

    QApplication.instance() or QApplication([])
    w = SuperPickyMainWindow()
    w.directory_path = str(directory)
    w._require_initialization_for_processing = lambda: True
    w._is_removable_source = lambda: False
    w._confirm_start = lambda: True
    w._prescan = lambda d, warn=True: _scan(d)
    launched, opened, restored = [], [], []
    w._launch_worker = lambda *a, **kw: launched.append(a)
    w._open_results_smart = lambda: opened.append(True)
    w._quick_restore_directory = lambda: restored.append(True)
    return w, launched, opened, restored


class _ChoiceDlg:
    """替身拦截弹窗：exec() 返回预设选择 / Stand-in dialog returning a preset choice."""

    next_choice = "cancel"
    created = 0

    def __init__(self, *args, **kwargs):
        _ChoiceDlg.created += 1
        self.choice = None

    def exec(self):
        self.choice = _ChoiceDlg.next_choice
        return 0


def test_guard_blocks_silent_overwrite(tmp_path):
    """已选片目录点开始：取消/查看结果都不启动；重置并重选需二次确认。"""
    from ui import custom_dialogs as _cd
    from ui import main_window as _mw

    _seed_db(tmp_path, ["OLD1"])
    w, launched, opened, restored = _make_window(tmp_path)

    with patch.object(_cd, "DeleteConfirmDialog", _ChoiceDlg):
        _ChoiceDlg.next_choice = "cancel"
        w._start_processing()
        assert launched == [] and opened == [] and restored == []

        _ChoiceDlg.next_choice = "view"
        w._start_processing()
        assert launched == [] and opened == [True] and restored == []

        # 二次确认选「取消」：不复原、不置标记
        _ChoiceDlg.next_choice = "reprocess"
        with patch.object(_mw.StyledMessageBox, "question",
                          return_value=_mw.StyledMessageBox.No):
            w._start_processing()
        assert launched == [] and restored == []
        assert w._reprocess_after_reset is False

        # 二次确认选「重置并重选」：快速复原，并标记复原后自动重选
        with patch.object(_mw.StyledMessageBox, "question",
                          return_value=_mw.StyledMessageBox.Yes):
            w._start_processing()
        assert launched == [] and restored == [True]
        assert w._reprocess_after_reset is True


def test_reset_complete_auto_starts_full_cull_once(tmp_path):
    """复原成功：不弹完成提示、自动开始一次并跳过拦截；部分失败或普通重置不自动开始。"""
    from ui import main_window as _mw

    w, _launched, _opened, _restored = _make_window(tmp_path)
    shown, scheduled = [], []
    w._show_message = lambda *a, **kw: shown.append(a)
    w._check_report_csv = lambda *a, **kw: None

    with patch.object(_mw.QTimer, "singleShot",
                      side_effect=lambda ms, fn: scheduled.append(fn)):
        w._reprocess_after_reset = True
        w._suppress_results_browser_once = True
        w._on_reset_complete(True, {"restored": 3, "failed": 0}, None)
        assert shown == []
        assert scheduled == [w._start_processing]
        assert w._skip_processed_check_once is True
        assert w._reprocess_after_reset is False

        # 有文件没移回原位：不自动开始，照常弹提示让用户检查
        scheduled.clear()
        w._skip_processed_check_once = False
        w._reprocess_after_reset = True
        w._on_reset_complete(True, {"restored": 2, "failed": 1}, None)
        assert scheduled == [] and len(shown) == 1
        assert w._skip_processed_check_once is False

        # 普通重置（没请求重选）：照旧弹提示，不自动开始
        shown.clear()
        w._on_reset_complete(True, {"restored": 1, "failed": 0}, None)
        assert scheduled == [] and len(shown) == 1


def test_start_after_reset_skips_guard_exactly_once(tmp_path):
    """复原后那一次开始跳过拦截直接启动；标记消费后下一次照常拦截。"""
    from ui import custom_dialogs as _cd

    _seed_db(tmp_path, ["OLD1"])
    (tmp_path / "OLD1.NEF").write_bytes(b"old")
    w, launched, _opened, _restored = _make_window(tmp_path)
    w._resume_prompt_handled = False

    cfg_patch = patch("ui.main_window.get_advanced_config")
    with (
        patch.object(_cd, "DeleteConfirmDialog", _ChoiceDlg),
        patch("tools.exiftool_manager.get_exiftool_manager"),
        cfg_patch as mock_cfg,
    ):
        mock_cfg.return_value.birdid_auto_identify = False
        _ChoiceDlg.created = 0
        _ChoiceDlg.next_choice = "cancel"

        w._skip_processed_check_once = True
        w._start_processing()
        assert _ChoiceDlg.created == 0
        assert len(launched) == 1
        assert w._skip_processed_check_once is False

        w._start_processing()
        assert _ChoiceDlg.created == 1
        assert len(launched) == 1
