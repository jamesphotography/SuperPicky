# -*- coding: utf-8 -*-
"""
识鸟模型选项与对比模式单测 / Unit tests for bird-ID engine selection and compare mode.
"""
import csv

import torch

import advanced_config as ac
from advanced_config import BIRDID_ENGINE_CHOICES, BIRDID_ENGINE_DEFAULT, AdvancedConfig
from birdid import bird_identifier as bi


def test_choices_match_identifier():
    assert tuple(BIRDID_ENGINE_CHOICES) == tuple(bi.BIRDID_ENGINES)
    assert BIRDID_ENGINE_DEFAULT == bi.DEFAULT_BIRDID_ENGINE == "compare"


def test_config_engine_default_and_validation(tmp_path):
    cfg = AdvancedConfig(config_file=str(tmp_path / "c.json"))
    assert cfg.birdid_engine == "compare"            # RC 默认对比模式 / RC default
    cfg.set_birdid_engine("BirdTrace")
    assert cfg.birdid_engine == "birdtrace"
    cfg.set_birdid_engine("merlin")                  # inside-tester 遗留值 / leftover value
    assert cfg.birdid_engine == "compare"
    cfg.config["birdid_engine"] = "bogus"            # 手改坏的配置 / hand-edited garbage
    assert cfg.birdid_engine == "compare"


def test_env_overrides_settings(tmp_path, monkeypatch):
    cfg = AdvancedConfig(config_file=str(tmp_path / "c.json"))
    cfg.set_birdid_engine("osea")
    monkeypatch.setattr(ac, "get_advanced_config", lambda: cfg)
    monkeypatch.delenv("SUPERPICKY_BIRDID_ENGINE", raising=False)
    assert bi.get_birdid_engine() == "osea"
    monkeypatch.setenv("SUPERPICKY_BIRDID_ENGINE", "birdtrace")
    assert bi.get_birdid_engine() == "birdtrace"
    monkeypatch.setenv("SUPERPICKY_BIRDID_ENGINE", "nonsense")
    assert bi.get_birdid_engine() == "compare"


def test_resolve_engines_and_fallback_when_birdtrace_missing(monkeypatch):
    monkeypatch.setattr(bi, "birdtrace_ready", lambda: True)
    assert bi.resolve_engines("compare") == ("birdtrace", "osea")
    assert bi.resolve_engines("birdtrace") == ("birdtrace", None)
    assert bi.resolve_engines("osea") == ("osea", None)
    monkeypatch.setattr(bi, "birdtrace_ready", lambda: False)
    assert bi.resolve_engines("compare") == ("osea", None)    # 缺模型退回 OSEA / fall back to OSEA
    assert bi.resolve_engines("birdtrace") == ("osea", None)


def _fake_predict(image, engine=None, **kw):
    name = {"birdtrace": "哈氏弯嘴鹛", "osea": "灰冠弯嘴鹛"}[engine]
    return [{"cn_name": name, "confidence": 50.0}]


def test_compare_mode_birdtrace_primary_with_osea_check(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERPICKY_BIRDID_ENGINE", "compare")
    monkeypatch.setattr(bi, "birdtrace_ready", lambda: True)
    monkeypatch.setattr(bi, "get_user_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(bi, "predict_bird", _fake_predict)
    result = bi.identify_bird("x.jpg", use_gps=False, use_geo_filter=False, preloaded_crop=object())
    assert result["success"] and result["engine"] == "compare" and result["primary_engine"] == "birdtrace"
    assert result["results"][0]["cn_name"] == "哈氏弯嘴鹛"
    assert result["compare"]["engine"] == "osea"
    assert result["compare"]["results"][0]["cn_name"] == "灰冠弯嘴鹛"
    with open(bi.compare_csv_path(), encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[1][2:10] == ["BirdTrace", "哈氏弯嘴鹛", "50.0", "", "OSEA", "灰冠弯嘴鹛", "50.0", ""]
    assert rows[1][10] == "0"


def test_single_engine_has_no_compare(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERPICKY_BIRDID_ENGINE", "birdtrace")
    monkeypatch.setattr(bi, "birdtrace_ready", lambda: True)
    monkeypatch.setattr(bi, "get_user_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(bi, "predict_bird", _fake_predict)
    result = bi.identify_bird("x.jpg", use_gps=False, use_geo_filter=False, preloaded_crop=object())
    assert result["primary_engine"] == "birdtrace" and "compare" not in result
    assert not (tmp_path / "birdid_engine_compare.csv").exists()


def test_check_model_failure_does_not_break_primary(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERPICKY_BIRDID_ENGINE", "compare")
    monkeypatch.setattr(bi, "birdtrace_ready", lambda: True)
    monkeypatch.setattr(bi, "get_user_data_dir", lambda: str(tmp_path))

    def predict(image, engine=None, **kw):
        if engine == "osea":
            raise RuntimeError("osea boom")
        return [{"cn_name": "褐鹰", "confidence": 90.0}]

    monkeypatch.setattr(bi, "predict_bird", predict)
    result = bi.identify_bird("x.jpg", use_gps=False, use_geo_filter=False, preloaded_crop=object())
    assert result["success"] and result["results"][0]["cn_name"] == "褐鹰"
    assert "osea boom" in result["compare"]["error"]


def test_tiers_run_model_once_and_reuse_probs(monkeypatch):
    """地区逐层放宽时模型只推理一次 / The model runs once while region tiers widen."""
    calls = []
    probs = torch.zeros(10964)
    probs[5] = 0.9

    def fake_class_probs(image, is_yolo_cropped, engine):
        calls.append(engine)
        return probs

    class Geo:
        def iter_candidates(self, *a):
            yield {1}, "region_subnational", "AU-VIC"
            yield {2}, "region_country", "AU"
            yield None, "none", None

    seen = []

    def fake_predict(image, species_class_ids=None, probs=None, **kw):
        seen.append(probs)
        return [] if species_class_ids else [{"class_id": 5}]

    monkeypatch.setattr(bi, "_class_probs", fake_class_probs)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: Geo())
    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    from PIL import Image
    found, tier, _count, _region = bi._identify_with_tiers(
        Image.new("RGB", (64, 64)), 3, None, None, "AU", "AU-VIC", True, None, None, engine="birdtrace")
    assert found == [{"class_id": 5}] and tier == "none"
    assert calls == ["birdtrace"]                     # 只推理一次 / one inference
    assert len(seen) == 3 and all(p is probs for p in seen)
