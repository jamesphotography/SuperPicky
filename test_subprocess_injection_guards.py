# -*- coding: utf-8 -*-
"""
外部命令调用的注入防护回归测试。

背景 / Background:
    两类写法曾把用户可控的路径/文件名交给解释器处理，属于同一类缺陷：

    1. ui/results_browser_window._move_to_trash 曾用 f-string 把路径插值进
       AppleScript 源，且只转义双引号、不转义反斜杠。文件名中的 \\" 组合会提前
       闭合字符串，其余部分作为 AppleScript 代码执行（实测可执行任意表达式），
       而该函数执行的正是删除操作。
    2. tools/file_utils 的 attrib 回退路径曾带 shell=True。经 cmd.exe 转发时
       路径被 list2cmdline 拼成字符串，含 & 或 | 且不含空格的路径不会被加引号，
       cmd 会把后半段当作另一条命令执行；含 % 的路径还会被变量展开而损坏。

    两处都已改为「静态脚本 + argv 传参」和「不经 shell 直接 exec」。本测试用
    静态源码检查把这两条约定钉住——这类缺陷极易在后续「顺手简化」时复发，而
    行为测试需要 macOS + Finder 自动化授权，无法在 CI(Linux) 上运行。

    Both fixes are pinned by static source inspection here, because behavioural
    tests would need macOS with Finder automation permission and cannot run in
    CI. These defects are easy to reintroduce while "simplifying" the code.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent


def _function_source(module_path: Path, func_name: str) -> str:
    """
    从模块源码中取出指定顶层函数的源文本。

    参数 / Parameters:
        module_path: 模块文件路径。/ Module file path.
        func_name: 顶层函数名。/ Top-level function name.

    返回 / Returns:
        str: 该函数的源代码文本。/ Source text of that function.
    """

    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"未找到函数 {func_name} / function not found")


# ── AppleScript：路径必须走 argv，不得插值进脚本源 ─────────────────────────


def test_move_to_trash_never_interpolates_path_into_applescript() -> None:
    """
    _move_to_trash 不得用 f-string/format/% 拼接 AppleScript 源。

    只要脚本源是静态常量、路径经 argv 传入，文件名里的任何字符都无法改变
    脚本结构，注入即不可能发生。
    """

    src = _function_source(
        REPO_ROOT / "ui" / "results_browser_window.py", "_move_to_trash"
    )
    tree = ast.parse(src)

    # 找到赋给 script 的表达式，必须是纯字面量拼接（无任何动态插值）
    script_nodes = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "script" for t in node.targets
        )
    ]
    assert script_nodes, "未找到 script 赋值 / no script assignment found"

    for value in script_nodes:
        for sub in ast.walk(value):
            assert not isinstance(sub, ast.JoinedStr), (
                "AppleScript 源不得使用 f-string 插值 / no f-string in script"
            )
            assert not (
                isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mod)
            ), "AppleScript 源不得使用 % 格式化 / no %-formatting in script"
            assert not (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "format"
            ), "AppleScript 源不得使用 .format() / no .format() in script"


def test_move_to_trash_passes_path_through_argv() -> None:
    """脚本必须从 argv 取路径，且 osascript 调用带 -- 分隔符。"""

    src = _function_source(
        REPO_ROOT / "ui" / "results_browser_window.py", "_move_to_trash"
    )
    assert "on run argv" in src, "AppleScript 应定义 run argv 处理器"
    assert "item 1 of argv" in src, "路径应取自 argv"
    assert '"--"' in src, "osascript 调用应带 -- 分隔符，避免路径被当作选项"


def test_move_to_trash_still_rejects_directories() -> None:
    """保留只删文件的守卫，防止误删目录。/ Keep the file-only guard."""

    src = _function_source(
        REPO_ROOT / "ui" / "results_browser_window.py", "_move_to_trash"
    )
    assert "os.path.isfile" in src, "必须保留 isfile 守卫"
    assert "normpath" in src, "必须保留 normpath 规范化"


# ── Windows attrib：不得经 shell 转发 ─────────────────────────────────────


@pytest.mark.parametrize(
    "func_name", ["hide_path", "unhide_path", "clear_readonly_attribute"]
)
def test_attrib_calls_never_use_shell(func_name: str) -> None:
    """
    file_utils 中调用 attrib 的回退路径不得带 shell=True。

    attrib.exe 是独立可执行文件，无需 cmd.exe；经 shell 转发会让路径中的
    & | % 被解释。
    """

    path = REPO_ROOT / "tools" / "file_utils.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            target = node
            break
    assert target is not None, (
        f"{func_name} 不存在——若已重命名请同步更新本测试，不要让它静默跳过 / "
        f"{func_name} missing; update this test rather than silently skipping"
    )

    for call in ast.walk(target):
        if not isinstance(call, ast.Call):
            continue
        func_repr = ast.dump(call.func)
        if "subprocess" not in func_repr and "run" not in func_repr:
            continue
        for kw in call.keywords:
            if kw.arg == "shell":
                truthy = isinstance(kw.value, ast.Constant) and bool(kw.value.value)
                assert not truthy, (
                    f"{func_name} 中的 subprocess 调用不得使用 shell=True"
                )


def test_attrib_calls_keep_timeout() -> None:
    """attrib 调用必须保留 timeout，避免外部命令挂起阻塞调用方。"""

    source = (REPO_ROOT / "tools" / "file_utils.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = 0
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        args_dump = ast.dump(call)
        if "'attrib'" not in args_dump and '"attrib"' not in args_dump:
            continue
        found += 1
        assert any(kw.arg == "timeout" for kw in call.keywords), (
            "attrib 调用必须带 timeout"
        )
    assert found >= 2, f"预期至少 2 处 attrib 调用，实际 {found}"
