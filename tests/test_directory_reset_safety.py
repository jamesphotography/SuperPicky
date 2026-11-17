# -*- coding: utf-8 -*-
"""
重置不能丢照片：界面、命令行、批量子目录共用 core.directory_reset。

此前命令行 cmd_reset（界面的批量重置对已处理的子目录也调用它）有三处会删照片：
同名先删再移、移动后无条件 rmtree 子目录、第 3 步无条件 rmtree 评分目录。

A reset must never lose photos; the GUI, the CLI and batch subfolders now share
core.directory_reset. The old CLI path deleted same-name files and removed
folders unconditionally.
"""
import os

import pytest


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


@pytest.fixture
def i18n():
    from tools.i18n import get_i18n
    return get_i18n()


def _reset(root, i18n):
    from core.directory_reset import reset_single_directory
    return reset_single_directory(str(root), i18n, lambda _m: None, skip_exif_reset=True)


def test_same_name_in_burst_and_rating_folder_both_kept(tmp_path, i18n):
    """连拍子目录与评分目录里有同名照片：两份都保留，不互相覆盖。"""
    root = tmp_path
    _touch(root / "3星_优选" / "DSC1.NEF", b"rating copy")
    _touch(root / "3星_优选" / "burst_001" / "DSC1.NEF", b"burst copy")

    _reset(root, i18n)

    found = sorted(p.read_bytes() for p in root.rglob("DSC1.NEF"))
    assert found == [b"burst copy", b"rating copy"], "同名照片被覆盖删除"


def test_same_name_at_root_not_overwritten(tmp_path, i18n):
    """根目录已有同名文件：根目录那份不被覆盖，评分目录那份也不被删。"""
    root = tmp_path
    _touch(root / "DSC2.NEF", b"root original")
    _touch(root / "2星_良好" / "DSC2.NEF", b"organized")

    _reset(root, i18n)

    assert (root / "DSC2.NEF").read_bytes() == b"root original"
    assert (root / "2星_良好" / "DSC2.NEF").read_bytes() == b"organized", "评分目录里的照片被删掉"


def test_rating_folder_with_leftovers_not_deleted(tmp_path, i18n):
    """评分目录里还有移不走的文件时，目录整个保留。"""
    root = tmp_path
    _touch(root / "DSC3.NEF", b"root")
    _touch(root / "1星_普通" / "DSC3.NEF", b"kept")
    _touch(root / "1星_普通" / "DSC4.NEF", b"moved back")

    _reset(root, i18n)

    assert (root / "DSC4.NEF").read_bytes() == b"moved back"
    assert (root / "1星_普通" / "DSC3.NEF").read_bytes() == b"kept"


def test_cli_reset_uses_shared_safe_path(tmp_path):
    """命令行 cmd_reset 走同一套：同名照片两份都在。"""
    from types import SimpleNamespace
    import superpicky_cli

    root = tmp_path
    _touch(root / "DSC5.NEF", b"root original")
    _touch(root / "3星_优选" / "burst_002" / "DSC5.NEF", b"burst copy")

    superpicky_cli.cmd_reset(SimpleNamespace(directory=str(root), yes=True))

    found = sorted(p.read_bytes() for p in root.rglob("DSC5.NEF"))
    assert found == [b"burst copy", b"root original"]


# ---------------------------------------------------------------------------
# 符号链接：重置只动照片目录里的真实文件，绝不把外部文件搬进来
# Symlinks: reset only moves real files inside the photo folder
# ---------------------------------------------------------------------------

symlinks = pytest.mark.skipif(os.name == "nt", reason="Windows 建符号链接需要特权")


def _outside(tmp_path):
    """照片目录之外的一个文件夹，里面有一张照片 / a folder outside the photo folder."""
    out = tmp_path / "outside"
    _touch(out / "OUT1.NEF", b"outside photo")
    return out


@symlinks
def test_symlinked_burst_folder_not_followed(tmp_path, i18n):
    """评分目录里有指向外部文件夹的链接：外部照片原地不动，没被搬进照片目录。"""
    root = tmp_path / "photos"
    _touch(root / "2星_良好" / "DSC6.NEF", b"inside")
    out = _outside(tmp_path)
    os.symlink(out, root / "2星_良好" / "burst_009")

    _reset(root, i18n)

    assert (out / "OUT1.NEF").read_bytes() == b"outside photo"
    assert not (root / "OUT1.NEF").exists() and not (root / "2星_良好" / "OUT1.NEF").exists()
    assert (root / "DSC6.NEF").exists(), "真实照片照常移回"


@symlinks
def test_symlinked_file_in_rating_folder_not_moved(tmp_path, i18n):
    """评分目录里有指向外部文件的链接：不移动，外部文件不受影响。"""
    root = tmp_path / "photos"
    out = _outside(tmp_path)
    (root / "3星_优选").mkdir(parents=True)
    os.symlink(out / "OUT1.NEF", root / "3星_优选" / "OUT1.NEF")

    _reset(root, i18n)

    assert (out / "OUT1.NEF").read_bytes() == b"outside photo"
    assert not (root / "OUT1.NEF").exists()


@symlinks
def test_symlinked_rating_folder_untouched(tmp_path, i18n):
    """评分目录本身是指向外部的链接（含按目录名摊平那一步）：外部文件原地不动。"""
    root = tmp_path / "photos"
    root.mkdir()
    out = _outside(tmp_path)
    os.symlink(out, root / "3星_优选")

    _reset(root, i18n)

    assert (out / "OUT1.NEF").read_bytes() == b"outside photo"
    assert not (root / "OUT1.NEF").exists()
