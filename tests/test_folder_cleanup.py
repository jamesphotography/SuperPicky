# -*- coding: utf-8 -*-
"""
core.folder_cleanup 的测试：搬空的整理目录要删干净，真东西一样都不能碰。
Tests for pruning emptied organizer folders without touching real content.
"""
import os

from core.folder_cleanup import prune_organized_folders, prune_tree, prune_upwards

RATINGS = {"3星_优选", "2星_良好", "1星_普通", "0星_放弃", "3star_excellent", "2star_good"}


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def test_real_case_empty_sibling_and_appledouble(tmp_path):
    """2026-10-03 实测场景：整种合并搬走 burst 后，鸟种目录里还有处理时就搬空的
    2星_良好 与 exFAT 的 ._2星_良好——都应清掉，鸟种目录本身与根目录里它的 ._ 也删。"""
    root = tmp_path
    species = root / "黑颏雀鹀"
    (species / "2星_良好").mkdir(parents=True)
    _touch(species / "._2星_良好")
    (species / "3星_优选").mkdir()                     # burst_014 刚被搬走
    _touch(root / "._黑颏雀鹀")
    _touch(root / "刺蝗莺" / "3星_优选" / "burst_014" / "_Z9W4886.NEF")

    prune_upwards(str(root), str(species / "3星_优选"))

    assert not species.exists()
    assert not (root / "._黑颏雀鹀").exists()
    assert (root / "刺蝗莺" / "3星_优选" / "burst_014" / "_Z9W4886.NEF").exists()


def test_junk_files_do_not_block(tmp_path):
    """.DS_Store / Thumbs.db / desktop.ini 不再把目录「撑」成非空。"""
    d = tmp_path / "鸟种" / "2星_良好"
    for junk in (".DS_Store", "Thumbs.db", "desktop.ini"):
        _touch(d / junk)
    prune_upwards(str(tmp_path), str(d))
    assert not (tmp_path / "鸟种").exists()


def test_real_files_are_never_deleted(tmp_path):
    """照片、边车、用户自己的文件、本体还在的 ._ 伴生文件，一样都不删。"""
    d = tmp_path / "鸟种" / "2星_良好"
    keep = [d / "DSC1.ARW", d / "DSC1.xmp", d / "笔记.txt", d / "._DSC1.ARW"]
    for p in keep:
        _touch(p)
    _touch(d / ".DS_Store")
    prune_upwards(str(tmp_path), str(d))
    assert all(p.exists() for p in keep)
    assert (d / ".DS_Store").exists(), "目录保留时，里面的东西一概不动"


def test_root_and_hidden_folders_are_never_removed(tmp_path):
    """根目录不删；.superpicky 这类隐藏目录即使空也不删。"""
    (tmp_path / ".superpicky").mkdir()
    (tmp_path / "鸟种" / "2星_良好").mkdir(parents=True)
    prune_upwards(str(tmp_path), str(tmp_path / "鸟种" / "2星_良好"))
    assert tmp_path.exists() and (tmp_path / ".superpicky").exists()
    assert not prune_tree(str(tmp_path / ".superpicky"))


def test_walk_stops_at_first_folder_with_content(tmp_path):
    """有照片的上级停止修剪，但它里面同级的空目录照样清掉。"""
    species = tmp_path / "鸟种"
    _touch(species / "3星_优选" / "DSC2.ARW")
    (species / "2星_良好").mkdir()
    (species / "1星_普通").mkdir()
    prune_upwards(str(tmp_path), str(species / "1星_普通"))
    assert (species / "3星_优选" / "DSC2.ARW").exists()
    assert not (species / "2星_良好").exists()


def test_sweep_only_starts_from_organizer_folders(tmp_path):
    """收尾扫描只从星级 / burst_ 目录出发：两种布局的空目录清掉，
    用户自己在根目录建的空文件夹不碰。"""
    (tmp_path / "白鹭" / "2星_良好").mkdir(parents=True)            # 鸟种在外
    (tmp_path / "3星_优选" / "苍鹭" / "burst_003").mkdir(parents=True)  # 星级在外
    _touch(tmp_path / "白头鹎" / "3星_优选" / "DSC3.ARW")
    (tmp_path / "待整理").mkdir()                                      # 用户自己的

    removed = prune_organized_folders(str(tmp_path), RATINGS)

    assert not (tmp_path / "白鹭").exists()
    assert not (tmp_path / "3星_优选").exists()
    assert (tmp_path / "白头鹎" / "3星_优选" / "DSC3.ARW").exists()
    assert (tmp_path / "待整理").exists()
    assert removed == 5


def test_symlinks_are_not_followed(tmp_path):
    """符号链接当作真东西：不跟随、不删。"""
    target = tmp_path / "外部"
    target.mkdir()
    d = tmp_path / "鸟种" / "2星_良好"
    d.mkdir(parents=True)
    os.symlink(target, d / "链接")
    prune_upwards(str(tmp_path), str(d))
    assert (d / "链接").is_symlink() and target.exists()
