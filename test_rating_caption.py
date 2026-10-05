# -*- coding: utf-8 -*-
"""
评分说明（题注）生成的测试：结论行、逐项依据、参照数字、手动改星级。

Tests for the rating caption builder (core/rating_caption.py).
"""
import pytest

from core.rating_caption import (
    MANUAL_MARK,
    CaptionFacts,
    build_caption,
    mark_manual_change,
    outcome_from_reason_key,
)
from core.rating_quota import PhotoMetricsV2, assign_ratings
from tools.i18n import I18n

ZH = I18n("zh_CN").t
EN = I18n("en_US").t


def _ranked(**kw) -> CaptionFacts:
    """排序池里一张 3★ 照片的典型事实 / Typical facts of a ranked 3★ photo."""
    base = dict(
        rating=3, outcome="top", confidence=0.88, head_sharp=631.0, norm_sharpness=631.0,
        topiq=5.93, best_eye=0.99, focus_status="BEST", focus_weight=1.1, iso=1000,
        rank=1, group_size=18, species_known=True, pct_sharp=0.97, pct_topiq=0.91,
        quota3=20, quota2=30, sharp_floor3=300, weight_sharp=0.65, weight_topiq=0.35,
        birdid_enabled=True, species_confidence=92.0, species_threshold=50.0, pool_size=60,
    )
    base.update(kw)
    return CaptionFacts(**base)


def test_top_photo_caption_zh():
    """3★：先结论、再回答「为什么」（名次与 3★ 名额），然后排名依据与逐项证据。"""
    cap = build_caption(_ranked(picked=True), ZH)
    lines = cap.split("\n")
    assert lines[0] == "3★ 优选 · 精选"
    assert lines[1] == "为什么是 3★：同种 18 张里排第 1，前 4 张评 3★"
    assert lines[2].startswith("排名靠什么：头部锐度占 65%、美学分占 35%")
    assert "✓ 头部清晰：头部锐度 631（本批前 3%）" in lines
    assert "✓ 对焦点落在鸟头上（精焦，加分）" in lines
    assert "✓ 美学分 5.9 / 10（本批前 9%）" in lines
    assert "✓ 眼睛清晰可见" in lines
    assert "· 有鸟置信度 88%（YOLO 检测）" in lines
    assert "· 鸟种识别置信度 OSEA 92%" in lines
    assert not any(l.startswith("ISO") for l in lines)   # ISO 不是打分依据，不单独成行
    for jargon in ("TOPIQ", "权重", "[修正]", "调整后", "AI 认鸟把握"):
        assert jargon not in cap


def test_top_photo_caption_en():
    """英文界面输出英文，不夹带中文。"""
    cap = build_caption(_ranked(), EN)
    lines = cap.split("\n")
    assert lines[0] == "3★ Excellent"
    assert lines[1] == "Why 3★: #1 of 18 same-species photos; the top 4 get 3★"
    assert "✓ Sharp head: head sharpness 631 (top 3% of this batch)" in cap
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in cap)


def test_mid_and_rest_state_the_quota_cutoffs():
    """2★/1★ 写清名额分界：前 4 张 3★、第 5–10 张 2★（18 张，配额 20%/30%）。"""
    mid = build_caption(_ranked(rating=2, outcome="mid", rank=8), ZH).split("\n")
    assert mid[0] == "2★ 良好"
    assert mid[1] == "为什么是 2★：同种 18 张里排第 8。前 4 张评 3★，第 5–10 张评 2★"
    rest = build_caption(_ranked(rating=1, outcome="rest", rank=17), ZH).split("\n")
    assert rest[1] == "为什么是 1★：同种 18 张里排第 17，在 2★ 名额（前 10 张）之外"


def test_single_photo_species_is_not_shown_as_last():
    """同种只有 1 张时写「1 张里排第 1」，不再出现易误读的「排名100%」。"""
    cap = build_caption(_ranked(rating=2, outcome="floor_capped", group_size=1,
                                norm_sharpness=280, head_sharp=280, pct_sharp=0.4), ZH)
    why = cap.split("\n")[1]
    assert why == ("为什么不是 3★：同种 1 张里排第 1，名次够 3★，"
                   "但头部锐度 280 低于 3★ 最低要求 300")
    assert "100%" not in why
    assert "✗ 头部偏软：头部锐度 280（本批后 41%）" in cap


