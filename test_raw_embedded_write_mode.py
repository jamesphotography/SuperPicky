#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「写入文件」对专有 RAW 必须真正生效 / "Embedded" must actually apply to proprietary RAW.

对应用户反馈（2026-09-23）：设置里选了「写入文件」，专有 RAW 旁边照样只出现
`.xmp` 边车，元数据从未进入 RAW 本体。尼康自家软件不读 XMP 边车，对这类用户
等于这个功能不存在，而界面上那个选项让他以为选了就会写进文件。

原因是 2026-07 那次性能优化的取舍：不重写 RAW 本体快得多（~190ms/张 → ~10ms/张）
也更安全，于是 `get_arw_write_mode_for_file` 对全部专有 RAW（DNG 除外）**无条件**
返回 "sidecar"，把用户的选择整个绕开了。取舍本身有数据支撑，问题在于它没有给
用户留任何出口，界面还在骗人。

用户 2026-09-23 拍板：给专有 RAW 开一个出口。**没有复用 `metadata_write_mode`**
——它的默认值就是 "embedded"，直接让 embedded 对 RAW 生效等于把从未动过设置的
用户全部改成「写本体 + 整份复制」，静默撤销 2026-07 的性能优化；而把默认值迁成
sidecar 又会误伤 JPEG 用户（embedded 对 JPEG 一直是有效的）。故新增一个独立开关
`raw_embed_metadata`，**默认关闭**，现有用户行为一字不变，只有主动勾选的人承担代价。

开关打开时走 **auto** 而不是直写：auto 会先复制一份、写副本、比对 RAW 关键结构，
一致才原子替换，结构变了就回退边车，原文件永远不会被写坏。代价是多一次整文件
复制（内置 APFS 盘上因写时复制几乎免费，存储卡/ExFAT 外置盘上是真实全量复制）。

Regression: choosing "embedded" never applied to proprietary RAW, so metadata
never entered the file body — invisible to software that does not read XMP
sidecars. Now honored, routed through the structure-verifying "auto" path.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from advanced_config import AdvancedConfig
from tools.exiftool_manager import ExifToolManager


