# -*- coding: utf-8 -*-
"""导出透传修图选项 / export passes enhance_opts through."""
import pytest

pytest.importorskip("PySide6")
from core.enhance.options import EnhanceOptions  # noqa: E402
from ui import crop_studio  # noqa: E402


def test_export_worker_forwards_enhance_opts(monkeypatch):
    captured = {}

    def fake_export(src, box, out, *, exif_src=None, jpeg_quality=95,
                    out_size=None, enhance_opts=None):
        captured["opts"] = enhance_opts
        return out

    monkeypatch.setattr(crop_studio, "export_crop", fake_export)
    opts = EnhanceOptions(denoise_strength=0.3)
    w = crop_studio._ExportWorker("s", None, "o.jpg", None, enhance_opts=opts)
    w.run()
    assert captured["opts"] is opts
