# -*- coding: utf-8 -*-
"""
SettingsCenter 测试:导航(2026-11 起为 常用/高级/实验室/关于 四页)、页面切换、
旧页键映射、各小节控件读写与跨页联动。

SettingsCenter tests: navigation (four pages since 2026-11: common /
advanced / labs / about), page switching, legacy page-key mapping, section
widget read/write and cross-page coordination.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_nav_has_four_pages_and_switch():
    """
    验证导航列表为 常用/高级/实验室/关于 四页,且 show_page 能正确切换 stack。

    2026-11 设置中心整合(spec D1):原 精选/识鸟/输出/视频/外部应用 五页拆成小节,
    重新拼装进「常用」「高级」两页,视频移入实验室。

    Verify the nav lists the four pages Common / Advanced / Labs / About and
    show_page switches the stack (settings consolidation, spec D1).
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER
    w = SettingsCenter(get_i18n())
    assert PAGE_ORDER == ["common", "advanced", "labs", "about"]
    assert w._nav.count() == 4
    assert w._stack.count() == 4
    # 默认停在「常用」/ Opens on "common" by default
    assert w._stack.currentIndex() == PAGE_ORDER.index("common")
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
        cfg.config["birdid_use_geo_filter"] = True
        cfg.save()

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("birdid")

        # 切换到 GBIF 并保存
        # Switch to GBIF source and save
        w._bid_gbif.setChecked(True)
        w._save_birdid()
        assert cfg.birdid_use_geo_filter is False, (
            f"Expected False (GBIF selected), got {cfg.birdid_use_geo_filter}"
        )

        w.close()
    finally:
        os.unlink(tmp_path)


def _settings_with_region(monkeypatch, country_code, country_name, region_code, region_name):
    """
    用临时配置构造设置中心并打开识鸟页 / Build SettingsCenter on a temp config.

    参数 / Parameters:
        monkeypatch: pytest fixture.
        country_code (str): 国家代码 / Country code.
        country_name (str): 国家显示名 / Country display name.
        region_code (Optional[str]): 省州代码 / Subnational code.
        region_name (str): 省州显示名 / Subnational display name.

    返回 / Returns:
        tuple: (SettingsCenter, AdvancedConfig)
    """
    import tempfile

    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    cfg = AdvancedConfig(config_file=tmp_path)
    cfg.config["birdid_country_code"] = country_code
    cfg.config["birdid_selected_country"] = country_name
    cfg.config["birdid_region_code"] = region_code
    cfg.config["birdid_selected_region"] = region_name
    cfg.save()
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    w = SettingsCenter(get_i18n())
    w.show_page("birdid")
    return w, cfg


def test_birdid_subnational_region_restored(monkeypatch):
    """
    澳洲显示 8 个州且恢复已保存的 AU-ACT（老配置恢复生效，spec §2.5）。

    Australia lists its 8 states and restores the saved AU-ACT, so region codes
    stored by older versions work again.
    """
    w, cfg = _settings_with_region(monkeypatch, "AU", "澳大利亚", "AU-ACT", "澳大利亚首都特区")
    assert w._bid_region.count() == 9, f"应为「整个国家」+ 8 个州，实际 {w._bid_region.count()}"
    assert not w._bid_region.isHidden() and not w._bid_region_label.isHidden()
    assert w._bid_region.currentData() == "AU-ACT"
    assert cfg.birdid_country_code == "AU"
    w.close()


def test_birdid_region_row_hidden_without_subnational(monkeypatch):
    """
    非中澳美国家没有省州，地区行整行隐藏 / The row hides for countries without subnational data.
    """
    w, _cfg = _settings_with_region(monkeypatch, "JP", "日本", None, "整个国家")
    assert w._bid_region.count() == 1
    assert w._bid_region.isHidden() and w._bid_region_label.isHidden()
    w.close()


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


# ── Task 6: 关于页测试 / About page tests ────────────────────────────────────