def test_caps_explain_why_not_three_stars():
    """看不清眼睛、连拍名额已满时，「为什么」行说清为什么不是 3★。"""
    eye = build_caption(_ranked(rating=2, outcome="eye_capped", best_eye=0.03), ZH)
    assert "名次够 3★，但看不清眼睛，最多给 2★" in eye.split("\n")[1]
    assert "✗ 看不清眼睛（可见度 3%）" in eye
    burst = build_caption(_ranked(rating=2, outcome="burst_capped", burst_cap=2), ZH)
    assert "同一组连拍已有 2 张 3★（每组最多 2 张）" in burst.split("\n")[1]


def test_group_wording_follows_bird_id_state():
    """识鸟关闭时按「本批」排名；开了但没认出鸟种时说「未识别出鸟种的」。"""
    off = build_caption(_ranked(birdid_enabled=False, species_known=False,
                                species_confidence=None), ZH)
    assert off.split("\n")[1].startswith("为什么是 3★：本批 18 张里排第 1")
    assert "鸟种识别置信度" not in off
    unknown = build_caption(_ranked(species_known=False, species_confidence=None), ZH)
    assert unknown.split("\n")[1].startswith("为什么是 3★：未识别出鸟种的 18 张里排第 1")


def test_low_confidence_species_explains_handling():
    """识别置信度不够：写出候选、置信度与门槛，以及「不写入鸟种、排名仍按候选比较」。"""
    cap = build_caption(_ranked(species_confidence=25.0, species_low_confidence=True,
                                species_candidate="长尾草雀"), ZH)
    assert ("· 鸟种未确定：最可能是长尾草雀，但识别置信度 OSEA 25% 低于设置的 50%，"
            "不写入鸟种、不按它分目录；排名时仍和长尾草雀的照片一起比较") in cap
    assert "鸟种识别置信度" not in cap


def test_gated_photos_state_the_rule_and_threshold():
    """被硬门槛挡下的照片：「为什么」写出具体数值与最低要求，不出现排名与排名依据。"""
    cap = build_caption(CaptionFacts(
        rating=0, outcome="low_sharpness", confidence=0.9, norm_sharpness=38,
        head_sharp=38, sharp_gate=100, topiq=4.2, best_eye=0.8, iso=800), ZH)
    lines = cap.split("\n")
    assert lines[:2] == ["0★ 未通过", "为什么是 0★：头部太模糊——头部锐度 38，低于最低要求 100"]
    assert "· 头部锐度 38" in cap
    assert "排名靠什么" not in cap and "本批" not in cap
    conf = build_caption(CaptionFacts(rating=0, outcome="low_confidence", confidence=0.29,
                                      confidence_threshold=0.7, iso=800), ZH)
    assert conf.split("\n") == ["0★ 未通过",
                                 "为什么是 0★：不确定画面里是鸟——有鸟置信度 29%，低于设置的 70%"]
    assert build_caption(CaptionFacts(rating=-1, outcome="no_bird"), ZH) == "无鸟\n画面里没有检测到鸟"


def test_iso_note_and_focus_variants():
    """高 ISO 折算写明原始值；合焦的三种来源分别说明。"""
    cap = build_caption(_ranked(head_sharp=700, norm_sharpness=630, iso=6400), ZH)
    assert "已按 ISO 6400 折算（原始 700）" in cap
    assert "折算" not in build_caption(_ranked(head_sharp=600, norm_sharpness=590, iso=1100), ZH)
    assert "· 对焦点在鸟身上，不在头部" in build_caption(
        _ranked(focus_status="GOOD", focus_weight=0.9), ZH)
    assert "· 没有相机对焦点信息" in build_caption(
        _ranked(focus_status="GOOD", focus_weight=1.0), ZH)
    assert "鸟头实测清晰，按合焦处理" in build_caption(
        _ranked(focus_status="GOOD", focus_weight=0.9, focus_arbitrated=True), ZH)
    assert "✗ 对焦点在鸟以外（脱焦，减分）" in build_caption(_ranked(focus_status="WORST"), ZH)


