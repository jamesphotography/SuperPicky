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
    # 4.6: "video" 页恢复——ExtremeSimple 当初摘掉了全部三个视频入口，导致
    # 主流程视频处理只剩配置能开(老用户升级后仍在跑，新用户永远开不了)，
    # 且「参数设置可开启」的日志指向一个不存在的入口。首页开关维持剥离状态。
    # 4.6: the "video" page is back — stripping every entry point left the
    # main-flow video processing reachable only by a pre-existing config value.
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


def test_birdid_subnational_region_hidden(monkeypatch):
    """
    验证无州级数据时地区下拉整行隐藏，且旧配置里的 region_code 不致报错。

    地理数据源改为 GBIF 1°网格后，本设计不再提供州/省级分区（GPS 已精确到 1°，
    手选地区只在无 GPS 时作国家级回退，见 spec §3.3）。因此地区下拉恒只有
    「整个国家」一项，应整行隐藏而非展示一个无意义的空下拉。

    本用例取代原先的 C1 回归测试（该测试断言州级下拉被填充，守护的功能已随
    数据源变更被有意移除）。旧配置中残留的 AU-ACT 等 region_code 必须能被安全
    忽略，不得抛异常。

    After switching the geo data source to the GBIF 1-degree grid, this design no
    longer provides sub-national divisions (GPS already resolves to 1 degree;
    manual selection is only a country-level fallback for photos without GPS —
    see spec section 3.3). The region dropdown therefore only ever holds "Entire
    country" and the whole row is hidden.

    This replaces the former C1 regression test, whose asserted behaviour
    (sub-national dropdown population) was deliberately removed along with the
    data source. Leftover region_codes such as AU-ACT in older configs must be
    ignored safely without raising.

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

        # 无州级数据 → 下拉只有「整个国家」一项，且整行隐藏
        # No sub-national data → only "Entire country", and the row is hidden
        assert w._bid_region.count() == 1, (
            f"地区下拉应只有「整个国家」一项，实际 {w._bid_region.count()} 项"
        )
        assert not w._bid_region.isVisible(), "无州级数据时地区下拉应隐藏"
        assert not w._bid_region_label.isVisible(), "无州级数据时地区标签应隐藏"

        # 旧配置里的 AU-ACT 被安全忽略：当前选中项回落到「整个国家」(itemData=None)
        # The stale AU-ACT is ignored safely: selection falls back to "Entire country"
        assert w._bid_region.currentData() is None, (
            f"旧 region_code 应被忽略，实际 currentData={w._bid_region.currentData()!r}"
        )

        # 国家本身仍必须正确恢复 / The country itself must still restore correctly
        assert cfg.birdid_country_code == "AU", "国家选择不应被地区行的变更影响"

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
        w.show_page("video")
        assert w._stack.currentIndex() == PAGE_ORDER_FOR_TEST().index("video")

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
