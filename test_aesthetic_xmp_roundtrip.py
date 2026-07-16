#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iRateBird 鸟种颜值(aesthetic_index)写入 XMP 的真实往返测试。

背景：core/photo_processor.py 早已把 aesthetic_index 塞进 meta_item 交给
ExifToolManager 排队写入，但 tools/exiftool_manager.py 三处参数构建（单文件
embedded 子进程 _write_metadata_subprocess、XMP 侧车 _write_metadata_xmp_sidecar、
以及批量默认路径 batch_set_metadata 内联的 args_list）均缺少对应处理，导致
颜值分数从未真正落盘。修复借用 XMP-iptcExt:AdditionalModelInformation（与
罕见度 Event 同一 IPTC Extension 命名空间的冷门字段，LR/C1 主面板不显示）。

本测试用真实系统 exiftool（非 mock）对一张最小 JPEG 做写入→读回，钉住
三处写入路径中默认最常用的两条：单文件 embedded 写入与批量写入（覆盖
default metadata_write_mode="embedded" 下非 ARW 文件的实际生产路径）。

Real round-trip test for iRateBird species-beauty (aesthetic_index) XMP
writing.

Background: core/photo_processor.py already stages aesthetic_index into
meta_item for ExifToolManager to queue-write, but none of the three
arg-building sites in tools/exiftool_manager.py handled it
(_write_metadata_subprocess for single-file embedded writes,
_write_metadata_xmp_sidecar for the sidecar path, and the inlined args_list
in batch_set_metadata — the actual default production path for non-ARW
files) — so the score never reached disk. The fix borrows
XMP-iptcExt:AdditionalModelInformation, an obscure IPTC Extension field in
the same namespace family as the rarity "Event" borrowing, invisible in
LR/C1 main panels.

This test uses the real system exiftool (no mocking) to write then read
back a minimal JPEG, pinning down the two most commonly exercised paths:
the single-file embedded write and the batch write (which covers the
default metadata_write_mode="embedded" production path for non-ARW files).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from tools.exiftool_manager import ExifToolManager

AESTHETIC_VALUE = 90.0
# 实测：ExifTool 把 XMP-iptcExt:AdditionalModelInformation（与 Event 同样）
# 当作数值型 XMP 结构字段处理，JSON 读回是 float 90.0 而非字符串 "90.00"
# （用系统 exiftool 直接验证过，与 gbif_rarity_100/Event 行为一致）。
# Verified: ExifTool treats XMP-iptcExt:AdditionalModelInformation (like
# Event) as a numeric XMP struct field — JSON read-back is float 90.0, not
# the string "90.00" (confirmed directly against system exiftool; matches
# gbif_rarity_100/Event's existing behavior).
AESTHETIC_EXPECTED = 90.0


def _make_minimal_jpeg(path: str) -> None:
    """用 PIL 生成一张最小的合法 JPEG 供 exiftool 写入测试用。
    Create a minimal valid JPEG via PIL for exiftool write tests."""
    img = Image.new("RGB", (4, 4), color=(128, 128, 128))
    img.save(path, "JPEG")


@unittest.skipUnless(_HAS_PIL, "Pillow not installed, cannot build a real JPEG fixture")
class TestAestheticXmpRoundtrip(unittest.TestCase):
    """真实 exiftool 往返测试（非 mock）/ Real exiftool round-trip (no mocking)."""

    @classmethod
    def setUpClass(cls):
        # 构造真实 ExifToolManager：启动常驻读/写子进程。
        # Real ExifToolManager: starts the resident read/write subprocesses.
        cls.mgr = ExifToolManager()

    @classmethod
    def tearDownClass(cls):
        cls.mgr.shutdown()

    def setUp(self):
        fd, self.jpg_path = tempfile.mkstemp(suffix=".jpg", prefix="sp_aesthetic_")
        os.close(fd)
        _make_minimal_jpeg(self.jpg_path)

    def tearDown(self):
        if os.path.exists(self.jpg_path):
            os.remove(self.jpg_path)

    def _read_aesthetic(self) -> float:
        data = self.mgr.read_metadata(
            self.jpg_path, extra_args=["-XMP-iptcExt:AdditionalModelInformation"]
        ) or {}
        return float(data.get("AdditionalModelInformation"))

    def test_single_file_write_roundtrip(self):
        """单文件 embedded 写入路径（_write_metadata_subprocess）往返一致。
        Single-file embedded write path round-trips correctly."""
        item = {"file": self.jpg_path, "aesthetic_index": AESTHETIC_VALUE}
        ok = self.mgr._write_metadata_subprocess(item)
        self.assertTrue(ok)
        self.assertAlmostEqual(self._read_aesthetic(), AESTHETIC_EXPECTED, places=2)

    def test_batch_write_roundtrip(self):
        """批量写入路径（batch_set_metadata 默认 embedded 模式）往返一致，
        覆盖生产环境非 ARW 文件的实际默认路径。
        Batch write path (batch_set_metadata, default embedded mode)
        round-trips correctly — covers the actual default production path
        for non-ARW files."""
        stats = self.mgr.batch_set_metadata(
            [{"file": self.jpg_path, "aesthetic_index": AESTHETIC_VALUE}]
        )
        self.assertEqual(stats.get("failed", 0), 0)
        self.assertAlmostEqual(self._read_aesthetic(), AESTHETIC_EXPECTED, places=2)


if __name__ == "__main__":
    unittest.main()
