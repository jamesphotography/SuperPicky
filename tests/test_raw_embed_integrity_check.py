#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专有 RAW 写本体的完整性校验（#165）/ RAW in-place write verification (#165).

现象（Al，4.6.5 RC4，CR3）：勾了「同时写入专有 RAW 本体」，一批 113 张里却有几张
冒出了 .xmp 边车，日志显示全部写入成功、0 失败。

根因：auto 路径写完副本后比对「结构」，判据有两处错：
1. 要求数据块**偏移量**写入前后相等。exiftool 改写 CR2 / NEF / RAF 时会正常挪动
   预览图与 RAW 数据（实测偏移全变、内容一字节不变），于是这些格式 100% 回退边车，
   开关从未生效；
2. CR3 没有那些偏移标签，只剩不带 ``-n`` 读出的 FileSize 文本（如 "20 MB"），
   写入让大小变了几十 KB、恰好跨过 MB 取整边界的照片被判「结构变了」——随机回退。
回退还完全静默，算作成功。

修复：比较每个数据块的**长度与字节内容**、图像宽高、索尼 ARW 无长度可配对的偏移；
回退记录原因并进用户日志。

Al reported stray .xmp files next to a few CR3s with "write into proprietary RAW"
on. The verification compared block offsets (which exiftool legitimately moves
for CR2/NEF/RAF, so those always fell back) and a rounded "20 MB" FileSize
string (random fallbacks for CR3), silently. It now compares block content.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.exiftool_manager import (
    RAW_LAYOUT_TAGS,
    ExifToolManager,
    compare_raw_fingerprints,
    raw_layout_fingerprint,
)


def _write(path: str, data: bytes) -> None:
    """把字节写入文件 / write bytes to a file."""
    with open(path, "wb") as handle:
        handle.write(data)


