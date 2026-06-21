# -*- coding: utf-8 -*-
"""Crop Studio 全屏后期工作区冒烟测试(offscreen,headless 安全)。
   Smoke tests for the CropStudio fullscreen post-processing workspace.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_crop_studio_constructs():
    """构造不崩,且路径解析优先 temp_jpeg→current→original。"""
    from ui.crop_studio import CropStudio

    photo = {
        "filename": "a.NEF",
        "current_path": "/nonexist/a.NEF",
        "bird_species_cn": "红头鸲鹟",
        "rating": 3,
        "gbif_tier": 2,
    }
    w = CropStudio(photo, get_i18n())
    assert w is not None
    assert w._resolve_image_path(photo).endswith("a.NEF")
    # 等待后台线程结束并关闭,避免 QThread 在 GC 时仍运行触发 abort。
    # Join the worker and close so the QThread isn't destroyed while running.
    w._worker.wait(5000)
    _app.processEvents()
    w.close()


def test_canvas_set_image():
    """_Canvas 显示图像并 fit/actual_size 不崩。"""
    import numpy as np
    from ui.crop_studio import _Canvas

    c = _Canvas()
    c.set_image(np.zeros((60, 100, 3), "uint8"))
    c.fit()
    c.actual_size()
    c.zoom_in()
    c.zoom_out()
    assert c is not None


def _make_result():
    """构造一个含 3 个候选的 ok 结果(含原图哨兵)。"""
    import numpy as np
    from core.crop_advisor import (
        BIRD_ONLY_LABEL,
        ORIGINAL_LABEL,
        CropAdviceResult,
        CropSuggestion,
    )

    img = np.zeros((80, 120, 3), "uint8")
    sugg = [
        CropSuggestion("3:2", (10, 10, 100, 70), 0.88, img[10:70, 10:100].copy()),
        CropSuggestion(ORIGINAL_LABEL, (0, 0, 120, 80), 0.80, img.copy()),
        CropSuggestion(BIRD_ONLY_LABEL, (20, 20, 90, 60), 0.75, img[20:60, 20:90].copy()),
    ]
    return CropAdviceResult(suggestions=sugg, status="ok", bird_count=1)


def test_candidates_populate_and_select():
    """注入 ok 结果应渲染等量候选格,默认选中索引 0,原图候选选中后 box=None。"""
    from ui.crop_studio import CropStudio

    photo = {"filename": "c.NEF", "current_path": "/nonexist/c.NEF"}
    w = CropStudio(photo, get_i18n())
    w._worker.wait(5000)
    _app.processEvents()

    result = _make_result()
    w._on_advice(result)
    assert len(w._cells) == 3
    assert w._selected_index == 0
    assert w._current_box == (10, 10, 100, 70)  # 索引0非原图,记其框

    w._select_candidate(1)  # 原图候选
    assert w._current_box is None
    assert w._analysis_size == (120, 80)
    w.close()


def test_advice_no_bird_shows_hint():
    """no_bird 结果应显示提示文案、不建候选格。"""
    from core.crop_advisor import CropAdviceResult
    from ui.crop_studio import CropStudio

    photo = {"filename": "d.NEF", "current_path": "/nonexist/d.NEF"}
    w = CropStudio(photo, get_i18n())
    w._worker.wait(5000)
    _app.processEvents()

    w._on_advice(CropAdviceResult(status="no_bird", bird_count=0))
    assert len(w._cells) == 0
    assert w._cand_hint.isVisibleTo(w) or w._cand_hint.text()
    w.close()


def test_top_bar_shows_filename_and_stars():
    """顶栏含文件名文本,星级 pixmap 非空(rating>=1)。"""
    from ui.crop_studio import CropStudio

    photo = {
        "filename": "Z9W1.NEF",
        "current_path": "/nonexist/Z9W1.NEF",
        "bird_species_cn": "红头鸲鹟",
        "bird_species_en": "Red-headed robin",
        "rating": 4,
        "gbif_rarity_100": 60.0,
        "iucn_category": "LC",
    }
    w = CropStudio(photo, get_i18n())
    w._worker.wait(5000)
    _app.processEvents()

    # 文件名 label 文本
    fn = w.findChild(type(w._star_label), "cropStudioFilename")
    assert fn is not None and "Z9W1.NEF" in fn.text()
    # 星级 pixmap 非空
    pm = w._star_label.pixmap()
    assert pm is not None and not pm.isNull()
    w.close()


def test_set_mode_toggles_and_emits():
    """模式切换不崩;鸟种/删除按钮发出对应信号。"""
    from ui.crop_studio import CropStudio

    photo = {"filename": "e.NEF", "current_path": "/nonexist/e.NEF", "rating": 2}
    w = CropStudio(photo, get_i18n())
    w._worker.wait(5000)
    _app.processEvents()

    got = {}
    w.edit_species_requested.connect(lambda p: got.setdefault("species", p))
    w.delete_requested.connect(lambda p: got.setdefault("delete", p))
    w._btn_species.click()
    w._btn_delete.click()
    assert "species" in got and "delete" in got

    w._set_mode("manual")
    assert w._mode == "manual"
    w._set_mode("manual")  # 再次点击回到 crop
    assert w._mode == "crop"
    w._set_mode("auto")
    assert w._mode == "auto"
    w.close()


def test_manual_crop_maps_and_saves():
    """手动模式:框选映射到分析图坐标、记为当前框,并可存为候选。"""
    import numpy as np
    from PySide6.QtCore import QRect
    from ui.crop_studio import CropStudio

    photo = {"filename": "m.NEF", "current_path": "/nonexist/m.NEF"}
    w = CropStudio(photo, get_i18n())
    w._worker.wait(5000)
    _app.processEvents()

    # 注入分析图与离线打分函数,避免触发真实 TOPIQ 模型
    w._analysis_bgr = np.zeros((80, 120, 3), "uint8")
    w._analysis_size = (120, 80)
    w._topiq_fn = lambda crop: 0.5
    w._set_mode("manual")
    assert w._mode == "manual"

    pr = w._canvas.displayed_pixmap_rect()
    assert not pr.isEmpty()
    # 在像素图矩形内框选左上 1/2 区域
    label_rect = QRect(pr.left(), pr.top(), pr.width() // 2, pr.height() // 2)
    w._on_manual_crop(label_rect)
    assert w._current_box is not None
    bx1, by1, bx2, by2 = w._current_box
    assert 0 <= bx1 < bx2 <= 120 and 0 <= by1 < by2 <= 80
    assert w._manual_save_btn.isEnabled()

    before = len(w._suggestions)
    w._save_manual_as_candidate()
    assert len(w._suggestions) == before + 1
    assert w._mode == "crop"
    assert w._selected_index == len(w._suggestions) - 1
    w.close()


def test_resolve_prefers_temp_jpeg():
    """temp_jpeg_path 存在时应优先于 current/original。"""
    from ui.crop_studio import CropStudio

    photo = {
        "filename": "b.NEF",
        "temp_jpeg_path": "/tmp/b_preview.jpg",
        "current_path": "/nonexist/b.NEF",
        "original_path": "/nonexist/b.NEF",
    }
    w = CropStudio(photo, get_i18n())
    assert w._resolve_image_path(photo).endswith("b_preview.jpg")
    w._worker.wait(5000)
    _app.processEvents()
    w.close()