def test_about_page_shows_version():
    """
    验证关于页含有 APP_VERSION 版本号文本。

    Verify the About page contains the APP_VERSION version string
    in at least one QLabel.
    """
    from ui.settings_center import SettingsCenter
    from constants import APP_VERSION
    import PySide6.QtWidgets as _qw

    w = SettingsCenter(get_i18n())
    w.show_page("about")
    texts = [c.text() for c in w.findChildren(_qw.QLabel)]
    assert any(str(APP_VERSION) in t for t in texts), (
        f"APP_VERSION '{APP_VERSION}' not found in any QLabel text. "
        f"Found texts: {texts}"
    )
    w.close()


# ── Task 5: 输出/视频/外部应用页测试 / Output/Video/Apps page tests ──────────────


def test_scroll_areas_have_transparent_background():
    """
    回归测试：每个设置页的 QScrollArea 及其内容容器必须显式设为透明背景。

    根因背景：macOS 原生 QStyle 下，QScrollArea 的 viewport 不会继承祖先
    QDialog 的 QSS 背景色——系统外观为浅色模式时会掉回原生浅灰 #ececec，
    与应用深色主题不符（用户反馈"设置页面背景淡白色"）。offscreen 测试平台
    不会复现这个原生渲染细节，所以这里只做静态断言：确认每个 QScrollArea
    自身样式表包含 "background: transparent"，防止未来重构时又漏掉。
    真实渲染下的修复前/修复后对照见开发记录（forced Qt.ColorScheme.Light +
    真实 cocoa 平台插件采样视口像素）。

    Regression test: every settings page's QScrollArea and its inner content
    container must explicitly set a transparent background.

    Root cause: under the native macOS QStyle, a QScrollArea's viewport does
    not inherit the ancestor QDialog's QSS background — it falls back to the
    native light gray #ececec when the system appearance is Light, clashing
    with the app's dark theme (user-reported "settings page has a pale white
    background"). The offscreen test platform can't reproduce this native
    rendering quirk, so this only asserts the static property: each
    QScrollArea's own stylesheet contains "background: transparent", guarding
    against this being dropped again in a future refactor. The real
    before/after pixel comparison (forced Qt.ColorScheme.Light + the real
    cocoa platform plugin) lives in the dev record, not in this offscreen test.
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER
    from PySide6.QtWidgets import QScrollArea

    w = SettingsCenter(get_i18n())
    for key in PAGE_ORDER:
        w.show_page(key)
    scroll_areas = w.findChildren(QScrollArea)
    assert scroll_areas, "预期设置中心里应有 QScrollArea"
    for sa in scroll_areas:
        assert "background: transparent" in sa.styleSheet(), (
            f"QScrollArea 缺少透明背景样式，macOS 浅色模式下会露出原生浅灰: "
            f"{sa.styleSheet()!r}"
        )
    w.close()


def test_output_video_apps_pages_build():
    """
    验证输出、视频、外部应用设置页能正确构建，且关键属性存在。

    视频页在 4.5.0-4.6 之间一直没有入口，其构建代码因此长期未被执行；恢复
    入口的同时把它纳回本测试，确保页面真能建起来、总开关控件存在，而不是
    加回 PAGE_ORDER 后一点开就崩。

    The video page had no entry point for several releases, so its build code
    went unexercised; cover it here now that the page is reachable again.
    """
    from ui.settings_center import SettingsCenter
    w = SettingsCenter(get_i18n())
    for key in ("output", "video", "apps"):
        w.show_page(key)
    assert hasattr(w, "_apps_list")   # 外部应用列表存在 / External apps list exists
    assert hasattr(w, "_video_auto_check"), "视频页的主流程总开关控件缺失"
    w.close()


# ── Final-review fixes 测试 / Final-review fix tests ─────────────────────────


def test_culling_default_values_no_drift(monkeypatch):
    """
    C2 回归测试：用默认配置打开精选页，不改任何滑块直接 _save_culling()，
    写回后 min_sharpness/min_nima 不能因滑块下限 clamp 而漂移。

    C2 regression test: open culling page with default config, call
    _save_culling() without touching any slider, and assert that
    min_sharpness and min_nima have NOT drifted from their stored defaults.

    修复前: setRange(200,600) 会将 setValue(100) clamp 到 200，
    _save_culling 无条件回写 → 默认用户触发即把 100→200。
    修复后: setRange(100,600) 使 setValue(100) 可以无损保留。

    Before fix: setRange(200,600) clamped setValue(100) to 200;
    _save_culling wrote back unconditionally → defaults silently drifted.
    After fix: setRange(100,600) preserves setValue(100) faithfully.
    """
    import tempfile
    import os
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        # 使用纯默认配置 / Use pure default config
        cfg = AdvancedConfig(config_file=tmp_path)
        default_sharpness = AdvancedConfig.DEFAULT_CONFIG["min_sharpness"]   # 100
        default_nima = AdvancedConfig.DEFAULT_CONFIG["min_nima"]             # 3.5

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("culling")

        # 不触碰任何滑块，直接保存 / Save without touching any slider
        w._save_culling()

        assert cfg.min_sharpness == default_sharpness, (
            f"min_sharpness drifted: expected {default_sharpness}, got {cfg.min_sharpness}"
        )
        assert abs(cfg.min_nima - default_nima) < 0.01, (
            f"min_nima drifted: expected {default_nima}, got {cfg.min_nima}"
        )
        w.close()
    finally:
        os.unlink(tmp_path)


def test_custom_skill_writes_custom_fields(monkeypatch):
    """
    I1 回归测试：精选档为 custom 时，_save_culling 应同步写回 custom_sharpness
    和 custom_aesthetics，确保 CLI 路径读到最新值而非陈旧缓存。

    I1 regression test: when skill_level is "custom", _save_culling must
    also persist custom_sharpness and custom_aesthetics so the CLI path
    does not read stale cached values.
    """
    import tempfile
    import os
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        # 设置为自定义档 / Set to custom skill level
        cfg.set_skill_level("custom")
        cfg.save()

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("culling")

        # 强制档位为 custom，修改锐度到 450，NIMA 到 50 (=5.0) / Force custom, set sharpness=450, NIMA=50
        w._current_skill_key = "custom"
        w._cull_sharp.setValue(450)
        w._cull_nima.setValue(50)   # 50/10 = 5.0，在 set_custom_aesthetics clamp 范围 4.0-7.0 内
        w._save_culling()

        assert cfg.custom_sharpness == 450, (
            f"custom_sharpness not written back: expected 450, got {cfg.custom_sharpness}"
        )
        # custom_aesthetics 应等于 NIMA 滑块当前值 / 10 = 5.0
        # custom_aesthetics should equal NIMA slider value / 10 = 5.0
        assert abs(cfg.custom_aesthetics - 5.0) < 0.05, (
            f"custom_aesthetics not written back: expected ~5.0, got {cfg.custom_aesthetics}"
        )
        w.close()
    finally:
        os.unlink(tmp_path)


def test_algo_legacy_toggle_switches_config_and_slider_visibility():
    """
    验证「高级选项」区的旧版评星算法复选框:v2 初始态配额行可见/旧滑块隐藏、
    复选框未勾选;勾选后配置即时落盘为 v1、旧滑块可见/配额行隐藏;取消勾选恢复 v2。

    (评星算法从原来的两张大卡片降级为「高级」折叠区里的一个复选框——普通用户
    不再面对"算法选择",只看到 技能档 + 3星配额 + 检测开关。)

    Verify the legacy rating-algorithm checkbox in the "Advanced" disclosure:
    under v2 the quota row is visible, the legacy sliders hidden, and the
    checkbox unchecked; checking it persists "v1" immediately (legacy sliders
    shown / quota hidden); unchecking restores v2. (The algorithm switch was
    demoted from two large cards to one checkbox in the Advanced section so
    ordinary users no longer face an "algorithm choice".)
    """
    import tempfile
    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    _orig_get = _ac_mod.get_advanced_config
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        assert cfg.rating_algorithm == "v2"  # 默认 v2 / default stays v2
        # settings_center 内部均为方法内局部 import,调用时解析到补丁后的符号
        # settings_center uses in-method imports, resolved at call time
        _ac_mod.get_advanced_config = lambda: cfg

        w = SettingsCenter(get_i18n())
        w.show_page("culling")

        # v2 初始:配额行可见,旧阈值滑块隐藏,复选框未勾选 / v2 initial state
        assert not w._cull_quota.isHidden()
        assert w._cull_sharp.isHidden() and w._cull_nima.isHidden()
        assert not w._algo_legacy_checkbox.isChecked()

        # 勾选旧版复选框 → 即时落盘 v1 + 可见性互换 / check legacy box
        w._algo_legacy_checkbox.setChecked(True)
        assert cfg.rating_algorithm == "v1"
        assert AdvancedConfig(config_file=tmp_path).rating_algorithm == "v1"  # 已写盘
        assert w._cull_quota.isHidden()
        assert not w._cull_sharp.isHidden() and not w._cull_nima.isHidden()

        # 取消勾选 → 恢复 v2 / uncheck restores v2
        w._algo_legacy_checkbox.setChecked(False)
        assert cfg.rating_algorithm == "v2"
        assert not w._cull_quota.isHidden()
        assert w._cull_sharp.isHidden() and w._cull_nima.isHidden()
        w.close()
    finally:
        _ac_mod.get_advanced_config = _orig_get
        os.unlink(tmp_path)


def test_immediate_save_no_done_needed(monkeypatch):
    """
    验证统一即时保存模型:精选/识鸟/输出页的控件改动无需点"完成"就已落盘。
    直接用第二个 AdvancedConfig 实例读同一文件,证明写盘发生在控件回调而非
    _on_done/_save_*。

    Verify the unified immediate-save model: control changes on the culling /
    birdid / output pages persist to disk without clicking "Done". A second
    AdvancedConfig instance reads the same file to prove the write happened in
    the control callback, not in _on_done/_save_*.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())

        # 精选页:改 AI 置信度 → 立即从磁盘可读到新值(未点完成)
        w.show_page("culling")
        w._cull_ai.setValue(63)
        assert AdvancedConfig(config_file=tmp_path).min_confidence == 0.63

        # 识鸟页:改置信度 → 立即落盘
        w.show_page("birdid")
        w._bid_conf.setValue(88)
        assert AdvancedConfig(config_file=tmp_path).birdid_confidence == 88

        # 输出页:改删除确认 → 立即落盘
        w.show_page("output")
        w._delete_confirm.setChecked(False)
        assert AdvancedConfig(config_file=tmp_path).delete_confirm is False

        w.close()
    finally:
        os.unlink(tmp_path)


