# -*- coding: utf-8 -*-
"""
ExifTool 标题/题注的内联退回写法必须是单行。

常驻 ExifTool 用 `-@ -` 逐行读参数，评分题注带换行；临时文件写不出来而退回内联时，
不压平换行，题注的每一行都会变成独立的参数或文件名。

The inline fallback for Title/Description must be a single line, since the
persistent ExifTool reads one argument per line.
"""
from tools.exiftool_manager import inline_text_arg


def test_multiline_caption_becomes_one_argument():
    """多行中文题注压成一行，内容保留。"""
    arg = inline_text_arg("XMP:Description", "3★ 优选\n✓ 头部锐度 612\r\n✗ 美学 4.1")
    assert "\n" not in arg and "\r" not in arg
    assert arg == "-XMP:Description=3★ 优选 ✓ 头部锐度 612 ✗ 美学 4.1"


def test_single_line_title_unchanged():
    """单行标题原样写入。"""
    assert inline_text_arg("XMP:Title", "白喉抚蜜鸟") == "-XMP:Title=白喉抚蜜鸟"