def _config(metadata_mode: str = "embedded", raw_embed=None) -> AdvancedConfig:
    """造一个隔离的配置实例，绝不碰用户真实的 advanced_config.json。"""
    data = {"metadata_write_mode": metadata_mode}
    if raw_embed is not None:
        data["raw_embed_metadata"] = raw_embed
    path = os.path.join(tempfile.mkdtemp(prefix="sp_cfg_"), "advanced_config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return AdvancedConfig(config_file=path)


class TestRawWriteModeFollowsTheSetting(unittest.TestCase):
    """配置层：专有 RAW 的写入策略必须跟随用户选择。"""

    def test_default_is_unchanged_so_nobody_is_surprised_by_an_upgrade(self):
        """
        **默认必须仍是边车。**

        这条是整个改动的安全底线：`metadata_write_mode` 的默认值就是 "embedded"，
        若让它直接对 RAW 生效，所有从未动过设置的用户升级后都会突然变成写本体 +
        整份复制。默认关闭，升级后行为一字不变。
        """
        cfg = _config("embedded")          # 默认模式，且未设置新开关

        self.assertIs(cfg.raw_embed_metadata, False)
        for name in ("DSC_0001.NEF", "DSC_0001.ARW", "IMG_0001.CR3", "P1000001.RW2"):
            self.assertEqual(cfg.get_arw_write_mode_for_file(f"/x/{name}"), "sidecar",
                             f"{name} 的默认行为被改变了")

    def test_opting_in_routes_proprietary_raw_through_the_verified_path(self):
        """
        勾选「专有 RAW 也写入文件本体」后走 auto（写本体 + 结构校验 + 回退）。

        不用 "embedded"/"inplace" 直写，是因为那两条没有结构校验：exiftool 一旦
        写坏 RAW 结构，损伤直接落在用户的原始文件上。auto 写的是副本，比对通过
        才替换。
        """
        cfg = _config("embedded", raw_embed=True)

        for name in ("DSC_0001.NEF", "DSC_0001.ARW", "IMG_0001.CR3", "P1000001.RW2"):
            self.assertEqual(cfg.get_arw_write_mode_for_file(f"/x/{name}"), "auto",
                             f"{name} 未跟随开关")

    def test_sidecar_mode_wins_over_the_opt_in(self):
        """
        选「写入边车」时，即便勾了开关也仍写边车。

        「全部写边车」是更强的意思表示；两者冲突时听用户在主设置里说的那句。
        """
        cfg = _config("sidecar", raw_embed=True)

        self.assertEqual(cfg.get_arw_write_mode_for_file("/x/DSC_0001.NEF"), "sidecar")

    def test_dng_is_unaffected(self):
        """
        DNG 不在强制边车之列（它是开放格式，写本体本就安全），本次改动不该碰它。
        """
        cfg = _config("embedded", raw_embed=True)

        self.assertNotEqual(cfg.get_arw_write_mode_for_file("/x/a.DNG"), "sidecar")


class TestSetMetadataTargetFollowsTheSetting(unittest.TestCase):
    """写入层：目标文件必须跟随设置，而不是一律写边车。"""

    def setUp(self):
        fd, self.nef = tempfile.mkstemp(suffix=".NEF", prefix="sp_test_")
        os.close(fd)

    def tearDown(self):
        for path in (self.nef, os.path.splitext(self.nef)[0] + ".xmp"):
            if os.path.exists(path):
                os.remove(path)

    def _manager(self, mode: str, raw_embed: bool = False):
        """不启动真实 exiftool 的替身，只记录最终参数。"""
        manager = object.__new__(ExifToolManager)
        calls = []
        manager._get_metadata_write_mode = lambda: mode
        manager._raw_embed_enabled = lambda: raw_embed

        def fake_send(args, timeout=30.0):
            calls.append(list(args))
            return True

        manager._send_to_process = fake_send
        return manager, calls

    def test_proprietary_raw_still_defaults_to_the_sidecar(self):
        """未勾开关时，专有 RAW 仍写边车——默认行为不变。"""
        manager, calls = self._manager("embedded")

        manager.set_metadata(self.nef, {"Title": "家燕"})

        self.assertTrue(calls[0][-1].endswith(".xmp"))

    def test_opting_in_writes_into_the_raw_itself(self):
        """
        勾选开关后，set_metadata 的目标是 RAW 本体而不是 .xmp。

        这是用户真正要的东西：元数据进了 NEF，尼康的软件才看得见。
        """
        manager, calls = self._manager("embedded", raw_embed=True)

        ok = manager.set_metadata(self.nef, {"Title": "家燕"})

        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], os.path.abspath(self.nef),
                         "仍然写向 .xmp 边车，设置没有生效")

    def test_sidecar_mode_still_targets_the_sidecar(self):
        """选「写入边车」时仍写 .xmp，原行为不变。"""
        manager, calls = self._manager("sidecar")

        manager.set_metadata(self.nef, {"Title": "家燕"})

        self.assertTrue(calls[0][-1].endswith(".xmp"))

    def test_keyword_read_target_follows_the_setting(self):
        """
        读既有关键字时看的文件也要跟着走。

        写进本体却仍去边车读旧关键字，merge-add 会基于一份过期清单计算，
        结果是关键字丢失或重复。
        """
        manager, _ = self._manager("embedded", raw_embed=True)
        # 边车必须真实存在，否则 _sidecar_read_target 会因为「文件不在」而回落到
        # 本体，测试就会因为错误的原因通过。
        # The sidecar must exist, or the function falls back to the body anyway
        # and the test would pass for the wrong reason.
        sidecar = os.path.splitext(self.nef)[0] + ".xmp"
        with open(sidecar, "w", encoding="utf-8") as handle:
            handle.write("<x:xmpmeta/>")

        self.assertEqual(manager._sidecar_read_target(self.nef), self.nef)


if __name__ == "__main__":
    unittest.main()
