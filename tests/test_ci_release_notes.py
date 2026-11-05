# -*- coding: utf-8 -*-
"""
CI 发布说明截取测试 / Tests for extracting the newest release notes.

验证 scripts/ci_release.py 的 extract_latest_notes 只保留 ChangeLog 最新版本的
中英两段，不把历史版本带进 GitHub Release 正文。

Verifies that extract_latest_notes in scripts/ci_release.py keeps only the newest
version's English and Chinese sections of the changelog.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_ci_release():
    """
    按路径加载 scripts/ci_release.py / Load scripts/ci_release.py by path.
    """

    spec = importlib.util.spec_from_file_location("ci_release", ROOT / "scripts" / "ci_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_release = _load_ci_release()

SAMPLE = """# SuperPicky 4.7.0

English notes.

## What's new

- Item

---

# SuperPicky 4.7.0（中文）

中文说明。

---

# SuperPicky 4.6.4

Old notes.
"""


def test_keeps_english_and_chinese_of_newest_version() -> None:
    """最新版本的中英两段都保留，旧版本被截掉 / Newest EN + ZH kept, older dropped."""

    notes = ci_release.extract_latest_notes(SAMPLE)
    assert "# SuperPicky 4.7.0\n" in notes
    assert "# SuperPicky 4.7.0（中文）" in notes
    assert "中文说明。" in notes
    assert "4.6.4" not in notes
    assert "Old notes." not in notes


def test_trailing_separator_removed() -> None:
    """末尾的 --- 分隔线和空行被去掉 / Trailing rule and blank lines removed."""

    notes = ci_release.extract_latest_notes(SAMPLE)
    assert notes.endswith("中文说明。\n")


def test_rc_versions_are_distinct() -> None:
    """RC12 与 RC11 视为不同版本 / RC12 and RC11 are different versions."""

    text = "# SuperPicky 4.6.4 RC12\n\nA\n\n# SuperPicky 4.6.4 RC12（中文）\n\n甲\n\n# SuperPicky 4.6.4 RC11\n\nB\n"
    notes = ci_release.extract_latest_notes(text)
    assert "甲" in notes
    assert "RC11" not in notes


def test_second_level_headings_do_not_cut() -> None:
    """「## 」小标题不会截断 / "## " subheadings never cut the section."""

    notes = ci_release.extract_latest_notes(SAMPLE)
    assert "## What's new" in notes


def test_no_heading_returns_text_unchanged() -> None:
    """没有一级标题时原样返回 / Text without top-level headings is returned as is."""

    assert ci_release.extract_latest_notes("just text\n") == "just text\n"


def test_real_changelog_has_only_one_version() -> None:
    """仓库里真实 ChangeLog 截出来只含一个版本 / Real ChangeLog yields one version."""

    text = (ROOT / "ChangeLog.md").read_text(encoding="utf-8")
    notes = ci_release.extract_latest_notes(text)
    keys = {
        ci_release.changelog_heading_key(line)
        for line in notes.splitlines()
        if ci_release.changelog_heading_key(line) is not None
    }
    assert len(keys) == 1
    assert len(notes) < len(text)


# ── 下载清单 / Download list ─────────────────────────────────────────────────

REPO = "jamesphotography/SuperPicky"

# 与 v4.6.4 Release 上的真实资产一致（含手工上传的 Intel 包）。
# Matches the real v4.6.4 release assets (including the hand-built Intel dmg).
ASSETS_464 = [
    {"name": "code_patch_v4.6.4.zip", "size": 37000000},
    {"name": "patch_meta.json", "size": 200},
    {"name": "SuperPicky_Setup_FullCUDA13_Win64_v4.6.4_8f4f379-2.bin", "size": 830624997},
    {"name": "SuperPicky_Setup_FullCUDA13_Win64_v4.6.4_8f4f379-1.bin", "size": 1396485120},
    {"name": "SuperPicky_Setup_FullCUDA13_Win64_v4.6.4_8f4f379.exe", "size": 3514455},
    {"name": "SuperPicky_Setup_FullCUDA_Win64_v4.6.4_8f4f379.exe", "size": 2029173118},
    {"name": "SuperPicky_Setup_Full_Win64_v4.6.4_8f4f379.exe", "size": 765416305},
    {"name": "SuperPicky_v4.6.4_arm64_8f4f379.dmg", "size": 794165260},
    {"name": "SuperPicky_v4.6.4_x86_64_3a338a9.dmg", "size": 800000000},
]