def test_unknown_reason_falls_back_to_v1_text():
    """旧版 V1 评星：「为什么」行用引擎给出的原因文字。"""
    assert outcome_from_reason_key("whatever") == "v1"
    cap = build_caption(CaptionFacts(rating=2, outcome="v1", v1_reason="良好照片",
                                     norm_sharpness=500, topiq=5.0), ZH)
    assert cap.split("\n")[:2] == ["2★ 良好", "为什么是 2★：良好照片"]


def test_manual_change_replaces_only_the_marker_line():
    """手动改星级：首行插入说明，再改只替换这一行，原说明保留。"""
    original = build_caption(_ranked(), ZH)
    once = mark_manual_change(original, 1, ZH)
    assert once.startswith(f"{MANUAL_MARK} 手动改为 1★")
    assert once.split("\n", 1)[1] == original
    twice = mark_manual_change(once, 2, ZH)
    assert twice.split("\n", 1)[1] == original
    assert twice.count(MANUAL_MARK) == 1
    assert mark_manual_change(None, -1, ZH) == f"{MANUAL_MARK} 手动标记为无鸟（下面是原评分说明）"


def test_assign_ratings_reports_rank_and_floor_cap():
    """定星结果带组内名次/张数/百分位；名次够 3★ 但锐度不足时给 floor_capped。"""
    photos = [PhotoMetricsV2(key=f"p{i}", detected=True, confidence=0.9,
                             norm_sharpness=150 + i, topiq=5.0, best_eye=0.9, beak_vis=0.9)
              for i in range(3)]
    res = assign_ratings(photos, quota3=30, quota2=30)
    best = res["p2"]
    assert best.reason_key == "rating_v2.floor_capped"
    assert best.reason_args["rank"] == 1 and best.reason_args["group_size"] == 3
    assert 0.0 <= best.reason_args["pct_sharp"] <= 1.0
    assert outcome_from_reason_key(best.reason_key) == "floor_capped"


def test_picked_stage_appends_suffix_and_keeps_db_prefix(monkeypatch):
    """
    精选阶段：题注结论行补「· 精选」，写进文件与 DB；DB 备注前的「连拍统一鸟种」说明保留。

    The picked stage appends "· 精选" to the caption in both the file batch and
    the DB, keeping a DB-only burst-unification line in front of it.
    """
    import types
    import core.photo_processor as pp

    facts = _ranked(picked=False)
    plain = build_caption(facts, ZH)
    burst_line = "连拍统一鸟种（依据 _Z9W0001）"

    class _FakeDB:
        def __init__(self):
            self.rows = {"IMG_1": {"caption": burst_line + "\n" + plain}}

        def get_photo(self, key):
            return self.rows.get(key)

        def update_photo(self, key, data):
            self.rows.setdefault(key, {}).update(data)

    class _FakeWriter:
        def __init__(self):
            self.batches = []

        def batch_set_metadata(self, items):
            self.batches.append(items)
            return {"failed": 0}

    writer = _FakeWriter()
    monkeypatch.setattr(pp, "get_exiftool_manager", lambda: writer)
    proc = pp.PhotoProcessor.__new__(pp.PhotoProcessor)
    proc.star_3_photos = [{"file": "/x/IMG_1.NEF", "nima": 6.0, "sharpness": 600.0}]
    proc.config = types.SimpleNamespace(picked_top_percentage=25)
    proc.i18n = I18n("zh_CN")
    proc._caption_facts = {"IMG_1": facts}
    proc.report_db = _FakeDB()
    proc.stats = {}
    proc._log = lambda *a, **k: None

    proc._calculate_picked_flags()

    picked = build_caption(_ranked(picked=True), ZH)
    assert picked.split("\n")[0].endswith(" · 精选")
    assert writer.batches[0][0]["caption"] == picked
    assert proc.report_db.rows["IMG_1"]["caption"] == burst_line + "\n" + picked
    assert proc.report_db.rows["IMG_1"]["picked"] == 1


