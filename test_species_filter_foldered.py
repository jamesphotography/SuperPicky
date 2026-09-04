# -*- coding: utf-8 -*-
"""鸟种筛选列表只列「有目录的鸟种」+「其他鸟类」兜底。

Species filter lists only foldered species, plus an "other birds" catch-all.

背景 / Background:
主整理只给 2★以上的照片建鸟种目录（core/folder_layout.py 明确规定低星一律
归「其他鸟类」，即使已识别出鸟种）。于是只有低星照片的鸟种（如那张 0★ 的
塞舌尔花蜜鸟）在磁盘上没有任何目录，却仍出现在结果浏览器的鸟种下拉里——
用户每次都要拿它跟磁盘核对一遍，是纯干扰项。

改法：下拉只列「有 2★以上照片」的鸟种，另加一个「其他鸟类」条目兜住剩下的
照片（无鸟种的 + 只有低星的鸟种），保证一张照片都不会从鸟种维度消失。

Only species with 2★+ photos get a folder on disk, yet the browser listed
every species in the DB. The dropdown now lists foldered species plus an
"other birds" entry so no photo becomes unreachable.
"""
from tools.report_db import ReportDB, SPECIES_FILTER_OTHER


def _seed(tmp_path) -> ReportDB:
    """造一份贴近真实卡的数据。

    粉顶果鸠：3★ + 0★ 都有 → 磁盘上有目录
    塞舌尔花蜜鸟：只有 0★     → 磁盘上没有目录（本次要从下拉里剔除的）
    未识别照片：无鸟种字段

    Rose-crowned Fruit Dove has 2★+ photos (foldered); the Seychelles
    Sunbird only has a 0★ photo (never foldered); plus unidentified shots.
    """
    db = ReportDB(str(tmp_path))
    db.insert_photo({"filename": "dove_3star", "rating": 3, "bird_species_cn": "粉顶果鸠"})
    db.insert_photo({"filename": "dove_2star", "rating": 2, "bird_species_cn": "粉顶果鸠"})
    db.insert_photo({"filename": "dove_0star", "rating": 0, "bird_species_cn": "粉顶果鸠"})
    db.insert_photo({"filename": "sunbird_0star", "rating": 0, "bird_species_cn": "塞舌尔花蜜鸟"})
    db.insert_photo({"filename": "nobird_1", "rating": 1})
    db.insert_photo({"filename": "nobird_2", "rating": 0})
    return db


def test_species_without_folder_is_not_listed(tmp_path):
    """只有低星照片的鸟种不进下拉——磁盘上根本没有它的目录。

    A species with no 2★+ photo has no folder, so it must not be listed.
    """
    db = _seed(tmp_path)
    try:
        listed = db.get_distinct_species(foldered_only=True)
        assert "粉顶果鸠" in listed
        assert "塞舌尔花蜜鸟" not in listed, "只有 0★ 的鸟种不该出现在下拉里"
    finally:
        db.close()


def test_other_birds_filter_catches_unfoldered_photos(tmp_path):
    """「其他鸟类」必须兜住无鸟种的 + 只有低星的鸟种，且不误收有目录的鸟种。

    The catch-all must cover unidentified photos and unfoldered species,
    without swallowing photos of foldered species.
    """
    db = _seed(tmp_path)
    try:
        rows = db.get_photos_by_filters({"bird_species_cn": SPECIES_FILTER_OTHER})
        names = {r["filename"] for r in rows}
        assert names == {"sunbird_0star", "nobird_1", "nobird_2"}, f"实际: {names}"
    finally:
        db.close()