class TestFingerprintComparison(unittest.TestCase):
    """纯函数层：指纹只认数据块内容，不认位置。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sp_rawfp_")
        self.raw = b"R" * 4000 + b"\x01\x02\x03" * 500      # 「RAW 图像数据」
        self.preview = b"\xff\xd8" + b"P" * 900 + b"\xff\xd9"  # 「内嵌预览」

    def _file(self, name: str, header: bytes, gap: bytes = b"") -> tuple:
        """
        造一个「元数据头 + 预览 + 间隔 + RAW 数据」的假 RAW，返回 (路径, 标签)。
        header 的长度变化就模拟了「写入元数据后数据块整体后挪」。
        """
        path = os.path.join(self.dir, name)
        body = header + self.preview + gap + self.raw
        _write(path, body)
        preview_at = len(header)
        raw_at = preview_at + len(self.preview) + len(gap)
        tags = {
            "PreviewImageStart": preview_at, "PreviewImageLength": len(self.preview),
            "StripOffsets": raw_at, "StripByteCounts": len(self.raw),
            "ImageWidth": 6000, "ImageHeight": 4000,
        }
        return path, tags

    def test_blocks_moved_but_identical_is_safe(self):
        """
        **#165 核心**：元数据变长、数据块整体后挪，但内容一字节没变 → 安全。

        旧判据（偏移相等）在这里会判失败，CR2/NEF/RAF 因此永远写不进本体。
        """
        before = raw_layout_fingerprint(*self._file("a.cr2", b"H" * 100))
        after = raw_layout_fingerprint(*self._file("b.cr2", b"H" * 4321))

        self.assertIsNone(compare_raw_fingerprints(before, after))

    def test_changed_raw_bytes_are_rejected(self):
        """RAW 数据哪怕只变一个字节，也必须拒绝替换原文件。"""
        path_a, tags_a = self._file("a.nef", b"H" * 100)
        before = raw_layout_fingerprint(path_a, tags_a)
        path_b, tags_b = self._file("b.nef", b"H" * 100)
        with open(path_b, "r+b") as handle:
            handle.seek(tags_b["StripOffsets"] + 10)
            handle.write(b"X")

        reason = compare_raw_fingerprints(before, raw_layout_fingerprint(path_b, tags_b))

        self.assertIsNotNone(reason)
        self.assertIn("StripOffsets", reason)

    def test_changed_preview_length_is_rejected(self):
        """预览长度变了（预览被截断/替换）同样拒绝。"""
        path_a, tags_a = self._file("a.cr2", b"H" * 100)
        path_b, tags_b = self._file("b.cr2", b"H" * 100)
        tags_b["PreviewImageLength"] -= 1

        reason = compare_raw_fingerprints(raw_layout_fingerprint(path_a, tags_a),
                                          raw_layout_fingerprint(path_b, tags_b))

        self.assertIsNotNone(reason)

    def test_nothing_verifiable_is_not_safe(self):
        """
        一个数据块都没校验到 → 按「无法确认」回退边车，不能因为「没比出差异」就改写原图。

        旧代码在 CR3 上正是这样：没有一个偏移标签，判据退化成只比文件大小。
        """
        path, _ = self._file("a.cr3", b"H" * 100)
        empty = raw_layout_fingerprint(path, {"ImageWidth": 6000, "ImageHeight": 4000})

        self.assertEqual(compare_raw_fingerprints(empty, empty), "no data block to verify")

    def test_cr3_media_data_block_is_verified(self):
        """CR3 的图像数据在 mdat：MediaDataOffset/MediaDataSize 必须参与校验。"""
        path_a, tags_a = self._file("a.cr3", b"H" * 100)
        path_b, tags_b = self._file("b.cr3", b"H" * 900)
        for tags in (tags_a, tags_b):
            tags["MediaDataOffset"] = tags.pop("StripOffsets")
            tags["MediaDataSize"] = tags.pop("StripByteCounts")
            del tags["PreviewImageStart"], tags["PreviewImageLength"]

        before = raw_layout_fingerprint(path_a, tags_a)
        self.assertIn("MediaDataOffset", before["blocks"])
        self.assertIsNone(compare_raw_fingerprints(before, raw_layout_fingerprint(path_b, tags_b)))

    def test_multi_strip_values_are_supported(self):
        """多条带 RAW：exiftool -n 输出空格分隔的数列，逐段哈希。"""
        path, tags = self._file("a.raf", b"H" * 100)
        start, count = tags["StripOffsets"], tags["StripByteCounts"]
        half = count // 2
        tags["StripOffsets"] = f"{start} {start + half}"
        tags["StripByteCounts"] = f"{half} {count - half}"

        fp = raw_layout_fingerprint(path, tags)

        lengths, digest = fp["blocks"]["StripOffsets"]
        self.assertEqual(lengths, (half, count - half))
        self.assertIsNotNone(digest)

    def test_out_of_range_block_is_unreadable_not_safe(self):
        """标签指到文件外（文件被截断）→ 无法确认，回退。"""
        path, tags = self._file("a.nef", b"H" * 100)
        tags["StripByteCounts"] += 10_000

        fp = raw_layout_fingerprint(path, tags)

        self.assertIsNotNone(compare_raw_fingerprints(fp, fp))

    def test_sony_fixed_offsets_and_dimensions_must_not_change(self):
        """索尼隐藏数据偏移、图像宽高任一变化都拒绝（沿用原设计的保守处理）。"""
        path, tags = self._file("a.arw", b"H" * 100)
        tags["SR2SubIFDOffset"] = 5000
        before = raw_layout_fingerprint(path, tags)

        moved = dict(tags, SR2SubIFDOffset=5004)
        self.assertEqual(compare_raw_fingerprints(before, raw_layout_fingerprint(path, moved)),
                         "fixed offsets moved")
        resized = dict(tags, ImageWidth=5999)
        self.assertEqual(compare_raw_fingerprints(before, raw_layout_fingerprint(path, resized)),
                         "image size changed")

    def test_unreadable_layout_is_not_safe(self):
        """读不到布局（exiftool 超时/失败）→ 回退。"""
        self.assertEqual(compare_raw_fingerprints(None, None), "layout unreadable")

    def test_rounded_file_size_is_no_longer_compared(self):
        """
        #165 随机回退的直接来源：FileSize 不再参与比较（写入元数据本来就会改变大小）。
        """
        self.assertNotIn("FileSize", RAW_LAYOUT_TAGS)


class TestAutoWritePath(unittest.TestCase):
    """写入层：auto 路径的替换 / 回退 / 记录。不启动真实 exiftool。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sp_rawauto_")
        self.nef = os.path.join(self.dir, "DSC_0001.NEF")
        self.data = b"H" * 64 + b"RAWDATA" * 200
        _write(self.nef, self.data)
        self.sidecar = os.path.splitext(self.nef)[0] + ".xmp"

    def _manager(self, tamper: bool = False, write_ok: bool = True):
        """
        造替身：写副本时在文件头插入「元数据」（数据块后挪），tamper=True 时再改坏 RAW 数据。
        布局标签按文件内容现算，模拟 exiftool 的 -n 输出。
        """
        manager = object.__new__(ExifToolManager)
        manager._raw_embed_fallbacks = []
        import threading
        manager._raw_embed_fallback_lock = threading.Lock()
        manager._get_arw_write_mode = lambda path=None: "auto"
        manager._apply_field_switches = lambda item: item
        self.sidecar_writes = []

        def fake_sidecar(item):
            self.sidecar_writes.append(item["file"])
            _write(self.sidecar, b"<x:xmpmeta/>")
            return True

        def fake_write(item, in_place=True):
            if not write_ok:
                return False
            with open(item["file"], "rb") as handle:
                body = handle.read()
            body = b"META" * 30 + body
            if tamper:
                body = body[:-1] + b"!"
            _write(item["file"], body)
            return True

        def fake_layout(path):
            with open(path, "rb") as handle:
                body = handle.read()
            start = body.index(b"RAWDATA")
            return {"StripOffsets": start, "StripByteCounts": len(body) - start,
                    "ImageWidth": 6000, "ImageHeight": 4000}

        manager._write_metadata_xmp_sidecar = fake_sidecar
        manager._write_metadata_subprocess = fake_write
        manager._read_raw_layout_tags = fake_layout
        return manager

    def _item(self):
        return {"file": self.nef, "rating": 3, "pick": 0}

    def test_moved_but_intact_raw_is_written_in_place(self):
        """数据块后挪但内容不变 → 替换原文件，不产生边车（#165 修复前 NEF 永远走边车）。"""
        manager = self._manager()

        self.assertTrue(manager._write_metadata_arw(self._item()))

        with open(self.nef, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"META"))
        self.assertFalse(os.path.exists(self.sidecar))
        self.assertEqual(manager.drain_raw_embed_fallbacks(), [])
        self.assertEqual([n for n in os.listdir(self.dir) if n != "DSC_0001.NEF"], [],
                         "临时副本没有清理")

    def test_damaged_copy_falls_back_and_is_recorded(self):
        """副本 RAW 数据被改坏 → 原文件保持原样，写边车，并记录原因供日志汇总。"""
        manager = self._manager(tamper=True)

        self.assertTrue(manager._write_metadata_arw(self._item()))

        with open(self.nef, "rb") as handle:
            self.assertEqual(handle.read(), self.data, "原文件被改动了")
        self.assertEqual(self.sidecar_writes, [self.nef])
        events = manager.drain_raw_embed_fallbacks()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], self.nef)
        self.assertIn("content changed", events[0][1])
        self.assertEqual(manager.drain_raw_embed_fallbacks(), [], "drain 后应清空")

    def test_write_failure_falls_back_and_is_recorded(self):
        """exiftool 写副本失败 → 回退边车并记录。"""
        manager = self._manager(write_ok=False)

        self.assertTrue(manager._write_metadata_arw(self._item()))

        with open(self.nef, "rb") as handle:
            self.assertEqual(handle.read(), self.data)
        self.assertEqual(manager.drain_raw_embed_fallbacks()[0][1], "write failed")

    def test_existing_sidecar_is_kept_in_step(self):
        """
        已有边车（旧版本或此前回退留下的）在写本体成功后同步更新——Lightroom 对专有
        RAW 优先读边车，不同步会一直显示旧星级。
        """
        _write(self.sidecar, b"<old/>")
        manager = self._manager()

        manager._write_metadata_arw(self._item())

        self.assertEqual(self.sidecar_writes, [self.nef])

    def test_no_sidecar_is_created_when_none_existed(self):
        """写本体成功且原本没有边车 → 不新建边车（#165 用户要的就是不要多余 .xmp）。"""
        manager = self._manager()

        manager._write_metadata_arw(self._item())

        self.assertEqual(self.sidecar_writes, [])


if __name__ == "__main__":
    unittest.main()
