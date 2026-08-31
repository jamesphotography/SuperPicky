#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设置中心「关于」页版本查询入口的回归测试。

验证按钮存在、结果展示分支正确，以及官网地址不是已失效的旧域名。
不发起真实网络请求，也不点击触发后台线程。

Regression tests for the Settings Center About page version-check entry.

Verifies the buttons exist, result rendering branches are correct, and the
download URL is not the dead legacy host. No real network requests are made and
no background thread is started.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import config as app_config  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """提供进程级 QApplication。/ Provide a process-wide QApplication."""

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def settings_center(qapp):
    """构建设置中心并切到关于页。

    显式钉住 zh_CN 语言，避免受运行环境的系统语言影响；断言一律与同一个
    i18n 实例比对，因此不依赖具体译文。

    Build the Settings Center on the About page. The locale is pinned to zh_CN
    so the system language cannot affect the run; assertions compare against the
    same i18n instance, so they never depend on a specific translation.
    """

    from ui.settings_center import SettingsCenter
    from tools.i18n import I18n

    center = SettingsCenter(I18n("zh_CN"), start_page="about")
    yield center
    center.deleteLater()


# ── 配置层 / Configuration ───────────────────────────────────────────────────

def test_download_page_is_not_dead_legacy_host() -> None:
    """下载页不得指向已无 DNS 记录的旧域名。

    superpicky.jamesphotography.com.au 已不存在，指向它等于给用户一个死链。
    The legacy host no longer resolves; pointing at it would be a dead link.
    """

    url = app_config.endpoints.UPDATE_DOWNLOAD_PAGE
    assert "jamesphotography.com.au" not in url
    assert url.startswith("https://superpicky.app")


def test_manifest_url_configured() -> None:
    """发布清单地址应可配置且指向官网 JSON。"""

    url = app_config.endpoints.DOWNLOAD_MANIFEST_URL
    assert url.endswith("downloads_github.json")
    assert url.startswith("https://superpicky.app")


# ── 关于页控件 / About page widgets ──────────────────────────────────────────

def test_about_page_has_version_check_controls(settings_center) -> None:
    """关于页必须同时提供「检查更新」按钮与结果标签。"""

    assert settings_center._about_check_btn is not None
    assert settings_center._about_version_result is not None
    assert settings_center._about_check_btn.isEnabled()


def test_about_page_has_visit_site_button(settings_center) -> None:
    """关于页必须有前往官网的按钮（4.3.0 之后一度丢失的入口）。"""

    from PySide6.QtWidgets import QPushButton

    labels = [b.text() for b in settings_center.findChildren(QPushButton)]
    expected = settings_center.i18n.t("update.update_center_btn_visit_site")
    assert expected in labels


# ── 结果展示分支 / Result rendering ──────────────────────────────────────────

def test_result_shows_new_version(settings_center) -> None:
    """有更新时应显示带版本号的提示。"""

    settings_center._on_site_version_checked(True, "4.9.9", "")
    text = settings_center._about_version_result.text()
    assert "4.9.9" in text


def test_result_shows_up_to_date(settings_center) -> None:
    """无更新时应显示已是最新，且不出现版本号误导。"""

    settings_center._on_site_version_checked(False, "4.5.0", "")
    text = settings_center._about_version_result.text()
    assert text == settings_center.i18n.t("update.update_center_result_latest")


def test_result_shows_failure(settings_center) -> None:
    """查询失败时显示失败提示，绝不能显示成有更新。"""

    settings_center._on_site_version_checked(False, "", "network unreachable")
    text = settings_center._about_version_result.text()
    assert text == settings_center.i18n.t("update.update_center_result_failed")


def test_button_reenabled_after_result(settings_center) -> None:
    """结果返回后按钮必须恢复可用，否则只能查一次。"""

    settings_center._about_check_btn.setEnabled(False)
    settings_center._on_site_version_checked(False, "4.5.0", "")
    assert settings_center._about_check_btn.isEnabled()
