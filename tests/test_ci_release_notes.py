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
