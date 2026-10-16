# -*- coding: utf-8 -*-
"""
重置摊平后的空目录清理：exFAT 盘上的 ._ 伴生文件不能再把鸟种目录留下。
Reset flatten must not leave species folders behind because of AppleDouble files.
"""
import os

from tools.find_bird_util import force_flatten_directory


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def test_flatten_removes_species_folders_despite_appledouble(tmp_path):
    """species-first 布局 + exFAT 的 ._ 文件：照片回到根目录，鸟种目录与根目录里
    它们的 ._ 伴生文件都删掉；处理时留下的空「黄冠吸蜜鸟/2星_良好」也一并清掉。"""
    root = tmp_path
    burst = root / "灰头吸蜜鸟" / "3星_优选" / "burst_009"
    _touch(burst / "_Z9W5144.NEF")
    _touch(burst / "_Z9W5144.xmp")
    _touch(burst / "._burst_009")                      # 父目录里 burst 的伴生文件放错位置也无妨
    _touch(root / "灰头吸蜜鸟" / "3星_优选" / "._burst_009")
    _touch(root / "灰头吸蜜鸟" / "._3星_优选")
    _touch(root / "灰头吸蜜鸟" / ".DS_Store")
    _touch(root / "._灰头吸蜜鸟")
    (root / "黄冠吸蜜鸟" / "2星_良好").mkdir(parents=True)
    _touch(root / "黄冠吸蜜鸟" / "._2星_良好")
    _touch(root / "._黄冠吸蜜鸟")

    stats = force_flatten_directory(str(root), log_callback=lambda _m: None)

    assert (root / "_Z9W5144.NEF").exists() and (root / "_Z9W5144.xmp").exists()
    assert not (root / "灰头吸蜜鸟").exists()
    assert not (root / "黄冠吸蜜鸟").exists()
    assert not (root / "._灰头吸蜜鸟").exists() and not (root / "._黄冠吸蜜鸟").exists()
    assert stats["moved"] == 2
    assert stats["dirs_removed"] == 5     # 灰头吸蜜鸟、3星_优选、burst_009、黄冠吸蜜鸟、2星_良好


def test_flatten_keeps_folders_that_still_hold_real_files(tmp_path):
    """同名冲突没移走的照片、用户自建的目录都原样保留。"""
    root = tmp_path
    _touch(root / "DSC1.NEF", b"root copy")
    _touch(root / "灰头吸蜜鸟" / "2星_良好" / "DSC1.NEF", b"organized copy")
    _touch(root / "灰头吸蜜鸟" / "2星_良好" / "._DSC1.NEF")
    _touch(root / "我的整理" / "笔记.txt")

    stats = force_flatten_directory(str(root), log_callback=lambda _m: None)

    assert (root / "灰头吸蜜鸟" / "2星_良好" / "DSC1.NEF").read_bytes() == b"organized copy"
    assert (root / "灰头吸蜜鸟" / "2星_良好" / "._DSC1.NEF").exists(), "本体还在，伴生文件不删"
    assert (root / "DSC1.NEF").read_bytes() == b"root copy"
    assert (root / "我的整理" / "笔记.txt").exists()
    assert stats["skipped"] == 1 and stats["dirs_removed"] == 0
