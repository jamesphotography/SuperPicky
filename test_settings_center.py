# -*- coding: utf-8 -*-
"""
SettingsCenter 骨架测试 — Task 2 TDD

测试左侧导航 QListWidget 6 项 + 右侧 QStackedWidget 页切换。

SettingsCenter skeleton tests — Task 2 TDD

Tests the left-side QListWidget with 6 items + right-side QStackedWidget page switching.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_nav_has_six_pages_and_switch():
    """
    验证导航列表有 6 个分页，且 show_page 能正确切换 stack。

    Verify the nav list has 6 pages and show_page correctly switches the stack.
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER
    w = SettingsCenter(get_i18n())
    assert PAGE_ORDER == ["culling", "birdid", "output", "video", "apps", "about"]
    assert w._nav.count() == 6
    w.show_page("about")
    assert w._stack.currentIndex() == PAGE_ORDER.index("about")
    w.close()


def test_skill_preset_fills_thresholds_and_manual_edit_switches_custom():
    """
    验证精选页协同逻辑:
    1. 选技能等级预设 → 阈值滑块被自动填充为该档对应值
    2. 手动拖动阈值滑块 → 档位切换到"自定义"

    Verify culling page coordination logic:
    1. Selecting a skill preset fills the threshold sliders with that level's values.
    2. Manually adjusting a threshold slider switches the level to "custom".

    注意:brief 假设 get_skill_level_thresholds 返回含键名的字典且存在 "advanced" 档位,
    但实际 API 返回 Tuple[int, float] 且档位为 beginner/intermediate/master/custom。
    此处用 "master"(最严格档)代替 "advanced",并用元组下标 th[0]/th[1] 访问。

    Note: The brief assumed a dict return with "advanced" level; actual API returns
    Tuple[int, float] and levels are beginner/intermediate/master/custom.
    We use "master" (strictest) in place of "advanced", and access via th[0]/th[1].
    """
    from ui.settings_center import SettingsCenter
    from core.skill_presets import get_skill_level_thresholds

    w = SettingsCenter(get_i18n())
    w.show_page("culling")

    # 选 master 预设档 → 阈值滑块被填为该档值 / Select master preset → sliders filled
    th = get_skill_level_thresholds("master")  # returns (sharpness: int, aesthetics: float)
    w._on_skill_preset_selected("master")
    assert w._cull_sharp.value() == int(th[0])
    # Fix C: 补断言 NIMA 滑块也被填充 / Also assert NIMA slider was filled
    assert w._cull_nima.value() == int(round(th[1] * 10))  # 5.5 → 55

    # 手动改阈值 → 档位切到自定义 / Manual change → switches to custom
    w._cull_sharp.setValue(w._cull_sharp.value() + 30)
    assert w._current_skill_key == "custom"

    w.close()


def test_save_roundtrip_no_truncation():
    """
    验证保存往返无截断:锐度/NIMA/AI 置信度在上限值时写入 advanced_config 后读回完整。

    Verify save roundtrip without truncation: sharpness/NIMA/AI confidence at
    ceiling values survive a write-read cycle through advanced_config without clamp loss.
    """
    import tempfile, os
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    # 使用临时配置文件隔离,避免污染真实用户配置
    # Use a temporary config file to isolate from real user config
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        # 预填入非默认值,确保 settings center 初始化不越界
        cfg.config["min_sharpness"] = 400
        cfg.config["min_nima"] = 5.0
        cfg.config["min_confidence"] = 0.5

        w = SettingsCenter(get_i18n())
        w.show_page("culling")

        # 手动拨到上限值 / Set sliders to ceiling values
        w._cull_sharp.setValue(600)   # max sharpness
        w._cull_nima.setValue(70)     # max NIMA: 70/10 = 7.0
        w._cull_ai.setValue(70)       # max confidence: 70/100 = 0.7

        # 调用内部保存方法写回临时 cfg 实例
        # Patch the global cfg used by _save_culling to our temp instance
        import advanced_config as _ac_mod
        _orig = _ac_mod.get_advanced_config
        _ac_mod.get_advanced_config = lambda: cfg
        try:
            w._save_culling()
        finally:
            _ac_mod.get_advanced_config = _orig

        # 断言无截断 / Assert no truncation
        assert cfg.min_sharpness == 600, f"Expected 600, got {cfg.min_sharpness}"
        assert cfg.min_nima == 7.0, f"Expected 7.0, got {cfg.min_nima}"
        assert cfg.min_confidence == 0.7, f"Expected 0.7, got {cfg.min_confidence}"

        w.close()
    finally:
        os.unlink(tmp_path)


# ── Task 4: 识鸟页测试 / Bird-ID page tests ────────────────────────────────────


def test_birdid_page_reads_and_writes_config(monkeypatch):
    """
    验证识鸟页能读取 advanced_config 并正确写回自动识鸟开关。

    Verify the Bird-ID settings page reads advanced_config and correctly
    writes back the auto-identify toggle.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    # 使用临时配置文件隔离，避免污染真实用户配置
    # Use a temporary config file to avoid polluting real user config
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        cfg.config["birdid_auto_identify"] = False
        cfg.save()

        # monkeypatch global get_advanced_config to return our temp instance
        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("birdid")
        w._bid_auto.setChecked(True)
        w._save_birdid()
        assert cfg.birdid_auto_identify is True, (
            f"Expected True, got {cfg.birdid_auto_identify}"
        )
        w.close()
    finally:
        os.unlink(tmp_path)


def test_birdid_confidence_roundtrip(monkeypatch):
    """
    验证识鸟置信度滑块往返无截断：在 clamp 范围 30-95 内写入并读回。

    Verify the Bird-ID confidence slider round-trips without truncation:
    write and read back values within the clamp range 30-95.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        cfg.config["birdid_confidence"] = 50
        cfg.save()

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("birdid")

        # 设置到上限 95，验证 clamp 不截断
        # Set to ceiling 95, assert no clamp truncation
        w._bid_conf.setValue(95)
        w._save_birdid()
        assert cfg.birdid_confidence == 95, (
            f"Expected 95, got {cfg.birdid_confidence}"
        )

        # 设置到下限 30，验证 clamp 不截断
        # Set to floor 30, assert no clamp truncation
        w._bid_conf.setValue(30)
        w._save_birdid()
        assert cfg.birdid_confidence == 30, (
            f"Expected 30, got {cfg.birdid_confidence}"
        )

        w.close()
    finally:
        os.unlink(tmp_path)


def test_birdid_region_save(monkeypatch):
    """
    验证识鸟页 _save_birdid 调用 set_birdid_region 写回数据源选择。

    Verify that _save_birdid calls set_birdid_region to persist the
    data-source (eBird / GBIF) selection.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        cfg.config["birdid_use_ebird"] = True
        cfg.save()

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("birdid")

        # 切换到 GBIF 并保存
        # Switch to GBIF source and save
        w._bid_gbif.setChecked(True)
        w._save_birdid()
        assert cfg.birdid_use_ebird is False, (
            f"Expected False (GBIF selected), got {cfg.birdid_use_ebird}"
        )

        w.close()
    finally:
        os.unlink(tmp_path)
