# -*- coding: utf-8 -*-
"""
切换到旧版 V1 评星前的警告与取消回滚 (V4.8)。

V1 把同一个数值同时当判废线和达标线，1★/2★ 恒为空集，只产生 0★/3★。
实测 433 张批次在美学阈值 7.0 下 99.7% 判 0★，因此切换前必须告知并可取消。

Tests for the confirmation shown before switching to legacy V1 rating.
"""
import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.settings_center import SettingsCenter  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _center(qapp, monkeypatch, confirmed: bool, skill="custom", aesth=7.0):
    """构造一个只测算法切换逻辑的壳，绕开真实弹窗与真实配置写盘。"""
    sc = SettingsCenter.__new__(SettingsCenter)

    class _I18n:
        def t(self, key, **kw):
            return key

    sc.i18n = _I18n()
    sc._rating_v2 = True
    sc._algo_legacy_checkbox = None
    sc._quota_row_widgets = []
    sc._sharp_row_widgets = []
    sc._nima_row_widgets = []
    monkeypatch.setattr(sc, "_confirm_switch_to_v1", lambda cfg: confirmed,
                        raising=False)
    return sc


def _widget_host(i18n):
    """
    把 _confirm_switch_to_v1 绑到一个真实 QWidget 上。

    QMessageBox(self) 要求 parent 是已初始化的 QWidget，而 SettingsCenter.__init__
    需要完整的配置与主窗口上下文；这里用最小的真实 QWidget 承载被测方法，
    既能真正构造弹窗，又不必拉起整个设置中心。
    """
    import types

    from PySide6.QtWidgets import QWidget

    host = QWidget()
    host.show()          # 必须可见：不可见时 _confirm_switch_to_v1 会直接放行
    host.i18n = i18n
    host._V1_NIMA_WARN_LEVEL = SettingsCenter._V1_NIMA_WARN_LEVEL
    host._confirm_switch_to_v1 = types.MethodType(
        SettingsCenter._confirm_switch_to_v1, host)
    return host


class _Cfg:
    """最小配置替身，记录 set_rating_algorithm 是否被调用。"""

    def __init__(self, skill="custom", aesth=7.0, sharp=600):
        self.skill_level = skill
        self.custom_aesthetics = aesth
        self.custom_sharpness = sharp
        self.saved = None

    def set_rating_algorithm(self, key):
        self.saved = key

    def save(self):
        pass


def test_cancel_keeps_v2(qapp, monkeypatch):
    """用户取消 → 不写配置、维持 V2。"""
    sc = _center(qapp, monkeypatch, confirmed=False)
    cfg = _Cfg()
    monkeypatch.setattr("advanced_config.get_advanced_config", lambda: cfg)
    sc._on_algo_selected("v1")
    assert cfg.saved is None, "取消后不应持久化 rating_algorithm"
    assert sc._rating_v2 is True, "取消后应维持 V2"


def test_confirm_switches_to_v1(qapp, monkeypatch):
    """用户确认 → 正常切到 V1。"""
    sc = _center(qapp, monkeypatch, confirmed=True)
    cfg = _Cfg()
    monkeypatch.setattr("advanced_config.get_advanced_config", lambda: cfg)
    sc._on_algo_selected("v1")
    assert cfg.saved == "v1"
    assert sc._rating_v2 is False


def test_switch_to_v2_never_warns(qapp, monkeypatch):
    """切回 V2 不该弹窗——只有切 V1 才有代价。"""
    sc = _center(qapp, monkeypatch, confirmed=False)   # 若误调用确认框会返回 False
    cfg = _Cfg()
    monkeypatch.setattr("advanced_config.get_advanced_config", lambda: cfg)
    sc._on_algo_selected("v2")
    assert cfg.saved == "v2", "切到 V2 不应被确认框拦截"
    assert sc._rating_v2 is True


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_warning_strings_exist_in_both_locales(locale):
    """中英文案齐全，且高阈值警告带 {nima} 占位符。"""
    with open(f"locales/{locale}.json", encoding="utf-8") as f:
        d = json.load(f)
    s = d["settings"]
    for key in ("culling_algo_v1_warn_title", "culling_algo_v1_warn_body",
                "culling_algo_v1_warn_high", "culling_algo_v1_warn_ok",
                "culling_algo_v1_warn_cancel"):
        assert key in s, f"{locale} 缺少 {key}"
        assert s[key].strip(), f"{locale} 的 {key} 为空"
    assert "{nima}" in s["culling_algo_v1_warn_high"]
    for ph in ("{nima}", "{sharp}", "{advice}"):
        assert ph in s["culling_algo_v1_warn_body"], f"{locale} body 缺占位符 {ph}"


