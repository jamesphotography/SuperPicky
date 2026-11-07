# -*- coding: utf-8 -*-
"""
全屏 100% 检视的全尺寸补解:RAW + 小尺寸相机 JPEG 拍摄时,改取 RAW 内嵌的大图。

此前补解只看显示图尺寸:相机 JPEG 是 S 档(如 Z9 的 2688×1792)时被判为「已是
全分辨率」直接跳过,放大到 100% 就是把小图拉大,发糊。

Full-res top-up for 100% zoom: with RAW + small camera JPEG shooting, use the
RAW's large embedded JPEG. The old check only looked at the shown image, so a
2688x1792 JPEG S counted as "already full-res" and 100% zoom was blurry.

用假的 rawpy 模块,不依赖真实 RAW 文件。
Uses a fake rawpy module; no real RAW files needed.
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from ui import fullscreen_viewer as fv  # noqa: E402


def _jpeg_bytes(w: int, h: int) -> bytes:
    """生成指定尺寸的 JPEG 字节 / make JPEG bytes of the given size."""
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(120, 80, 40))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG")
    return bytes(ba)


@pytest.fixture
def fake_rawpy(monkeypatch):
    """
    注入假 rawpy:imread 返回可设定内嵌 JPEG 尺寸与方向码的对象。
    Inject a fake rawpy whose embedded JPEG size and flip are configurable.
    """
    state = {"size": (600, 400), "flip": 0, "fmt": "JPEG"}
    mod = types.ModuleType("rawpy")

    class ThumbFormat:
        JPEG = "JPEG"
        BITMAP = "BITMAP"

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def sizes(self):
            return types.SimpleNamespace(flip=state["flip"])

        def extract_thumb(self):
            return types.SimpleNamespace(format=state["fmt"], data=_jpeg_bytes(*state["size"]))

    mod.ThumbFormat = ThumbFormat
    mod.imread = lambda path: _Raw()
    monkeypatch.setitem(sys.modules, "rawpy", mod)
    return state


def test_larger_embedded_preview_is_used(fake_rawpy):
    fake_rawpy["size"] = (600, 400)
    img = fv._decode_raw_embedded("x.NEF", (300, 200))
    assert (img.width(), img.height()) == (600, 400)


def test_not_larger_embedded_preview_is_skipped(fake_rawpy):
    fake_rawpy["size"] = (600, 400)
    assert fv._decode_raw_embedded("x.NEF", (600, 400)).isNull()


def test_portrait_raw_is_rotated_by_flip(fake_rawpy):
    """内嵌 JPEG 无方向信息、RAW 记为竖拍(flip=6)时转正后再比较。"""
    fake_rawpy["size"], fake_rawpy["flip"] = (600, 400), 6
    img = fv._decode_raw_embedded("x.NEF", (200, 300))
    assert (img.width(), img.height()) == (400, 600)


def test_aspect_mismatch_keeps_shown_image(fake_rawpy):
    """方向对不上就放弃替换,绝不显示歪图。/ Never swap in a mis-oriented image."""
    fake_rawpy["size"], fake_rawpy["flip"] = (600, 400), 0
    assert fv._decode_raw_embedded("x.NEF", (200, 300)).isNull()


def test_bitmap_thumb_is_skipped(fake_rawpy):
    fake_rawpy["fmt"] = "BITMAP"
    assert fv._decode_raw_embedded("x.NEF", (300, 200)).isNull()


def test_raw_open_failure_returns_null(monkeypatch):
    mod = types.ModuleType("rawpy")

    def _boom(path):
        raise OSError("unsupported")

    mod.imread = _boom
    monkeypatch.setitem(sys.modules, "rawpy", mod)
    assert fv._decode_raw_embedded("x.NEF", (300, 200)).isNull()


def test_raw_resolved_only_for_camera_jpeg(tmp_path):
    raw = tmp_path / "_Z9W0001.NEF"
    raw.write_bytes(b"raw")
    photo = {"current_path": str(raw)}
    cam_jpg = str(tmp_path / "_Z9W0001.jpg")
    cache_jpg = str(tmp_path / ".superpicky" / "cache" / "temp_preview" / "_Z9W0001.jpg")
    resolve = fv.FullscreenViewer._resolve_raw_for_topup
    assert resolve(photo, cam_jpg) == str(raw)
    # 缓存预览本就取自 RAW 内嵌图 / cached preview already came from the RAW
    assert resolve(photo, cache_jpg) is None
    # 纯 JPEG 照片没有 RAW / JPEG-only photo has no RAW
    assert resolve({"current_path": cam_jpg}, cam_jpg) is None


def test_loader_emits_embedded_image(fake_rawpy, tmp_path):
    shown = tmp_path / "a.jpg"
    shown.write_bytes(_jpeg_bytes(300, 200))
    fake_rawpy["size"] = (600, 400)
    got = []
    loader = fv._ImageLoader(str(shown), None, raw_path="x.NEF", shown_size=(300, 200))
    loader.ready.connect(lambda img: got.append((img.width(), img.height())))
    loader.run()
    assert got == [(600, 400)]


def test_loader_small_shown_without_gain_emits_nothing(fake_rawpy, tmp_path):
    """内嵌图不更大且显示图在封顶内:不做多余的全解。"""
    shown = tmp_path / "a.jpg"
    shown.write_bytes(_jpeg_bytes(300, 200))
    fake_rawpy["size"] = (300, 200)
    got = []
    loader = fv._ImageLoader(str(shown), None, raw_path="x.NEF", shown_size=(300, 200))
    loader.ready.connect(lambda img: got.append(img))
    loader.run()
    assert got == []
