# -*- coding: utf-8 -*-
"""
Grid 缩略图与详情「全图」显示必须使用干净原图,绝不使用带标注的 debug 图
(yolo_debug_path 全图红框 / debug_crop_path 裁切+对焦十字头圈)。仅详情的
「裁切诊断视图」按钮才允许显示 debug_crop_path。

Grid thumbnails and the detail panel's full-image view must render the clean
photo (temp_jpeg → original JPEG) and never the annotated debug artifacts.
Only the detail panel's explicit crop-diagnostic view may show debug_crop_path.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def _touch(path: str) -> str:
    """创建一个占位文件并返回其路径。"""
    with open(path, "wb") as f:
        f.write(b"x")
    return path


def _make_photo(tmp: str, *, temp_jpeg=True, original_jpg=True) -> dict:
    """构造一条含全部 debug 路径的照片记录(路径均真实存在)。"""
    photo = {
        "filename": "bird.NEF",
        "yolo_debug_path": _touch(os.path.join(tmp, "yolo_debug.jpg")),
        "debug_crop_path": _touch(os.path.join(tmp, "crop_debug.jpg")),
    }
    if temp_jpeg:
        photo["temp_jpeg_path"] = _touch(os.path.join(tmp, "temp.jpg"))
    if original_jpg:
        photo["original_path"] = _touch(os.path.join(tmp, "bird.jpg"))
    return photo


def test_grid_candidates_exclude_debug_images():
    """Grid 候选顺序:只含 temp_jpeg → 原图,绝不含任一 debug 图。"""
    from ui.thumbnail_grid import _thumbnail_candidates

    with tempfile.TemporaryDirectory() as tmp:
        photo = _make_photo(tmp)
        cands = _thumbnail_candidates(photo)

        assert photo["temp_jpeg_path"] in cands
        assert cands[0] == photo["temp_jpeg_path"], "temp_jpeg 应为首选"
        assert photo["yolo_debug_path"] not in cands, "带框调试图不得出现"
        assert photo["debug_crop_path"] not in cands, "带标注裁切图不得出现"


def test_grid_falls_back_to_original_not_debug():
    """temp_jpeg 缺失时,Grid 回退到原图 JPEG 而非 debug 图。"""
    from ui.thumbnail_grid import _thumbnail_candidates

    with tempfile.TemporaryDirectory() as tmp:
        photo = _make_photo(tmp, temp_jpeg=False)
        cands = _thumbnail_candidates(photo)

        assert cands == [photo["original_path"]], f"应仅回退到原图,实得 {cands}"


def test_grid_derives_sibling_jpeg_when_temp_jpeg_stale():
    """temp_jpeg_path 陈旧失效时,Grid 从 current_path 推导同目录 JPG 边车。

    复现真实数据 bug:RAW 移入 burst 子目录后 temp_jpeg_path 指向旧位置(不存在),
    但配对 JPG 与 RAW 同目录同名——应据 current_path 找回它。
    """
    from ui.thumbnail_grid import _thumbnail_candidates

    with tempfile.TemporaryDirectory() as tmp:
        burst = os.path.join(tmp, "burst_035")
        os.makedirs(burst)
        nef = os.path.join(burst, "_JOW3215.NEF")
        _touch(nef)
        sibling = _touch(os.path.join(burst, "_JOW3215.jpg"))  # 配对 JPG,真实位置
        photo = {
            "filename": "_JOW3215.NEF",
            "current_path": nef,
            # 陈旧 temp_jpeg:指向上一层(整理前位置),文件已不在
            "temp_jpeg_path": os.path.join(tmp, "_JOW3215.jpg"),
        }
        cands = _thumbnail_candidates(photo)
        assert cands == [sibling], f"应据 current_path 找回同目录 JPG,实得 {cands}"


def test_detail_full_view_derives_sibling_when_temp_jpeg_stale():
    """详情·全图:temp_jpeg 陈旧时同样据 current_path 找回同目录 JPG 边车。"""
    from ui.detail_panel import DetailPanel

    with tempfile.TemporaryDirectory() as tmp:
        burst = os.path.join(tmp, "burst_001")
        os.makedirs(burst)
        nef = _touch(os.path.join(burst, "_JOW3006.NEF"))
        sibling = _touch(os.path.join(burst, "_JOW3006.jpg"))
        photo = {
            "filename": "_JOW3006.NEF",
            "current_path": nef,
            "temp_jpeg_path": os.path.join(tmp, "wrong", "_JOW3006.jpg"),
        }
        panel = DetailPanel(get_i18n())
        panel._current_photo = photo
        panel._use_crop_view = False
        assert panel._resolve_image_path() == sibling
        panel.cleanup()
        panel.deleteLater()


def test_fullscreen_hd_derives_sibling_when_temp_jpeg_stale():
    """全屏高清解析:temp_jpeg 陈旧时据 current_path 找回同目录全幅 JPG,
    避免双击后停在低清缩略图(_resolve_hd_path 不依赖 self,可直接调用)。"""
    from ui.fullscreen_viewer import FullscreenViewer

    with tempfile.TemporaryDirectory() as tmp:
        burst = os.path.join(tmp, "burst_035")
        os.makedirs(burst)
        nef = _touch(os.path.join(burst, "_JOW3215.NEF"))
        sibling = _touch(os.path.join(burst, "_JOW3215.jpg"))
        photo = {
            "filename": "_JOW3215.NEF",
            "current_path": nef,
            "temp_jpeg_path": os.path.join(tmp, "北极海鹦", "_JOW3215.jpg"),  # 陈旧
        }
        # self 未被方法体使用,传 None 以避免构造重量级查看器(含预载线程池)
        assert FullscreenViewer._resolve_hd_path(None, photo) == sibling


def test_fullscreen_hd_never_uses_debug_paths():
    """全屏高清解析绝不使用带框/带标注的 debug 图。"""
    from ui.fullscreen_viewer import FullscreenViewer

    with tempfile.TemporaryDirectory() as tmp:
        photo = {
            "filename": "x.NEF",
            "current_path": os.path.join(tmp, "x.NEF"),  # 不存在
            "yolo_debug_path": _touch(os.path.join(tmp, "yolo.jpg")),
            "debug_crop_path": _touch(os.path.join(tmp, "crop.jpg")),
        }
        assert FullscreenViewer._resolve_hd_path(None, photo) is None


def test_detail_full_view_uses_clean_image():
    """详情·全图:优先干净 temp_jpeg,不用 debug 图。"""
    from ui.detail_panel import DetailPanel

    with tempfile.TemporaryDirectory() as tmp:
        photo = _make_photo(tmp)
        panel = DetailPanel(get_i18n())
        panel._current_photo = photo
        panel._use_crop_view = False
        assert panel._resolve_image_path() == photo["temp_jpeg_path"]
        panel.cleanup()
        panel.deleteLater()


def test_detail_full_view_fallback_is_original_not_crop_debug():
    """详情·全图:temp_jpeg 缺失时回退原图,绝不回退到带标注裁切图。"""
    from ui.detail_panel import DetailPanel

    with tempfile.TemporaryDirectory() as tmp:
        photo = _make_photo(tmp, temp_jpeg=False)
        panel = DetailPanel(get_i18n())
        panel._current_photo = photo
        panel._use_crop_view = False
        resolved = panel._resolve_image_path()
        assert resolved == photo["original_path"], f"应回退原图,实得 {resolved!r}"
        assert resolved != photo["debug_crop_path"]
        panel.cleanup()
        panel.deleteLater()


def test_detail_crop_view_still_shows_diagnostic():
    """详情·裁切诊断视图:仍显示 debug_crop_path(功能保留)。"""
    from ui.detail_panel import DetailPanel

    with tempfile.TemporaryDirectory() as tmp:
        photo = _make_photo(tmp)
        panel = DetailPanel(get_i18n())
        panel._current_photo = photo
        panel._use_crop_view = True
        assert panel._resolve_image_path() == photo["debug_crop_path"]
        panel.cleanup()
        panel.deleteLater()