def test_every_photo_reachable_from_the_dropdown(tmp_path):
    """完备性：各鸟种条目 + 「其他鸟类」必须不重不漏地覆盖全部照片。

    Completeness: the foldered species entries plus the catch-all must
    partition the whole library — no photo counted twice, none missing.
    """
    db = _seed(tmp_path)
    try:
        seen: list = []
        for sp in db.get_distinct_species(foldered_only=True):
            seen += [r["filename"] for r in db.get_photos_by_filters({"bird_species_cn": sp})]
        seen += [r["filename"] for r in
                 db.get_photos_by_filters({"bird_species_cn": SPECIES_FILTER_OTHER})]

        all_names = [r["filename"] for r in db.get_photos_by_filters({})]
        assert sorted(seen) == sorted(all_names), "下拉各项未能不重不漏覆盖全部照片"
    finally:
        db.close()


def test_foldered_filter_combines_with_rating_filter(tmp_path):
    """foldered_only 与既有的 ratings 参数叠加，保留「随星级动态刷新」行为。

    foldered_only must compose with the existing ratings parameter.
    """
    db = _seed(tmp_path)
    try:
        # 只看 0★ 时：粉顶果鸠有 0★ 照片且它有目录 → 仍列出；塞舌尔无目录 → 不列
        listed = db.get_distinct_species(foldered_only=True, ratings=[0])
        assert listed == ["粉顶果鸠"], f"实际: {listed}"
    finally:
        db.close()


# ----------------------------------------------------------------------
#  UI 层：下拉里必须出现「其他鸟类」条目
#  UI: the dropdown must carry an "other birds" entry
# ----------------------------------------------------------------------
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_dropdown_offers_other_birds_entry():
    """下拉除各鸟种外必须有「其他鸟类」条目，其 data 为哨兵值。

    The dropdown must offer an "other birds" entry carrying the sentinel,
    otherwise unfoldered photos become unreachable by species.
    """
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    i18n = get_i18n()
    i18n.switch_language("zh_CN")
    panel = FilterPanel(i18n)

    panel.update_species_list(["粉顶果鸠", "棕胸金鹃"], has_other=True)

    data = [panel.species_combo.itemData(i) for i in range(panel.species_combo.count())]
    assert SPECIES_FILTER_OTHER in data, f"下拉缺少「其他鸟类」条目: {data}"
    # 兜底项排在最后，不能插在鸟种中间
    assert data[-1] == SPECIES_FILTER_OTHER, "「其他鸟类」应排在列表末尾"


def test_dropdown_hides_other_entry_when_nothing_to_catch():
    """当前筛选下没有无目录照片时不显示该条目，避免点进去是空的。

    Hide the catch-all when the current filter leaves nothing for it.
    """
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    i18n = get_i18n()
    i18n.switch_language("zh_CN")
    panel = FilterPanel(i18n)

    panel.update_species_list(["粉顶果鸠"], has_other=False)

    data = [panel.species_combo.itemData(i) for i in range(panel.species_combo.count())]
    assert SPECIES_FILTER_OTHER not in data, "没有兜底照片时不该显示该条目"


# ----------------------------------------------------------------------
#  合并模式（多目录）必须与单库语义一致
#  Merged (multi-directory) mode must match the single-DB semantics
# ----------------------------------------------------------------------
def _seed_merged(tmp_path):
    """两个子目录：粉顶果鸠在 dir1 有 3★、在 dir2 只有 0★；塞舌尔只在 dir2 且 0★。

    「有目录」按跨目录并集判定：粉顶果鸠在 dir1 有目录，所以它在 dir2 的
    低星照片也归到粉顶果鸠条目下，不进兜底项——两边判据必须一致，划分才不重不漏。

    A species foldered in any directory counts as foldered overall; both the
    dropdown and the catch-all use that same rule.
    """
    from tools.merged_report_db import MergedReportDB
    d1, d2 = tmp_path / "day1", tmp_path / "day2"
    d1.mkdir(); d2.mkdir()

    db1 = ReportDB(str(d1))
    db1.insert_photo({"filename": "d1_dove_3star", "rating": 3, "bird_species_cn": "粉顶果鸠"})
    db1.insert_photo({"filename": "d1_nobird", "rating": 1})
    db1.close()

    db2 = ReportDB(str(d2))
    db2.insert_photo({"filename": "d2_dove_0star", "rating": 0, "bird_species_cn": "粉顶果鸠"})
    db2.insert_photo({"filename": "d2_sunbird_0star", "rating": 0, "bird_species_cn": "塞舌尔花蜜鸟"})
    db2.close()

    return MergedReportDB(str(tmp_path), [str(d1), str(d2)])


