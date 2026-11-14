# -*- coding: utf-8 -*-
"""
竖拍照片在全屏里要保持竖向(#164)/ Portrait photos stay upright in fullscreen (#164).

现象(Al,4.6.5 RC4,CR3):全屏打开竖拍照片,先短暂竖着显示,随即变成横躺;
切到「特写」正常,切回又横了。

根因:全屏先显示封顶分辨率图(QImageReader + setAutoTransform,方向正确),停留
0.25 秒后做全分辨率补解,那一步用的是 QImage(path)——PySide6 的 QImage(path)
不应用 EXIF 方向。CR3 内嵌预览长边超过封顶值 3200,必走补解,于是必横。

Al saw portrait CR3s flip to landscape a moment after opening in fullscreen:
the capped first decode honoured EXIF orientation, the full-res top-up used
QImage(path), which does not.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from ui import fullscreen_viewer as fv  # noqa: E402


def _portrait_jpeg(path: str, stored=(600, 400), orientation: int = 6) -> str:
    """
    写一张「存储为横、EXIF 方向 6(需顺时针转 90°)」的 JPEG——相机竖拍 JPEG 的典型存法。
    Write a JPEG stored landscape with EXIF orientation 6, like a camera portrait shot.
    """
    exif = Image.Exif()
    exif[0x0112] = orientation
    Image.new("RGB", stored, (120, 80, 40)).save(path, exif=exif.tobytes())
    return path


def test_qimage_path_really_ignores_orientation(tmp_path):
    """
    前提核实:PySide6 的 QImage(path) 不应用 EXIF 方向。
    若将来 Qt 改了默认行为,这条会失败,提醒重新评估(届时本修复仍然正确,只是不再必要)。
    """
    path = _portrait_jpeg(str(tmp_path / "p.jpg"))

    img = QImage(path)

    assert (img.width(), img.height()) == (600, 400)


def test_full_resolution_decode_is_upright(tmp_path):
    """max_edge=None(全分辨率)也按 EXIF 转正。"""
    path = _portrait_jpeg(str(tmp_path / "p.jpg"))

    img = fv._decode_capped(path, None)

    assert (img.width(), img.height()) == (400, 600)


def test_capped_decode_is_upright(tmp_path):
    """封顶解码:转正且长边封顶。"""
    path = _portrait_jpeg(str(tmp_path / "p.jpg"), stored=(1200, 800))

    img = fv._decode_capped(path, 600)

    assert (img.width(), img.height()) == (400, 600)


def test_full_res_top_up_loader_emits_upright_image(tmp_path):
    """
    **#164 核心**:全屏停留后的全分辨率补解(_ImageLoader, max_edge=None)发出的图
    必须是竖的,不能把已正确显示的竖图换成横躺的全图。
    """
    path = _portrait_jpeg(str(tmp_path / "p.jpg"))
    loader = fv._ImageLoader(path)
    got = []
    loader.ready.connect(got.append)

    loader.run()                      # 同步执行线程体 / run the body synchronously

    assert len(got) == 1
    assert (got[0].width(), got[0].height()) == (400, 600)
