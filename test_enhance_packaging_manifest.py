# -*- coding: utf-8 -*-
"""
打包清单 / packaging manifest 与 enhance 权重的关系。

scripts/download_models.py 的 MODELS_TO_DOWNLOAD 仍完整登记 enhance 两个权重
(svdlut.pth/scunet_color_real.pth)——功能实现文件保留不动，未来要恢复 Enhance
只需把 "enhance" 加回 main() 的 feature 集合。但 ExtremeSimple 剥离 Enhance 后，
build_release_win.py/build_release_mac.py 的 load_required_models() fallback 列表
不应再要求这两个文件必须存在，否则离线/解析失败时的兜底路径会误判"缺失"。

MODELS_TO_DOWNLOAD in scripts/download_models.py still fully registers both
enhance weights (svdlut.pth/scunet_color_real.pth) — the feature's
implementation files stay untouched; restoring Enhance later just means
re-adding "enhance" to main()'s feature set. But since ExtremeSimple stripped
Enhance, the load_required_models() fallback list in
build_release_win.py/build_release_mac.py must no longer require these two
files, or the offline/parse-failure fallback path would wrongly report them
missing.
"""
import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_download_manifest_has_enhance_weights():
    mod = importlib.import_module("scripts.download_models")
    names = {m["filename"] for m in mod.MODELS_TO_DOWNLOAD}
    assert "svdlut.pth" in names
    assert "scunet_color_real.pth" in names


def _fallback_filenames(build_file: str) -> set:
    """从 build 脚本 load_required_models 的 fallback 列表提取文件名(静态解析)。"""
    tree = ast.parse((ROOT / build_file).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_required_models":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    for k, v in zip(sub.keys, sub.values):
                        if isinstance(k, ast.Constant) and k.value == "filename" \
                                and isinstance(v, ast.Constant):
                            names.add(v.value)
    return names


def test_win_fallback_excludes_enhance_weights():
    """
    ExtremeSimple 剥离 Enhance 后，fallback 清单不应再要求这两个权重存在，
    否则 ensure_models() 会因为它们"永远缺失"而误判构建失败(真实复现过一次:
    v4.5.0-rc1 打包就是这样挂的)。
    """
    names = _fallback_filenames("build_release_win.py")
    assert "svdlut.pth" not in names and "scunet_color_real.pth" not in names


def test_mac_fallback_excludes_enhance_weights():
    """同上，Mac 构建脚本的 fallback 清单同理。"""
    names = _fallback_filenames("build_release_mac.py")
    assert "svdlut.pth" not in names and "scunet_color_real.pth" not in names
