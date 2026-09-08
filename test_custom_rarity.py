# -*- coding: utf-8 -*-
"""
自定义罕见指数（可选外部数据）的加载、导入与显示测试。

应用只提供接口，不附带任何罕见度数据源——多数数据的授权条款不允许随应用
再分发。数据文件由用户自备、放在用户配置目录，**文件缺席是常态**：所有代码
路径都必须在没有它时正常工作，那是绝大多数用户的状态，不是边界情况。

The app ships no dataset, only this interface; the data file lives in the
user's config directory and is usually ABSENT. Every code path must behave
correctly in that state — it is the norm, not an edge case.
"""
import os
import sqlite3
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def custom_db(tmp_path, monkeypatch):
    """造一个临时罕见指数库并让加载器指向它。"""
    from core import custom_rarity

    path = str(tmp_path / "custom_rarity.db")
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE custom_rarity ("
        "model_class_id INTEGER, scientific_name TEXT, english_name TEXT, "
        "chinese_simplified TEXT, rarity_index REAL)"
    )
    con.executemany(
        "INSERT INTO custom_rarity VALUES (?,?,?,?,?)",
        [
            (1, "Ardea coromanda", "Eastern Cattle Egret", "牛背鹭", 5.29),
            (2, "Hirundo rustica", "Barn Swallow", "家燕", 1.20),
            (3, "Butorides sundevalli", "Galapagos Heron", "加岛绿鹭", 4.57),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(custom_rarity, "_db_path", lambda: path)
    custom_rarity.reset_cache()
    yield path
    custom_rarity.reset_cache()


@pytest.fixture
def no_custom_db(tmp_path, monkeypatch):
    """让加载器指向一个不存在的路径——即开源分发版的状态。"""
    from core import custom_rarity

    monkeypatch.setattr(
        custom_rarity, "_db_path", lambda: str(tmp_path / "absent.db")
    )
    custom_rarity.reset_cache()
    yield
    custom_rarity.reset_cache()


# ── 加载器 / Loader ─────────────────────────────────────────────────────────

def test_lookup_by_chinese_name(custom_db):
    """按中文鸟名查到自定义指数。"""
    from core.custom_rarity import lookup_index

    assert lookup_index("牛背鹭", "Eastern Cattle Egret") == pytest.approx(5.29)


def test_lookup_falls_back_to_english_name(custom_db):
    """中文名对不上时用英文名兜底（英文环境处理的批次没有中文名）。"""
    from core.custom_rarity import lookup_index

    assert lookup_index(None, "Barn Swallow") == pytest.approx(1.20)
    assert lookup_index("", "Barn Swallow") == pytest.approx(1.20)


def test_lookup_returns_none_for_unknown_species(custom_db):
    """查不到的鸟种返回 None，不能瞎给值。"""
    from core.custom_rarity import lookup_index

    assert lookup_index("不存在的鸟", "No Such Bird") is None


def test_lookup_returns_none_without_the_data_file(no_custom_db):
    """
    没有数据文件时安全返回 None —— 这是开源分发版的常态，绝不能抛异常。
    """
    from core.custom_rarity import lookup_index

    assert lookup_index("牛背鹭", "Eastern Cattle Egret") is None


def test_is_available_reflects_the_file(custom_db, tmp_path, monkeypatch):
    """is_available() 如实反映数据在不在，供 UI 决定要不要显示这一段。"""
    from core import custom_rarity

    assert custom_rarity.is_available() is True
    monkeypatch.setattr(custom_rarity, "_db_path",
                        lambda: str(tmp_path / "gone.db"))
    custom_rarity.reset_cache()
    assert custom_rarity.is_available() is False


def test_lookup_is_cached_not_reread_per_photo(custom_db, monkeypatch):
    """
    浏览时每张照片都会查一次，必须走内存缓存而不是反复开库——
    否则翻图会被磁盘 IO 拖慢。
    """
    from core import custom_rarity

    custom_rarity.reset_cache()
    calls = {"n": 0}
    real_connect = sqlite3.connect

    def counting_connect(*a, **k):
        calls["n"] += 1
        return real_connect(*a, **k)

    monkeypatch.setattr(sqlite3, "connect", counting_connect)
    for _ in range(5):
        custom_rarity.lookup_index("牛背鹭", "Eastern Cattle Egret")

    assert calls["n"] == 1, f"应只开库一次，实际 {calls['n']} 次"


# ── 详情面板显示 / Detail panel rendering ───────────────────────────────────

def _rarity_text(panel) -> str:
    """取详情面板罕见度那一行的纯文本（去掉内联图标标签）。"""
    import re
    return re.sub(r"<[^>]+>", "", panel._val_gbif_rarity.text()).strip()


@pytest.fixture
def panel(qt_app):
    from ui.detail_panel import DetailPanel
    from tools.i18n import get_i18n
    p = DetailPanel(get_i18n())
    yield p
    p.close()


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """钉住中文：断言里有「传奇」等中文分档名。"""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def test_detail_panel_shows_both_indices(panel, custom_db):
    """
    有自定义罕见指数数据时，罕见度显示为「分档 (GBIF - 自定义指数)」，
    且自定义指数保留源数据的两位小数。

    自定义指数高度集中在 7-9 之间：全量 10727 种在两位小数下有 572 个不同取值，
    截成一位只剩 87 档，最拥挤的一档就挤了 721 种。丢掉第二位等于丢掉这份
    参考值的大部分区分度。
    """
    panel.show_photo({
        "filename": "DSC_1", "bird_species_cn": "牛背鹭",
        "bird_species_en": "Eastern Cattle Egret",
        "gbif_rarity_100": 91.5,
    })

    text = _rarity_text(panel)
    assert "(91.5 - 5.29)" in text, f"实际显示: {text!r}"


def test_detail_panel_shows_gbif_only_without_custom_data(panel, no_custom_db):
    """
    没有自定义罕见指数数据时（开源分发版的常态）保持原样，只显示 GBIF 分数。
    """
    panel.show_photo({
        "filename": "DSC_1", "bird_species_cn": "牛背鹭",
        "bird_species_en": "Eastern Cattle Egret",
        "gbif_rarity_100": 91.5,
    })

    text = _rarity_text(panel)
    assert "(91.5)" in text, f"实际显示: {text!r}"
    assert "-" not in text.split("(")[-1], f"不该出现分隔符: {text!r}"


def test_detail_panel_shows_gbif_only_for_species_missing_from_dataset(panel, custom_db):
    """有罕见指数库但这个鸟种查不到时，也只显示 GBIF，不留空括号或悬空的 -。"""
    panel.show_photo({
        "filename": "DSC_1", "bird_species_cn": "查无此鸟",
        "bird_species_en": "No Such Bird",
        "gbif_rarity_100": 42.0,
    })

    text = _rarity_text(panel)
    assert "(42.0)" in text, f"实际显示: {text!r}"


def test_detail_panel_unaffected_when_gbif_missing(panel, custom_db):
    """GBIF 没值时整行仍是占位符——自定义指数不能反客为主。"""
    panel.show_photo({
        "filename": "DSC_1", "bird_species_cn": "牛背鹭",
        "bird_species_en": "Eastern Cattle Egret",
        "gbif_rarity_100": None,
    })

    text = _rarity_text(panel)
    assert "5.29" not in text, f"GBIF 缺失时不该单独显示自定义指数: {text!r}"


# ── 分发安全 / Distribution safety ──────────────────────────────────────────

def test_data_file_lives_outside_any_packaged_directory():
    """
    数据文件必须放在用户配置目录，不能放在 birdid/data 下。

    三个 .spec 都以整目录方式打包 birdid/data，.gitignore 拦得住 git 却拦不住
    PyInstaller——只要开发机上有这个文件，它就会被塞进安装包分发给所有用户，
    而这正是当初放弃该功能的原因。
    """
    from core import custom_rarity
    from config import get_app_config_dir

    path = custom_rarity._db_path()
    assert str(get_app_config_dir()) in path, f"应在配置目录下，实际 {path}"
    assert "birdid" not in path, f"不得放在会被打包的 birdid/ 下: {path}"


def test_spec_files_do_not_reference_the_dataset():
    """三个打包配置都不该提到这个可选数据集——它绝不能进安装包。"""
    import glob
    for spec in glob.glob("*.spec"):
        content = open(spec, encoding="utf-8").read()
        assert "custom_rarity" not in content.lower(), f"{spec} 提到了该可选数据集"


# ── 导入 / Importing a distributed database ─────────────────────────────────

def _make_valid_db(path: str, rows=None) -> str:
    """造一个合法的罕见指数库。"""
    rows = rows if rows is not None else [
        (1, "Ardea coromanda", "Eastern Cattle Egret", "牛背鹭", 5.29),
        (2, "Hirundo rustica", "Barn Swallow", "家燕", 1.20),
    ]
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE custom_rarity ("
        "model_class_id INTEGER, scientific_name TEXT, english_name TEXT, "
        "chinese_simplified TEXT, rarity_index REAL)"
    )
    con.executemany("INSERT INTO custom_rarity VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


@pytest.fixture
def install_target(tmp_path, monkeypatch):
    """把安装目标指向临时目录。"""
    from core import custom_rarity
    target = str(tmp_path / "installed" / "custom_rarity.db")
    monkeypatch.setattr(custom_rarity, "_db_path", lambda: target)
    custom_rarity.reset_cache()
    yield target
    custom_rarity.reset_cache()


def test_install_accepts_a_valid_database(tmp_path, install_target):
    """导入合法的罕见指数库：就位并立即可查，无需重启。"""
    from core.custom_rarity import install_database, lookup_index, is_available

    src = _make_valid_db(str(tmp_path / "given.db"))
    ok, reason, count = install_database(src)

    assert (ok, reason, count) == (True, "ok", 2)
    assert os.path.exists(install_target)
    assert is_available() is True
    assert lookup_index("牛背鹭", None) == pytest.approx(5.29)


def test_install_rejects_a_file_that_is_not_a_database(tmp_path, install_target):
    """选错文件（不是 sqlite）时明确拒绝，不留下半个文件。"""
    from core.custom_rarity import install_database

    bogus = tmp_path / "notes.txt"
    bogus.write_text("这不是数据库", encoding="utf-8")

    ok, reason, count = install_database(str(bogus))

    assert ok is False and reason == "not_a_database"
    assert not os.path.exists(install_target)


def test_install_rejects_a_database_without_the_expected_table(tmp_path, install_target):
    """是 sqlite 但结构不对（选到了别的库）→ 拒绝。"""
    from core.custom_rarity import install_database

    other = str(tmp_path / "other.db")
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE photos (id INTEGER)")
    con.commit()
    con.close()

    ok, reason, _ = install_database(other)

    assert ok is False and reason == "missing_table"
    assert not os.path.exists(install_target)


def test_install_rejects_an_empty_database(tmp_path, install_target):
    """表在但没有数据 → 拒绝，免得用户以为装好了却什么都不显示。"""
    from core.custom_rarity import install_database

    empty = _make_valid_db(str(tmp_path / "empty.db"), rows=[])

    ok, reason, _ = install_database(empty)

    assert ok is False and reason == "empty"
    assert not os.path.exists(install_target)


def test_failed_install_keeps_the_existing_data(tmp_path, install_target):
    """
    已经装好数据后又误选了个坏文件，原有数据必须原样保留。

    否则用户一次误操作就把能用的数据弄没了，而这份数据是走私下渠道拿到的，
    不一定还能再要一份。
    """
    from core.custom_rarity import install_database, lookup_index

    install_database(_make_valid_db(str(tmp_path / "good.db")))
    assert lookup_index("牛背鹭", None) == pytest.approx(5.29)

    bogus = tmp_path / "bad.txt"
    bogus.write_text("坏文件", encoding="utf-8")
    ok, reason, _ = install_database(str(bogus))

    assert ok is False
    assert lookup_index("牛背鹭", None) == pytest.approx(5.29), "原有数据不得被破坏"


def test_install_replaces_an_older_database(tmp_path, install_target):
    """导入新版数据应覆盖旧版，并立即以新数据生效。"""
    from core.custom_rarity import install_database, lookup_index

    install_database(_make_valid_db(str(tmp_path / "v1.db")))
    v2 = _make_valid_db(str(tmp_path / "v2.db"),
                        rows=[(1, "Ardea coromanda", "Eastern Cattle Egret", "牛背鹭", 6.66)])

    ok, _, count = install_database(v2)

    assert ok is True and count == 1
    assert lookup_index("牛背鹭", None) == pytest.approx(6.66)


def test_species_count_reports_loaded_size(tmp_path, install_target):
    """供设置页显示「已启用（N 种）」。"""
    from core.custom_rarity import install_database, species_count

    assert species_count() == 0
    install_database(_make_valid_db(str(tmp_path / "g.db")))
    assert species_count() == 2


# ── 设置页入口 / Settings page entry ────────────────────────────────────────

@pytest.fixture
def settings_center(qt_app):
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n
    sc = SettingsCenter(get_i18n())
    yield sc
    sc.close()


def test_settings_page_shows_enabled_state_with_count(settings_center, tmp_path,
                                                       install_target):
    """装好数据后，设置页状态行要显示已启用与鸟种数。"""
    from core.custom_rarity import install_database

    install_database(_make_valid_db(str(tmp_path / "g.db")))
    settings_center._refresh_custom_rarity_status()

    text = settings_center._custom_rarity_status.text()
    assert "2" in text, f"应显示鸟种数，实际: {text!r}"


def test_settings_page_shows_disabled_state_without_data(settings_center,
                                                          install_target):
    """没有数据时状态行要说明未启用，且不显示任何数字。"""
    settings_center._refresh_custom_rarity_status()

    text = settings_center._custom_rarity_status.text()
    assert text.strip(), "状态行不能是空的"


def test_import_button_installs_the_chosen_file(settings_center, tmp_path,
                                                 install_target, monkeypatch):
    """
    点「导入」选中文件后：文件就位、状态行刷新为已启用。
    """
    from PySide6.QtWidgets import QFileDialog
    import ui.settings_center as sc_mod

    src = _make_valid_db(str(tmp_path / "given.db"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (src, "")))
    shown = []
    monkeypatch.setattr(sc_mod.StyledMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a)))

    settings_center._on_import_custom_rarity()

    assert os.path.exists(install_target)
    assert "2" in settings_center._custom_rarity_status.text()
    assert shown, "成功后应给出反馈"


def test_import_rejects_bad_file_with_a_localized_message(settings_center, tmp_path,
                                                           install_target, monkeypatch):
    """
    选错文件时给出可读的中文提示，而不是原因码，也不能留下文件。
    """
    from PySide6.QtWidgets import QFileDialog
    import ui.settings_center as sc_mod

    bogus = tmp_path / "notes.txt"
    bogus.write_text("不是数据库", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bogus), "")))
    warned = []
    monkeypatch.setattr(sc_mod.StyledMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a)))

    settings_center._on_import_custom_rarity()

    assert not os.path.exists(install_target)
    assert warned, "失败必须提示用户"
    msg = " ".join(str(x) for x in warned[0])
    assert "not_a_database" not in msg, f"不该把原因码丢给用户: {msg!r}"


def test_import_cancelled_does_nothing(settings_center, install_target, monkeypatch):
    """用户在选择框点取消 → 什么都不做，不报错。"""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))

    settings_center._on_import_custom_rarity()

    assert not os.path.exists(install_target)