def test_name_format_and_more_countries_present(monkeypatch):
    """
    验证补齐的两处识鸟页 UI:鸟名显示格式下拉存在且改动即时落盘;国家下拉含
    「更多国家」入口(MORE 伪代码在 country_list 中)。

    Verify the two restored Bird-ID UI pieces: the name-format dropdown exists
    and persists immediately on change; the country dropdown includes the "more
    countries" entry (the MORE pseudo-code is present in country_list).
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        w.show_page("birdid")

        # 鸟名格式下拉:切到 clements 后即时落盘 / name-format persists immediately
        idx = w._bid_name_format.findData("clements")
        assert idx >= 0
        w._bid_name_format.setCurrentIndex(idx)
        assert AdvancedConfig(config_file=tmp_path).name_format == "clements"

        # 「更多国家」入口存在 / "more countries" entry present
        assert "MORE" in w._bid_country_list.values()

        w.close()
    finally:
        os.unlink(tmp_path)


def test_clear_cache_removes_cache_keeps_originals_and_clears_db(monkeypatch, tmp_path):
    """
    验证「清理所有预览缓存」按钮的三条不变量:
      1. 只删除 .superpicky/cache 目录;
      2. 原始照片文件不受影响;
      3. report.db 里指向缓存的路径字段被清空（clear_cache_paths 生效）。

    这是本批设置中心新功能里唯一的破坏性操作（shutil.rmtree），且 DB 清理
    逻辑被 `except Exception: pass` 包裹——一旦 clear_cache_paths 方法名/签名
    发生回归会静默失败、无人察觉。本测试同时守住「破坏范围」与「静默失败」两
    个风险；插入后先断言字段非空，确保第 3 条断言检验的是清空动作本身生效，
    而非字段本来就为 NULL。

    Verify the three invariants of the "Clear all preview caches" button:
      1. only the .superpicky/cache directory is removed;
      2. original photo files are left untouched;
      3. the report.db cache path columns are cleared (clear_cache_paths works).

    This is the only destructive action (shutil.rmtree) among the batch's new
    Settings Center features, and the DB cleanup is wrapped in
    `except Exception: pass` — a regression in clear_cache_paths' name/signature
    would fail silently. This test guards both the blast radius and the silent
    failure. A pre-assert that the column is non-null before clearing ensures the
    third assertion tests the clearing action itself, not a column already NULL.
    """
    import sqlite3

    from ui.settings_center import SettingsCenter
    from ui import custom_dialogs
    from tools.report_db import ReportDB

    directory = str(tmp_path)

    # 原图:不该被删 / original photo: must survive
    original = tmp_path / "photo.jpg"
    original.write_bytes(b"JPEGDATA")

    # 缓存目录 + 假缓存文件:该被删 / cache dir + fake cache file: must be removed
    cache_dir = tmp_path / ".superpicky" / "cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "preview_001.jpg"
    cache_file.write_bytes(b"CACHE")

    # report.db:写一条带缓存路径字段的记录 / DB row carrying a cache path column
    db = ReportDB(directory)
    db.insert_photo({"filename": "photo.jpg", "temp_jpeg_path": str(cache_file)})
    db.close()

    db_path = tmp_path / ".superpicky" / "report.db"

    def _read_temp_jpeg_path() -> object:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT temp_jpeg_path FROM photos WHERE filename = 'photo.jpg'"
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    # 前置:字段确实非空,否则第 3 条断言等于没测 / pre-assert: column non-null
    assert _read_temp_jpeg_path() == str(cache_file)

    # 弹窗:确认返回 Yes,信息/警告框静默(offscreen 下避免 exec 阻塞)
    # dialogs: confirm returns Yes; info/warning silenced (avoid exec under offscreen)
    monkeypatch.setattr(
        custom_dialogs.StyledMessageBox, "question",
        lambda *a, **k: custom_dialogs.StyledMessageBox.Yes,
    )
    monkeypatch.setattr(custom_dialogs.StyledMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(custom_dialogs.StyledMessageBox, "warning", lambda *a, **k: None)

    w = SettingsCenter(get_i18n())
    # 设置中心从父窗口取 directory_path;测试用轻量 stub 提供该属性
    # Settings Center reads directory_path off the parent window; stub it here.
    monkeypatch.setattr(w, "parent", lambda: type("_Host", (), {"directory_path": directory})())

    w._on_clear_cache_clicked()

    # 1. 缓存目录被删 / cache dir removed
    assert not cache_dir.exists()
    # 2. 原图仍在 / original survives
    assert original.exists() and original.read_bytes() == b"JPEGDATA"
    # 3. DB 缓存路径字段被清空 / DB cache path column cleared
    assert _read_temp_jpeg_path() is None

    w.close()


def test_video_toggle_reachable_and_persists(monkeypatch):
    """
    视频页的主流程总开关必须可达且能存下去。

    4.5.0 的 ExtremeSimple 一次摘掉了三个视频入口，但主流程代码与
    video_auto_process_in_main 守卫都留着：升级前开过的老用户照常在用，
    没开过的人却再也打不开——功能活着，却没有任何开关碰得到它。
    本测试锁定入口恢复后这条链路真的通：打开视频页 → 勾选 → 保存 →
    配置写入 True，而不是给了个点了没反应的假开关。

    The main-flow video toggle must be reachable and actually persist.
    Stripping every entry point left the feature alive but unreachable;
    this pins that enabling it from the settings page really writes through.
    """
    import tempfile
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        # 默认必须是关的——恢复入口不等于默认开启，不拍视频的用户不受影响
        # Restoring the entry point must not turn the feature on by default.
        assert cfg.config.get("video_auto_process_in_main") is False

        import advanced_config as _ac_mod
        monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)

        w = SettingsCenter(get_i18n())
        # 视频页已并入实验室,旧页键 "video" 映射到 labs
        # The video page now lives in Labs; the legacy "video" key maps there.
        w.show_page("video")
        assert w._stack.currentIndex() == PAGE_ORDER_FOR_TEST().index("labs")

        w._video_auto_check.setChecked(True)
        w._save_video()

        assert cfg.config["video_auto_process_in_main"] is True, (
            "视频页勾选后未写回配置——开关点了没反应"
        )
        w.close()
    finally:
        os.path.exists(tmp_path) and os.unlink(tmp_path)


def PAGE_ORDER_FOR_TEST():
    """取当前 PAGE_ORDER（局部导入，避免与文件顶部的导入风格冲突）。"""
    from ui.settings_center import PAGE_ORDER
    return PAGE_ORDER


# ── 2026-11 四页整合 / Four-page consolidation ───────────────────────────────

# 旧页键 → 新页键(与 ui.settings_center._LEGACY_PAGE_KEY 对照)
# Legacy page key → new page key (mirrors ui.settings_center._LEGACY_PAGE_KEY)
_EXPECTED_LEGACY = {
    "culling": "common",
    "birdid": "common",
    "output": "common",
    "video": "labs",
    "apps": "advanced",
}

# 搬家后必须仍然存在、且只建一份的关键控件
# Key widgets that must still exist, built exactly once, after the move
_KEY_WIDGETS = (
    "_cull_flight", "_cull_burst", "_cull_burst_fps", "_cull_rescue", "_cull_ai",
    "_bid_auto", "_bid_conf", "_bid_country", "_folder_layout_combo", "_raw_embed",
    "_keep_temp_files", "_completion_sound", "_delete_confirm", "_apps_list",
    "_close_action_combo",
)


def _page_of(w, widget) -> int:
    """
    返回控件所在设置页在 stack 中的序号,不在任何页里返回 -1。

    参数:
    w: SettingsCenter 实例
    widget: 要定位的控件

    返回:
    int: 页序号或 -1

    Return the stack index of the page containing ``widget`` (-1 if none).
    """
    for i in range(w._stack.count()):
        page = w._stack.widget(i)
        if widget is page or page.isAncestorOf(widget):
            return i
    return -1


def test_legacy_page_keys_land_on_new_pages():
    """
    还在用旧页键(culling/birdid/output/video/apps)的调用方,打开后必须落到正确
    的新页,不能停在第一页不动;构造参数 start_page 与 _rebuild_pages 同样适用。

    Callers still using a legacy page key must land on the mapped new page —
    via show_page, the start_page constructor argument and _rebuild_pages.
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER, _LEGACY_PAGE_KEY

    assert _LEGACY_PAGE_KEY == _EXPECTED_LEGACY
    w = SettingsCenter(get_i18n(), start_page="about")
    for old, new in _EXPECTED_LEGACY.items():
        w.show_page("about")
        w.show_page(old)
        assert w._stack.currentIndex() == PAGE_ORDER.index(new), old
    w._rebuild_pages("apps")
    assert w._stack.currentIndex() == PAGE_ORDER.index("advanced")
    w.close()

    w2 = SettingsCenter(get_i18n(), start_page="video")
    assert w2._stack.currentIndex() == PAGE_ORDER.index("labs")
    w2.close()


