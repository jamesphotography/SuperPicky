# -*- coding: utf-8 -*-
"""打包清单含 enhance 权重 / packaging manifest includes enhance weights."""
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


def test_win_fallback_has_enhance_weights():
    names = _fallback_filenames("build_release_win.py")
    assert "svdlut.pth" in names and "scunet_color_real.pth" in names


def test_mac_fallback_has_enhance_weights():
    names = _fallback_filenames("build_release_mac.py")
    assert "svdlut.pth" in names and "scunet_color_real.pth" in names