def test_mark_no_bird_marks_caption_everywhere(tmp_path):
    """标记无鸟：DB、内存记录与写给 Lightroom 的题注都在首行标明「手动标记为无鸟」。"""
    import os
    from ui.results_browser_window import _run_mark_no_bird

    nef = tmp_path / "IMG_2.NEF"
    nef.write_bytes(b"x")
    original = build_caption(_ranked(), ZH)
    photo = {"filename": "IMG_2", "current_path": str(nef), "rating": 3,
             "bird_species_cn": "铜翅鸠", "caption": original}

    class _DB:
        def __init__(self):
            self.data = {}

        def update_photo(self, key, data):
            self.data.update(data)

    class _Writer:
        def __init__(self):
            self.captions = []

        def clear_species_metadata(self, *a, **k):
            return True

        def set_rating_and_pick(self, path, rating, caption=None, **k):
            self.captions.append((rating, caption))
            return True

    db, writer = _DB(), _Writer()
    _run_mark_no_bird(str(tmp_path), photo, db, "IMG_2", I18n("zh_CN"),
                      old_bird_cn="铜翅鸠", metadata_writer=writer)
    expected = f"{MANUAL_MARK} 手动标记为无鸟（下面是原评分说明）\n" + original
    assert db.data["caption"] == expected
    assert photo["caption"] == expected
    assert writer.captions == [(-1, expected)]
    assert os.path.exists(str(nef))


def test_display_metrics_match_caption_basis():
    """显示口径：头部锐度 = 原始 × ISO 折算，美学 = 原始分（不乘对焦/飞鸟系数）。"""
    from core.iso_sharpness import display_aesthetic, display_head_sharpness, iso_sharpness_factor
    assert iso_sharpness_factor(800) == 1.0
    assert iso_sharpness_factor(1600) == pytest.approx(0.95)
    assert iso_sharpness_factor(3200) == pytest.approx(0.90)
    row = {"head_sharp": 600.0, "iso": 3200, "nima_score": 5.74,
           "adj_sharpness": 648.0, "adj_topiq": 6.3}
    assert display_head_sharpness(row) == pytest.approx(540.0)
    assert display_aesthetic(row) == pytest.approx(5.74)
    assert display_head_sharpness({"head_sharp": None}) is None


def test_sharpness_sort_uses_iso_normalized_head_sharpness(tmp_path):
    """按锐度排序用折算后的头部锐度：原始值更高但 ISO 6400 的照片排在后面。"""
    from tools.report_db import ReportDB
    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo({"filename": "hi_iso", "rating": 2, "head_sharp": 600.0, "iso": 6400,
                         "adj_sharpness": 900.0})
        db.insert_photo({"filename": "lo_iso", "rating": 2, "head_sharp": 560.0, "iso": 400,
                         "adj_sharpness": 100.0})
        db.insert_photo({"filename": "none", "rating": 2, "head_sharp": None})
        names = [r["filename"] for r in db.get_photos_by_filters({"sort_by": "sharpness_desc"})]
        assert names == ["lo_iso", "hi_iso", "none"]   # 560 > 600×0.85=510
        db.insert_photo({"filename": "aes_hi", "rating": 2, "nima_score": 6.0, "adj_topiq": 1.0})
        names = [r["filename"] for r in db.get_photos_by_filters({"sort_by": "aesthetic_desc"})]
        assert names[0] == "aes_hi"
    finally:
        db.close()


def test_detail_panel_shows_caption_numbers():
    """详情面板「头部锐度」「美学分」与题注里的数字一致（整数 / 一位小数）。"""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from ui.detail_panel import DetailPanel
    panel = DetailPanel(I18n("zh_CN"))
    try:
        panel._current_photo = {"filename": "IMG_3", "head_sharp": 608.0, "iso": 1600,
                                "nima_score": 5.96, "adj_sharpness": 504.1, "adj_topiq": 6.55,
                                "focus_status": "GOOD", "rating": 3}
        panel._refresh_metadata()
        assert panel._val_sharpness.text() == "578"    # 608 × 0.95
        assert panel._val_aesthetic.text() == "6.0"
    finally:
        panel.close()


def test_tiny_pool_shows_values_without_percentiles():
    """排序池不足 10 张：写数值，不写「本批前/后」，也不据此打 ✓/✗。"""
    cap = build_caption(_ranked(pool_size=1, group_size=1, pct_sharp=0.0, pct_topiq=0.0), ZH)
    assert "本批" not in cap
    assert "· 头部锐度 631" in cap and "· 美学分 5.9 / 10" in cap