def _line_with(section: str, text: str) -> str:
    """返回含 text 的那一行 / Return the line containing text."""

    matches = [line for line in section.splitlines() if text in line]
    assert len(matches) == 1, (text, matches)
    return matches[0]


def test_download_list_links_every_installer_once() -> None:
    """每个安装包恰好出现一次、链接指向本 tag；内部文件不进清单。"""

    section = ci_release.render_download_section(ASSETS_464, "v4.6.4", REPO)
    base = f"https://github.com/{REPO}/releases/download/v4.6.4/"
    for asset in ASSETS_464:
        name = asset["name"]
        if name.endswith((".dmg", ".exe", ".bin")):
            assert section.count(f"({base}{name})") == 1, name
        else:
            assert name not in section
    assert "Other files" not in section


def test_download_list_order_and_cuda13_parts() -> None:
    """顺序：Mac M → Intel → CPU → CUDA 11.8 → CUDA 13；分卷按 exe、-1、-2。"""

    section = ci_release.render_download_section(ASSETS_464, "v4.6.4", REPO)
    order = ["_arm64_", "_x86_64_", "_Setup_Full_Win64_", "_Setup_FullCUDA_Win64_",
             "FullCUDA13_Win64_v4.6.4_8f4f379.exe", "-1.bin", "-2.bin"]
    positions = [section.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "  - [SuperPicky_Setup_FullCUDA13" in section


def test_download_list_sizes_match_github_style() -> None:
    """大小用十进制，与此前手写的发布页一致。"""

    section = ci_release.render_download_section(ASSETS_464, "v4.6.4", REPO)
    assert _line_with(section, "_arm64_").endswith("· 794 MB")
    assert _line_with(section, "FullCUDA_Win64").endswith("· 2.03 GB")
    assert _line_with(section, "8f4f379.exe) · 3.5").endswith("· 3.5 MB")
    assert _line_with(section, "-1.bin").endswith("· 1.40 GB")


def test_missing_intel_differs_for_rc_and_stable() -> None:
    """没有 Intel 包：测试版写「本测试版不提供」，正式版写「稍后提供」。"""

    assets = [a for a in ASSETS_464 if "_x86_64_" not in a["name"]]
    rc = ci_release.render_download_section(assets, "v4.6.5-rc1", REPO)
    stable = ci_release.render_download_section(assets, "v4.6.5", REPO)
    assert "本测试版不提供" in _line_with(rc, "Mac (Intel)")
    assert "稍后提供" in _line_with(stable, "Mac (Intel)")


def test_failed_optional_build_leaves_no_line() -> None:
    """可选的 CUDA 包没上传时，清单里没有那一行，不会出现死链。"""

    assets = [a for a in ASSETS_464 if "FullCUDA" not in a["name"]]
    section = ci_release.render_download_section(assets, "v4.6.4", REPO)
    assert "CUDA" not in section
    assert "_Setup_Full_Win64_" in section


def test_unknown_installer_is_listed_not_dropped() -> None:
    """认不出分组的安装包放进「其他文件」，不静默丢掉。"""

    assets = ASSETS_464 + [{"name": "SuperPicky_Setup_Lite_Win64_v4.6.4.exe", "size": 5000000}]
    section = ci_release.render_download_section(assets, "v4.6.4", REPO)
    assert "Other files" in section
    assert "SuperPicky_Setup_Lite_Win64_v4.6.4.exe" in section


def test_add_downloads_cli_appends_after_notes(tmp_path) -> None:
    """命令行：读 gh 输出的 JSON，把清单接在说明后面，中文不乱码。"""

    import json
    import subprocess
    import sys

    notes = tmp_path / "notes.md"
    notes.write_text("# SuperPicky 4.6.4\n\n中文说明。\n", encoding="utf-8")
    assets_json = tmp_path / "assets.json"
    assets_json.write_text(json.dumps({"assets": ASSETS_464}), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_release.py"), "add-downloads",
         "--notes", str(notes), "--assets-json", str(assets_json),
         "--tag", "v4.6.4", "--repo", REPO, "--output", str(notes)],
        check=True, capture_output=True,
    )
    body = notes.read_text(encoding="utf-8")
    assert body.startswith("# SuperPicky 4.6.4\n\n中文说明。\n\n## Download / 下载\n")
    assert "任何电脑（CPU 版）" in body