def test_merged_mode_lists_only_foldered_species(tmp_path):
    """合并模式下拉同样只列有目录的鸟种。

    Merged mode lists only foldered species too.
    """
    mdb = _seed_merged(tmp_path)
    listed = mdb.get_distinct_species(foldered_only=True)
    assert listed == ["粉顶果鸠"], f"实际: {listed}"


def test_merged_mode_other_birds_partition(tmp_path):
    """合并模式的兜底项同样不重不漏，且不吞掉有目录鸟种的低星照片。

    The merged catch-all must not swallow low-star photos of a foldered species.
    """
    mdb = _seed_merged(tmp_path)
    rows = mdb.get_photos_by_filters({"bird_species_cn": SPECIES_FILTER_OTHER})
    names = {r["filename"] for r in rows}
    assert names == {"d1_nobird", "d2_sunbird_0star"}, f"实际: {names}"

    seen = []
    for sp in mdb.get_distinct_species(foldered_only=True):
        seen += [r["filename"] for r in mdb.get_photos_by_filters({"bird_species_cn": sp})]
    seen += list(names)
    all_names = [r["filename"] for r in mdb.get_photos_by_filters({})]
    assert sorted(seen) == sorted(all_names), "合并模式下未能不重不漏覆盖"


# ----------------------------------------------------------------------
#  浏览器粘合逻辑：决定下拉内容与是否需要兜底项
#  Browser glue: decide dropdown contents and whether the catch-all is needed
# ----------------------------------------------------------------------
def test_dropdown_contents_include_other_when_unfoldered_exist(tmp_path):
    """库里有无目录照片时，兜底项必须出现。

    The catch-all must appear when unfoldered photos exist.
    """
    from ui.results_browser_window import compute_dropdown_species

    db = _seed(tmp_path)
    try:
        species, has_other = compute_dropdown_species(db, use_en=False, ratings=None)
        assert species == ["粉顶果鸠"]
        assert has_other is True
    finally:
        db.close()


def test_dropdown_has_no_other_entry_when_everything_is_foldered(tmp_path):
    """所有照片都属于有目录的鸟种时，不显示兜底项。

    No catch-all when every photo belongs to a foldered species.
    """
    from ui.results_browser_window import compute_dropdown_species

    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo({"filename": "a", "rating": 3, "bird_species_cn": "粉顶果鸠"})
        db.insert_photo({"filename": "b", "rating": 0, "bird_species_cn": "粉顶果鸠"})
        species, has_other = compute_dropdown_species(db, use_en=False, ratings=None)
        assert species == ["粉顶果鸠"]
        assert has_other is False, "低星照片属于有目录鸟种，不该触发兜底项"
    finally:
        db.close()


def test_dropdown_respects_current_rating_filter(tmp_path):
    """兜底项跟随当前星级筛选：只看 3★ 时没有无目录照片，就不显示。

    The catch-all follows the active rating filter.
    """
    from ui.results_browser_window import compute_dropdown_species

    db = _seed(tmp_path)
    try:
        _, has_other = compute_dropdown_species(db, use_en=False, ratings=[3])
        assert has_other is False, "3★ 下没有无目录照片，不该显示兜底项"
        _, has_other_all = compute_dropdown_species(db, use_en=False, ratings=[0])
        assert has_other_all is True, "0★ 下有塞舌尔和未识别照片，应显示兜底项"
    finally:
        db.close()
