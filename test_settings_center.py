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


def test_birdid_subnational_region_restore(monkeypatch):
    """
    验证保存了子级地区（如 AU-ACT）后，重新打开设置页时地区下拉能正确恢复。

    这是 C1 回归测试：_restore_birdid_country 原先在 _bid_applying=True 期间调用
    _on_bid_country_changed，但后者因守卫提前返回，导致地区列表为空、保存的子级地区
    无法恢复（始终显示"整个国家"）。修复后直接调用 _populate_bid_regions 绕开守卫。

    Regression test for C1: after saving a sub-national region (e.g. AU-ACT),
    re-opening the settings page must restore the region dropdown correctly.

    Before the fix, _restore_birdid_country called _on_bid_country_changed while
    _bid_applying=True; that method returned early due to the guard, so the region
    list was never populated and the saved sub-region was lost (always showed
    "Entire Country"). After the fix, _populate_bid_regions is called directly,
    bypassing the guard.

    参数 / Parameters:
        monkeypatch: pytest fixture.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        # 预置：Australia 国家 + AU-ACT 地区
        # Pre-set: Australia country + AU-ACT region
        cfg = AdvancedConfig(config_file=tmp_path)
        cfg.config["birdid_country_code"] = "AU"
        cfg.config["birdid_selected_country"] = "澳大利亚"
        cfg.config["birdid_region_code"] = "AU-ACT"
        cfg.config["birdid_selected_region"] = "澳首都直辖区 (AU-ACT)"
        cfg.save()

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        # 打开设置页：此时 _restore_birdid_country 应能正确填充地区并恢复子级地区
        # Open settings page: _restore_birdid_country should populate and restore sub-region
        w = SettingsCenter(get_i18n())
        w.show_page("birdid")

        # 地区下拉项数应 > 1（即除"整个国家"外还有子级地区）
        # Region dropdown must have more than 1 item (sub-regions populated)
        assert w._bid_region.count() > 1, (
            f"Region dropdown was not populated (count={w._bid_region.count()}); "
            "C1 guard bug may still be present."
        )

        # 当前选中的地区文字应包含 AU-ACT
        # The currently selected region text should contain AU-ACT
        current_region_text = w._bid_region.currentText()
        assert "AU-ACT" in current_region_text, (
            f"Expected saved region AU-ACT to be restored, got: '{current_region_text}'"
        )

        w.close()
    finally:
        os.unlink(tmp_path)


def test_birdid_non_top10_country_roundtrip(monkeypatch):
    """
    验证存储了非 top-10 国家（如 FR）后，打开识鸟页不会把 birdid_country_code 覆盖为 None。

    复现路径:
    1. 经 Dock"更多国家"将 birdid_country_code 存为 "FR"。
    2. 打开 SettingsCenter 识鸟页：_restore_birdid_country 因 FR 不在下拉中匹配失败，
       下拉停在 index 0（Auto GPS）。
    3. 用户点 Done → _save_birdid 取到 country_code=None → set_birdid_region 把 FR 覆盖为 None。

    修复后: _restore_birdid_country 动态追加 FR 到下拉并选中; _save_birdid 从下拉取到 FR;
    set_birdid_region 保留 FR。兜底守卫作为第二层保护也可独立拦截。

    Verify that a saved non-top-10 country (e.g. FR) is preserved after opening
    the Bird-ID settings page and clicking Done.

    Reproduction path:
    1. birdid_country_code is stored as "FR" (e.g. via the Dock "More Countries" flow).
    2. SettingsCenter Bird-ID page opens: _restore_birdid_country fails to match FR
       (not in top-10 dropdown), dropdown stays at index 0 (Auto GPS).
    3. User clicks Done → _save_birdid reads country_code=None →
       set_birdid_region overwrites FR with None.

    After fix: _restore_birdid_country dynamically appends FR and selects it;
    _save_birdid reads FR from the dropdown; set_birdid_region keeps FR.
    The fallback guard acts as a second layer of protection.

    参数 / Parameters:
        monkeypatch: pytest fixture.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        # 预置非 top-10 国家 FR（法国）
        # Pre-set non-top-10 country FR (France)
        cfg = AdvancedConfig(config_file=tmp_path)
        cfg.set_birdid_region(True, "FR", "法国", None, "整个国家")

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        # 打开识鸟页并立即保存（模拟用户打开后点 Done 不做任何修改）
        # Open Bird-ID page and immediately save (simulates user opening then clicking Done)
        w = SettingsCenter(get_i18n())
        w.show_page("birdid")

        # 修复后下拉应显示 "法国"，当前项应为 FR
        # After fix, dropdown should show "法国" and current item should be FR
        current_text = w._bid_country.currentText()
        assert current_text == "法国", (
            f"Expected dropdown to show '法国' for FR, got: '{current_text}'"
        )

        w._save_birdid()

        # 核心断言：birdid_country_code 不能被覆盖为 None
        # Core assertion: birdid_country_code must not be overwritten to None
        assert cfg.birdid_country_code == "FR", (
            f"Expected birdid_country_code to remain 'FR', got: {cfg.birdid_country_code!r}"
        )

        w.close()
    finally:
        os.unlink(tmp_path)


# ── Task 5: 输出/视频/外部应用页测试 / Output/Video/Apps page tests ──────────────


def test_output_video_apps_pages_build():
    """
    验证输出、视频、外部应用三个设置页能正确构建，且关键属性存在。

    Verify that the output, video, and apps settings pages build correctly
    and that the key widget attributes are present.
    """
    from ui.settings_center import SettingsCenter
    w = SettingsCenter(get_i18n())
    for key in ("output", "video", "apps"):
        w.show_page(key)
    assert hasattr(w, "_apps_list")   # 外部应用列表存在 / External apps list exists
    w.close()
