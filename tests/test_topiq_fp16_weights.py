# -*- coding: utf-8 -*-
"""
美学模型半精度存储权重单测 / Tests for the fp16-stored TOPIQ aesthetic weights.

半精度版是为 Windows CUDA 安装包腾空间（GitHub 单文件 2 GiB 上限）；这里守住：
路径查找优先半精度版、下载清单与打包清单同步、半精度文件就是原权重 .half() 的结果。
The fp16 file keeps the Windows CUDA installer under GitHub's 2 GiB limit. These tests guard
the lookup priority, download/build list sync, and that the fp16 file equals original .half().
"""
import os
import re
import sys

import pytest

import topiq_model
from topiq_model import TOPIQ_WEIGHT_NAMES, get_topiq_weight_path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP16, FP32 = TOPIQ_WEIGHT_NAMES


def _isolate(tmp_path, monkeypatch, names):
    """
    让查找只看 tmp_path/models / Make the lookup see only tmp_path/models.

    参数 / Parameters:
        tmp_path: pytest 临时目录 / pytest temp dir.
        monkeypatch: pytest monkeypatch.
        names (list[str]): 要放进 models/ 的文件名 / filenames to create in models/.
    """
    models = tmp_path / "models"
    models.mkdir()
    for n in names:
        (models / n).write_bytes(b"x")
    monkeypatch.setattr(topiq_model, "__file__", str(tmp_path / "topiq_model.py"))
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return models


def test_lookup_prefers_fp16_then_falls_back(tmp_path, monkeypatch):
    models = _isolate(tmp_path, monkeypatch, [FP32, FP16])
    assert get_topiq_weight_path() == str(models / FP16)
    (models / FP16).unlink()
    assert get_topiq_weight_path() == str(models / FP32)     # 老源码环境 / old source checkout
    (models / FP32).unlink()
    with pytest.raises(FileNotFoundError):
        get_topiq_weight_path()


def test_download_and_build_lists_ship_fp16():
    """下载清单与两个打包脚本的模型清单必须同步（否则 Full 构建会因缺文件失败）。"""
    for name in ("scripts/download_models.py", "build_release_win.py", "build_release_mac.py"):
        with open(os.path.join(REPO, name), encoding="utf-8") as f:
            src = f.read()
        assert re.search(r'"filename":\s*"%s"' % re.escape(FP16), src), name
        assert not re.search(r'"filename":\s*"%s"' % re.escape(FP32), src), name


@pytest.mark.skipif(not all(os.path.isfile(os.path.join(REPO, "models", n)) for n in TOPIQ_WEIGHT_NAMES),
                    reason="美学权重未下载 / weights not downloaded")
def test_fp16_file_equals_original_half():
    import torch

    a = torch.load(os.path.join(REPO, "models", FP32), map_location="cpu", weights_only=True)["params"]
    b = torch.load(os.path.join(REPO, "models", FP16), map_location="cpu", weights_only=True)["params"]
    assert a.keys() == b.keys()
    for k, v in a.items():
        expect = v.half() if v.is_floating_point() else v
        assert b[k].dtype == expect.dtype and torch.equal(b[k], expect), k