def test_warn_level_threshold_is_below_current_default():
    """警告线必须低于 custom_aesthetics 的可选上限，否则形同虚设。"""
    assert SettingsCenter._V1_NIMA_WARN_LEVEL < 7.0


def test_confirm_dialog_actually_builds(qapp, monkeypatch):
    """
    真实走一遍 _confirm_switch_to_v1（不 mock 它本身），只拦住 exec() 不阻塞。

    这一条是为了覆盖弹窗构造本身：Qt 绑定写错、i18n key 缺失、占位符对不上
    都只会在真正构造 QMessageBox 时才暴露，mock 掉就测不到。
    """
    from PySide6.QtWidgets import QMessageBox

    class _I18n:
        def t(self, key, **kw):
            return key.format(**kw) if "{" in key else f"{key}|{sorted(kw)}"

    sc = _widget_host(_I18n())
    captured = {}

    def fake_exec(self):
        captured["text"] = self.text()
        captured["buttons"] = [b.text() for b in self.buttons()]
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec, raising=False)

    cfg = _Cfg(skill="custom", aesth=7.0, sharp=600)
    result = sc._confirm_switch_to_v1(cfg)

    assert captured, "对话框未被构造"
    assert len(captured["buttons"]) == 2, "应有确认/取消两个按钮"
    # 未点击任何按钮 → clickedButton() 为 None → 保守返回 False（维持 V2）
    assert result is False


def test_high_threshold_triggers_extra_warning(qapp, monkeypatch):
    """美学阈值 7.0 > 5.5 应带上额外警告；5.0 则不带。"""
    from PySide6.QtWidgets import QMessageBox

    seen = []

    def fake_exec(self):
        seen.append(self.text())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec, raising=False)

    class _I18n:
        def t(self, key, **kw):
            if key == "settings.culling_algo_v1_warn_high":
                return "HIGH_WARN"
            if key == "settings.culling_algo_v1_warn_body":
                return f"body|advice={kw.get('advice')}"
            return key

    sc = _widget_host(_I18n())

    sc._confirm_switch_to_v1(_Cfg(aesth=7.0))
    assert "HIGH_WARN" in seen[-1], "高阈值应追加额外警告"

    sc._confirm_switch_to_v1(_Cfg(aesth=5.0))
    assert "HIGH_WARN" not in seen[-1], "阈值不高时不应追加警告"


def test_invisible_widget_skips_dialog_and_never_blocks(qapp, monkeypatch):
    """
    窗口不可见时必须直接放行，绝不构造模态框。

    回归防护：确认框最初无条件 exec()，导致 test_settings_center 里
    `_algo_legacy_checkbox.setChecked(True)` 触发模态弹窗后无人可点，
    整个测试套件挂死 10 分钟。任何程序化切换都不该被弹窗阻塞。
    """
    from PySide6.QtWidgets import QMessageBox, QWidget
    import types

    def boom(self):
        raise AssertionError("不可见时不应构造/执行对话框")

    monkeypatch.setattr(QMessageBox, "exec", boom, raising=False)

    class _I18n:
        def t(self, key, **kw):
            return key

    host = QWidget()          # 刻意不 show()
    host.i18n = _I18n()
    host._V1_NIMA_WARN_LEVEL = SettingsCenter._V1_NIMA_WARN_LEVEL
    host._confirm_switch_to_v1 = types.MethodType(
        SettingsCenter._confirm_switch_to_v1, host)

    assert host._confirm_switch_to_v1(_Cfg(aesth=7.0)) is True
