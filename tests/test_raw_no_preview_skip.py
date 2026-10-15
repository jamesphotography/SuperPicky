# -*- coding: utf-8 -*-
"""
#125 回归测试：取不到预览图的 RAW 只跳过这一张，不能让整批处理中止。

raw_to_jpeg 在 rawpy 与 ExifTool 回退都失败时返回 None（例如 Topaz 导出的
线性 DNG 没有内嵌预览）。_convert_raws 此前把 None 当作「转换成功」，随后
os.path.relpath(None, ...) 抛 TypeError，整批处理报错中止。

#125 regression: a RAW with no extractable preview must be skipped on its own;
it used to be treated as success and os.path.relpath(None) aborted the run.
"""

import os
from typing import List, Tuple

from core.photo_processor import PhotoProcessor, ProcessingCallbacks, ProcessingSettings
from tools.i18n import get_i18n


def _make_processor(tmp_path) -> Tuple[PhotoProcessor, List[Tuple[str, str]]]:
    """
    构造指向临时目录的最小 PhotoProcessor，并捕获日志。

    Build a minimal PhotoProcessor pointed at a temp dir, capturing logs.
    """
    logs: List[Tuple[str, str]] = []
    callbacks = ProcessingCallbacks(log=lambda msg, level="info": logs.append((level, msg)))
    return PhotoProcessor(str(tmp_path), ProcessingSettings(), callbacks), logs


def test_raw_without_preview_is_skipped_not_fatal(tmp_path, monkeypatch):
    """
    一半 RAW 取不到预览：不抛异常，其余照片照常进入待处理列表。

    Half the RAWs yield no preview: no exception, the rest are still queued.
    """
    preview = tmp_path / ".superpicky" / "cache" / "temp_preview"
    preview.mkdir(parents=True)

    def fake_raw_to_jpeg(raw_path: str):
        """DNG 返回 None（无预览），其余返回缓存 JPEG 路径。/ DNG -> None."""
        if raw_path.endswith(".dng"):
            return None
        stem = os.path.splitext(os.path.basename(raw_path))[0]
        return str(preview / f"{stem}.jpg")

    monkeypatch.setattr("core.photo_processor.raw_to_jpeg", fake_raw_to_jpeg)
    processor, logs = _make_processor(tmp_path)

    raws = [(f"IMG_{i}", str(tmp_path / f"IMG_{i}.CR3")) for i in range(3)]
    raws += [(f"_T3A{i}", str(tmp_path / f"_T3A{i}.dng")) for i in range(3)]
    files_tbr: List[str] = []
    processor._convert_raws(raws, files_tbr)

    assert sorted(files_tbr) == sorted(
        os.path.join(".superpicky", "cache", "temp_preview", f"IMG_{i}.jpg") for i in range(3)
    )
    assert len(processor.temp_converted_jpegs) == 3
    # 界面能看到一条汇总警告，列出被跳过的文件 / one UI-visible summary warning
    warnings = [msg for level, msg in logs if level == "warning"]
    assert len(warnings) == 1
    assert all(f"_T3A{i}.dng" in warnings[0] for i in range(3))


def test_unsupported_raw_message_follows_ui_language(tmp_path, monkeypatch):
    """
    所有提取方式都失败时的提示跟随界面语言，英文界面不再输出中文。

    The "cannot read a preview" message follows the UI language.
    """
    import tools.find_bird_util as fbu

    messages: List[str] = []
    monkeypatch.setattr(fbu, "log_message", lambda msg, *a, **k: messages.append(msg))
    monkeypatch.setattr(fbu, "_extract_binary_via_exiftool_cli", lambda *a, **k: b"")

    i18n = get_i18n()
    original = i18n.current_lang
    try:
        i18n.switch_language("en_US")
        raw = tmp_path / "_T3A9273.dng"
        result = fbu._raw_to_jpeg_via_exiftool(str(raw), str(tmp_path / "x.jpg"), str(tmp_path))
    finally:
        i18n.switch_language(original)

    assert result is None
    final = messages[-1]
    assert "_T3A9273.dng" in final
    assert not any("一" <= ch <= "鿿" for ch in final), final
