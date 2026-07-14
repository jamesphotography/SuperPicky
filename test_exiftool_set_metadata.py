#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExifToolManager.set_metadata 回归测试。

背景：birdid_server（LR 插件 /exif/write-title、/exif/write-caption）和
birdid_cli --write-exif 一直调用 set_metadata，但该方法在一次历史重构中
丢失，AttributeError 被上层 except 吞掉，功能长期静默失效。本测试用
fake 替身（不依赖真实 exiftool 进程）钉住三件事：

1. 方法存在且中文值经 UTF-8 临时文件重定向写入（-TAG<=tmp.txt 约定）；
2. ARW 只写 XMP 侧车（不动 RAW 本体），无组前缀标签补 XMP:、
   IPTC 题注映射为 XMP:Description；
3. metadata_write_mode=none 时跳过写入并返回 True。

Regression tests for ExifToolManager.set_metadata. The method is called by
the LR-plugin server endpoints and birdid_cli but was lost in a refactor
(AttributeError swallowed upstream). These tests use fakes (no real
exiftool) to pin down: UTF-8 temp-file redirection for Chinese values,
ARW-to-XMP-sidecar routing with tag aliasing, and the "none" write mode
short-circuit.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.exiftool_manager import ExifToolManager

CN_TITLE = "黑尾塍鹬 (Black-tailed Godwit)"
CN_CAPTION = "锐度:512.34\n美学:6.78 中文题注"


def _make_manager(write_mode: str = "embedded"):
    """
    构造不启动真实 exiftool 的 ExifToolManager 替身。

    绕过 __init__（避免探测/启动 exiftool 进程），仅注入被测方法所需的
    协作者：_get_metadata_write_mode 返回指定模式，_send_to_process 记录
    收到的参数并在调用时读取临时文件内容（临时文件会在 finally 中删除，
    必须在此刻读）。

    Build an ExifToolManager test double without touching real exiftool.
    Skips __init__ and stubs the collaborators set_metadata needs; the fake
    _send_to_process captures args and reads temp-file contents at call time
    (they are deleted in the finally block afterwards).
    """
    mgr = object.__new__(ExifToolManager)
    calls = []

    mgr._get_metadata_write_mode = lambda: write_mode

    def fake_send_to_process(args, timeout=30.0):
        redirected = {}
        for arg in args:
            if '<=' in arg and arg.startswith('-'):
                tag, tmp_path = arg[1:].split('<=', 1)
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    redirected[tag] = f.read()
        calls.append({'args': list(args), 'redirected': redirected})
        return True

    mgr._send_to_process = fake_send_to_process
    return mgr, calls


class TestSetMetadata(unittest.TestCase):
    def setUp(self):
        fd, self.jpg_path = tempfile.mkstemp(suffix='.jpg', prefix='sp_test_')
        os.close(fd)
        fd, self.arw_path = tempfile.mkstemp(suffix='.arw', prefix='sp_test_')
        os.close(fd)

    def tearDown(self):
        for p in (self.jpg_path, self.arw_path):
            if os.path.exists(p):
                os.remove(p)

    def test_method_exists(self):
        """方法必须存在（防止重构再次丢失）/ Method must exist."""
        self.assertTrue(hasattr(ExifToolManager, 'set_metadata'))

    def test_chinese_values_via_utf8_temp_files(self):
        """中文值必须经 UTF-8 临时文件重定向写入原文件 / UTF-8 temp-file redirection."""
        mgr, calls = _make_manager("embedded")
        ok = mgr.set_metadata(self.jpg_path, {'Title': CN_TITLE, 'Caption-Abstract': CN_CAPTION})

        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        args = calls[0]['args']
        # 目标是原文件本体（embedded 模式）/ target is the file itself
        self.assertEqual(args[-1], self.jpg_path)
        self.assertIn('-overwrite_original_in_place', args)
        # 值不允许内联出现在参数里 / values must never appear inline in args
        self.assertFalse(any(CN_TITLE in a for a in args))
        # 临时文件内容与原值逐字一致 / temp-file contents match exactly
        self.assertEqual(calls[0]['redirected'].get('Title'), CN_TITLE)
        self.assertEqual(calls[0]['redirected'].get('Caption-Abstract'), CN_CAPTION)

    def test_iptc_tag_adds_utf8_charset_declaration(self):
        """写 IPTC 标签必须附加 UTF-8 字符集声明与 CodedCharacterSet 标记。
        IPTC IIM 默认 Latin-1,缺少声明时中文变 '?' 或被第三方按 Latin-1 误读。"""
        mgr, calls = _make_manager("embedded")
        mgr.set_metadata(self.jpg_path, {'Caption-Abstract': CN_CAPTION})
        args = calls[0]['args']
        self.assertIn('-charset', args)
        self.assertIn('iptc=utf8', args)
        self.assertIn('-CodedCharacterSet=UTF8', args)

    def test_xmp_only_write_skips_iptc_charset(self):
        """纯 XMP 写入不应附带 IPTC 字符集参数(避免无谓新增 IPTC 段)。"""
        mgr, calls = _make_manager("embedded")
        mgr.set_metadata(self.jpg_path, {'Title': CN_TITLE})
        args = calls[0]['args']
        self.assertNotIn('iptc=utf8', args)
        self.assertNotIn('-CodedCharacterSet=UTF8', args)

    def test_arw_routes_to_xmp_sidecar(self):
        """ARW 只写 XMP 侧车，标签补 XMP: 组、IPTC 题注映射 Description。"""
        mgr, calls = _make_manager("embedded")
        ok = mgr.set_metadata(self.arw_path, {'Title': CN_TITLE, 'Caption-Abstract': CN_CAPTION})

        self.assertTrue(ok)
        args = calls[0]['args']
        expected_xmp = os.path.splitext(self.arw_path)[0] + '.xmp'
        # 目标是侧车而非 RAW 本体 / target is the sidecar, not the RAW body
        self.assertEqual(args[-1], expected_xmp)
        self.assertNotIn(self.arw_path, args)
        self.assertEqual(calls[0]['redirected'].get('XMP:Title'), CN_TITLE)
        self.assertEqual(calls[0]['redirected'].get('XMP:Description'), CN_CAPTION)

    def test_write_mode_none_skips(self):
        """metadata_write_mode=none 跳过写入且返回 True / 'none' mode short-circuits."""
        mgr, calls = _make_manager("none")
        ok = mgr.set_metadata(self.jpg_path, {'Title': CN_TITLE})
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_missing_file_or_empty_tags(self):
        """文件不存在或空 tags 返回 False / missing file or empty tags -> False."""
        mgr, calls = _make_manager("embedded")
        self.assertFalse(mgr.set_metadata('/nonexistent/x.jpg', {'Title': 'x'}))
        self.assertFalse(mgr.set_metadata(self.jpg_path, {}))
        self.assertEqual(calls, [])

    def test_temp_files_cleaned_up(self):
        """调用结束后临时文件必须被清理 / temp files removed afterwards."""
        mgr, calls = _make_manager("embedded")
        mgr.set_metadata(self.jpg_path, {'Title': CN_TITLE})
        for arg in calls[0]['args']:
            if '<=' in arg:
                tmp_path = arg.split('<=', 1)[1]
                self.assertFalse(os.path.exists(tmp_path), f"temp file not cleaned: {tmp_path}")


if __name__ == '__main__':
    unittest.main()