def test_key_widgets_exist_once_on_expected_pages(monkeypatch):
    """
    搬家后关键控件都在,且每个小节只建一次(建两次会留下一份没人读写的孤儿控件,
    兜底保存只认最后那份)。同时核对控件落在规定的页上。

    After the move every key widget exists, each section is built exactly once
    (a second build would leave an orphan copy), and widgets sit on the
    pages the spec assigns.
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER

    sections = ("_sec_skill", "_sec_rating", "_sec_detect_basic", "_sec_detect_extra",
                "_sec_ai_confidence", "_build_format_filter_section",
                "_sec_birdid_basic", "_sec_birdid_extra", "_sec_output_basic",
                "_sec_output_metadata", "_sec_storage", "_sec_video", "_sec_apps")
    calls = {name: 0 for name in sections}
    for name in sections:
        orig = getattr(SettingsCenter, name)

        def _counting(self, *a, _orig=orig, _name=name, **k):
            calls[_name] += 1
            return _orig(self, *a, **k)

        monkeypatch.setattr(SettingsCenter, name, _counting)

    w = SettingsCenter(get_i18n())
    assert calls == {name: 1 for name in sections}, calls
    for attr in _KEY_WIDGETS:
        assert hasattr(w, attr), attr
        assert _page_of(w, getattr(w, attr)) >= 0, f"{attr} 不在任何页上 / on no page"

    common, advanced, labs = (PAGE_ORDER.index(k) for k in ("common", "advanced", "labs"))
    for attr in ("_cull_flight", "_cull_burst", "_bid_auto", "_bid_country",
                 "_bid_name_format", "_folder_layout_combo", "_completion_sound",
                 "_delete_confirm"):
        assert _page_of(w, getattr(w, attr)) == common, attr
    for attr in ("_cull_quota", "_cull_ai", "_cull_burst_fps", "_cull_rescue", "_bid_conf",
                 "_raw_embed", "_keep_temp_files", "_apps_list", "_close_action_combo"):
        assert _page_of(w, getattr(w, attr)) == advanced, attr
    for attr in ("_lab_multi_cb", "_video_auto_check"):
        assert _page_of(w, getattr(w, attr)) == labs, attr
    w.close()


def test_burst_toggle_on_common_drives_fps_on_advanced():
    """
    跨页联动:常用页取消「连拍检测」后,高级页「连拍速度」整行置灰;重新勾选恢复。

    Cross-page link: unchecking burst on Common disables the burst-speed row
    on Advanced; re-checking enables it again.
    """
    from ui.settings_center import SettingsCenter

    w = SettingsCenter(get_i18n())
    w._cull_burst.setChecked(True)
    assert all(x.isEnabled() for x in w._burst_fps_row_widgets)
    w._cull_burst.setChecked(False)
    assert w._burst_fps_row_widgets
    assert not any(x.isEnabled() for x in w._burst_fps_row_widgets)
    w._cull_burst.setChecked(True)
    assert all(x.isEnabled() for x in w._burst_fps_row_widgets)
    w.close()


def test_skill_preset_on_common_drives_rating_on_advanced_then_done():
    """
    常用页的档位卡片联动高级页的配额条(V2)或锐度/美学滑块(V1),随后 done()
    兜底保存不报错。_on_skill_preset_selected 与 _save_birdid 都直接读其他页的
    控件,只有四页在同一轮里全部建好时才安全——本测试锁住这一前提。

    A skill preset on Common updates the quota bar (V2) / V1 sliders on
    Advanced, and done() then flushes without error. Both handlers read
    widgets on other pages, which is only safe because all pages are built
    in one pass; this pins that.
    """
    from advanced_config import get_advanced_config
    from core.rating_quota import get_quota3_for_skill
    from core.skill_presets import get_skill_level_thresholds
    from ui.settings_center import SettingsCenter

    cfg = get_advanced_config()
    w = SettingsCenter(get_i18n())
    w._on_skill_preset_selected("master")
    assert w._current_skill_key == "master"
    assert w._cull_quota.quota3() == int(get_quota3_for_skill("master", cfg))
    sharp, _nima = get_skill_level_thresholds("master")
    assert w._cull_sharp.value() == int(sharp)
    w._bid_conf.setValue(77)          # 识鸟进阶控件触发 _save_birdid / triggers _save_birdid
    w.done(0)
    assert cfg.skill_level == "master"
    assert cfg.birdid_confidence == 77
    w.close()


def test_restore_defaults_rebuilds_four_pages(monkeypatch):
    """
    「恢复默认」后 _rebuild_pages 重建的是四个新页,关闭时兜底保存不会把旧值写回。

    After Restore Defaults the four new pages are rebuilt, so the close-time
    flush cannot write the old values back.
    """
    from advanced_config import AdvancedConfig, get_advanced_config
    from ui.settings_center import SettingsCenter, PAGE_ORDER

    cfg = get_advanced_config()
    default_sound = AdvancedConfig.DEFAULT_CONFIG["completion_sound_enabled"]
    w = SettingsCenter(get_i18n())
    w._completion_sound.setChecked(not default_sound)
    assert cfg.completion_sound_enabled is (not default_sound)

    cfg.restore_defaults()
    w._rebuild_pages("about")
    _app.processEvents()
    assert w._stack.currentIndex() == PAGE_ORDER.index("about")
    assert w._nav.count() == 4
    assert w._stack.count() == 4
    assert w._completion_sound.isChecked() is default_sound
    w.done(0)
    assert cfg.completion_sound_enabled is default_sound
    with open(cfg.config_file, encoding="utf-8") as f:
        import json
        assert json.load(f)["completion_sound_enabled"] is default_sound


def test_language_picker_removed_from_settings_center():
    """
    界面语言只留在菜单栏「设置 → 语言」(spec D4),设置中心不再有语言下拉。

    The UI language lives only in the menu bar (spec D4); the Settings Center
    no longer has a language combo.
    """
    from ui.settings_center import SettingsCenter

    w = SettingsCenter(get_i18n())
    assert not hasattr(w, "_lang_combo")
    assert not hasattr(SettingsCenter, "_build_language_picker")
    assert not hasattr(SettingsCenter, "_on_language_changed")
    w.close()


def test_common_headings_and_advanced_collapsibles():
    """
    常用页有「选片 / 识鸟 / 整理与提示」三个小节标题;高级页是五个可折叠小节,
    只有「评星」默认展开,点标题可展开/收起。

    Common has the Culling / Bird ID / Organizing headings; Advanced has five
    collapsible sections, only Rating expanded by default, toggled by click.
    """
    from PySide6.QtWidgets import QLabel, QPushButton
    from ui.settings_center import SettingsCenter, PAGE_ORDER

    i18n = get_i18n()
    w = SettingsCenter(i18n)
    common = w._stack.widget(PAGE_ORDER.index("common"))
    labels = [lb.text() for lb in common.findChildren(QLabel)]
    for key in ("settings.sec_culling", "settings.sec_birdid", "settings.sec_organize"):
        assert i18n.t(key) in labels, key

    titles = [i18n.t(k) for k in ("settings.sec_rating", "settings.sec_birdid",
                                  "settings.sec_output", "settings.sec_apps",
                                  "settings.sec_storage")]
    advanced = w._stack.widget(PAGE_ORDER.index("advanced"))
    buttons = [b for b in advanced.findChildren(QPushButton)
               if b.property("collapsibleHeader")]
    assert [b.text().split(" ", 1)[1].strip() for b in buttons] == titles
    assert [b.isChecked() for b in buttons] == [True, False, False, False, False]
    assert buttons[0].text().startswith("▾") and buttons[1].text().startswith("▸")

    # 收起的小节内容不可见,点开后可见 / collapsed content hidden until expanded
    w.show()
    w.show_page("advanced")
    _app.processEvents()
    assert not w._apps_list.isVisible()
    buttons[3].click()
    _app.processEvents()
    assert w._apps_list.isVisible()
    assert buttons[3].text().startswith("▾")
    w.close()


def test_focus_arb_spin_range_save_and_v1_disable():
    """
    验证高级页「脱焦判定锐度线」控件:范围 300-800、步长 10、初值取配置;
    setValue 立即写盘;切到 V1 后置灰,切回 V2 恢复可用。

    Verify the Advanced-page out-of-focus sharpness-line spinbox: range
    300-800, step 10, initial value from config; setValue persists
    immediately; disabled under V1, enabled again under V2.
    """
    import tempfile
    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    _orig_get = _ac_mod.get_advanced_config
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        _ac_mod.get_advanced_config = lambda: cfg
        w = SettingsCenter(get_i18n())
        spin = w._focus_arb_spin
        assert spin.minimum() == 300 and spin.maximum() == 800
        assert spin.singleStep() == 10
        assert spin.value() == int(cfg.v2_focus_arbitration_sharpness)
        assert spin.isEnabled()

        spin.setValue(450)
        assert cfg.v2_focus_arbitration_sharpness == 450.0
        assert AdvancedConfig(config_file=tmp_path).v2_focus_arbitration_sharpness == 450.0

        w._algo_legacy_checkbox.setChecked(True)
        assert not spin.isEnabled()
        w._algo_legacy_checkbox.setChecked(False)
        assert spin.isEnabled()
        w.close()
    finally:
        _ac_mod.get_advanced_config = _orig_get
        os.unlink(tmp_path)
