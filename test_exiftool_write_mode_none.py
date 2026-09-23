#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「不写元数据」设置必须在**所有**写入路径上生效 / The "none" write mode must hold everywhere.

对应用户反馈（2026-09-23）：把元数据写入设为「不写」之后，专有 RAW 旁边仍然
不断冒出 `.xmp` 边车文件。尼康自家软件不读 XMP 边车，这些文件对用户既无用又
碍眼，而且他明确关掉了这个功能。

根因：`ExifToolManager` 的写入入口里，只有 `set_metadata` / `batch_set_metadata`
检查了 `metadata_write_mode`；`set_rating_and_pick` **完全没有检查**，直接按
「专有 RAW 强制走边车」的路由写出 `.xmp`。而它正是用户在结果浏览器里改星级
（`_on_rating_changed`）和标记为无鸟（`_run_mark_no_bird`）时走的那条路。
调用处的注释还写着「mode=none 时内部自动跳过」——注释描述的行为并不存在。

断言方式刻意选「磁盘上有没有多出 .xmp」而不是「某个内部方法有没有被调用」：
用户看到的就是文件，换一种内部实现这条测试仍然成立。

Regression: with the metadata write mode set to "none", `set_rating_and_pick`
still emitted an XMP sidecar for proprietary RAW, because it was the one write
entry point that never consulted the setting. Asserted on the file appearing on
disk rather than on internal calls, since the file is what the user sees.
"""

import glob
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.exiftool_manager import ExifToolManager, get_exiftool_manager


class TestWriteModeNoneIsHonored(unittest.TestCase):
    """改星级路径必须遵守「不写元数据」/ The rating path must honor "none"."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sp_wmode_")
        # 内容无效不影响本测试：断言的是「有没有生成边车」，不是边车内容对不对。
        # Invalid contents are fine; the assertion is about the sidecar existing.
        self.nef = os.path.join(self.dir, "DSC_0001.NEF")
        with open(self.nef, "wb") as handle:
            handle.write(b"\x00" * 64)
        self._original = ExifToolManager._get_metadata_write_mode

    def tearDown(self):
        ExifToolManager._get_metadata_write_mode = self._original

    def _sidecars(self):
        return sorted(os.path.basename(p)
                      for p in glob.glob(os.path.join(self.dir, "*.xmp")))

    def _set_mode(self, mode: str):
        ExifToolManager._get_metadata_write_mode = lambda self, _m=mode: _m

    def test_sidecar_is_written_when_the_user_allows_it(self):
        """
        对照组：选「写入」时边车照常生成。

        没有这一条，下面那条「不生成」可能是因为别的原因（比如 exiftool 对这个
        假 NEF 直接失败）而假绿——对照组证明本测试确实看得见边车。

        Control case proving the test can actually observe a sidecar being
        written; without it, the "not written" assertion could pass for the
        wrong reason.
        """
        self._set_mode("sidecar")
        manager = get_exiftool_manager()

        manager.set_rating_and_pick(self.nef, 3)

        self.assertEqual(self._sidecars(), ["DSC_0001.xmp"])

    def test_no_sidecar_when_the_user_turned_metadata_off(self):
        """
        用户选「不写元数据」后，改星级不得生成 `.xmp`。

        这是用户报的症状本身：尼康软件不读 XMP 边车，而他已经明确关掉了写入。
        """
        self._set_mode("none")
        manager = get_exiftool_manager()

        result = manager.set_rating_and_pick(self.nef, 3)

        self.assertEqual(self._sidecars(), [],
                         "设为「不写」后改星级仍然生成了 XMP 边车")
        self.assertTrue(result,
                        "按配置跳过应视为成功，否则上层会当成写入失败报错")

    def test_no_sidecar_when_marking_a_photo_as_having_no_bird(self):
        """
        「标记为无鸟」同样不得生成 `.xmp`。

        它走的是同一个入口（`_run_mark_no_bird` 里 `set_rating_and_pick(path, -1)`），
        漏掉的话用户每标一张就多一个边车文件。
        """
        self._set_mode("none")
        manager = get_exiftool_manager()

        manager.set_rating_and_pick(self.nef, -1)

        self.assertEqual(self._sidecars(), [])


if __name__ == "__main__":
    unittest.main()
