# -*- coding: utf-8 -*-
"""
Lightroom 旗标写入的回归测试。

Lightroom Classic（13.2 起）从 XMP 读旗标时只看 xmpDM:good（"True" 留用 /
"False" 排除 / 缺省 = 无旗标）。SuperPicky 此前只写 xmpDM:pick，精选（皇冠）在
Lightroom Classic 里从未显示为留用旗标。这里锁定：三种状态都按 Lightroom 自己的写法
同时写 xmpDM:pick 与 xmpDM:good。

Regression test: flags are written as Lightroom writes them (xmpDM:pick plus
xmpDM:good), since Lightroom Classic reads only xmpDM:good.
"""
import shutil
import subprocess

import pytest

from tools.exiftool_manager import PICK_FLAG_CLEAR_ARGS, pick_flag_args


def test_pick_flag_args_match_lightroom():
    """留用 / 排除 / 无旗标 的参数与 Lightroom 写出的字段一致。"""
    assert pick_flag_args(1) == ['-XMP-xmpDM:Pick=1', '-XMP-xmpDM:Good=True']
    assert pick_flag_args(-1) == ['-XMP-xmpDM:Pick=-1', '-XMP-xmpDM:Good=False']
    assert pick_flag_args(0) == ['-XMP-xmpDM:Pick=0', '-XMP-xmpDM:Good=']
    assert '-XMP-xmpDM:Good=' in PICK_FLAG_CLEAR_ARGS


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="需要 exiftool")
def test_written_sidecar_carries_xmpdm_good(tmp_path):
    """用真实 exiftool 写侧车：xmpDM:good 的取值与 Lightroom 一致，无旗标时不写。"""
    xmp = tmp_path / "a.xmp"

    def write(pick: int) -> str:
        if xmp.exists():
            cmd = ["exiftool", "-q", "-overwrite_original", *pick_flag_args(pick), str(xmp)]
        else:
            cmd = ["exiftool", "-q", "-o", str(xmp), *pick_flag_args(pick)]
        subprocess.run(cmd, check=True)
        return xmp.read_text(encoding="utf-8")

    picked = write(1)
    assert "<xmpDM:good>True</xmpDM:good>" in picked and "<xmpDM:pick>1</xmpDM:pick>" in picked
    rejected = write(-1)
    assert "<xmpDM:good>False</xmpDM:good>" in rejected and "<xmpDM:pick>-1</xmpDM:pick>" in rejected
    cleared = write(0)
    assert "xmpDM:good" not in cleared and "<xmpDM:pick>0</xmpDM:pick>" in cleared
