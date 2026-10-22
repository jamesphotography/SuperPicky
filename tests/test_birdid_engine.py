# -*- coding: utf-8 -*-
"""
识鸟只用 BirdTrace 的单测（OSEA 与对比模式已删除）/ Bird ID runs BirdTrace only (OSEA and compare mode removed).
"""
import json

import torch

from advanced_config import AdvancedConfig
from birdid import bird_identifier as bi


def test_stale_engine_setting_is_dropped_on_load(tmp_path):
    """4.6.4 RC 留下的 birdid_engine（含 osea）加载时丢掉，保存后也不再写回。"""
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"birdid_engine": "osea", "language": "zh_CN"}), encoding="utf-8")
    cfg = AdvancedConfig(config_file=str(path))
    assert "birdid_engine" not in cfg.config
    assert not hasattr(cfg, "set_birdid_engine")
    cfg.save()
    assert "birdid_engine" not in json.loads(path.read_text(encoding="utf-8"))


def test_get_classifier_is_birdtrace(monkeypatch):
    """预加载与识别共用的 get_classifier 返回 BirdTrace 单例。"""
    from birdid import birdtrace_classifier as bt

    sentinel = object()
    seen = []

    def fake_get(weights, class_map):
        seen.append((weights, class_map))
        return sentinel

    monkeypatch.setattr(bt, "get_birdtrace_classifier", fake_get)
    assert bi.get_classifier() is sentinel
    assert seen == [(bi.BIRDTRACE_WEIGHTS_PATH, bi.BIRDTRACE_CLASS_MAP_PATH)]


def test_identify_runs_one_model_without_compare(monkeypatch):
    calls = []

    def predict(image, **kw):
        calls.append(kw)
        return [{"cn_name": "褐鹰", "confidence": 90.0}]

    monkeypatch.setattr(bi, "predict_bird", predict)
    result = bi.identify_bird("x.jpg", use_gps=False, use_geo_filter=False, preloaded_crop=object())
    assert result["success"] and result["results"][0]["cn_name"] == "褐鹰"
    assert len(calls) == 1
    assert "compare" not in result and "engine" not in calls[0]


def test_missing_birdtrace_reports_error(monkeypatch):
    """缺模型文件：识别失败并在 error 里点名 BirdTrace，不抛到调用方。"""
    def boom(image, is_yolo_cropped):
        raise FileNotFoundError("BirdTrace: 缺少文件 / missing: x.safetensors")

    monkeypatch.setattr(bi, "_class_probs", boom)
    from PIL import Image
    result = bi.identify_bird("x.jpg", use_gps=False, use_geo_filter=False,
                              preloaded_crop=Image.new("RGB", (32, 32)))
    assert not result["success"] and "BirdTrace" in result["error"]


def test_tiers_run_model_once_and_reuse_probs(monkeypatch):
    """地区逐层放宽时模型只推理一次 / The model runs once while region tiers widen."""
    calls = []
    probs = torch.zeros(10964)
    probs[5] = 0.9

    def fake_class_probs(image, is_yolo_cropped):
        calls.append(is_yolo_cropped)
        return probs

    class Geo:
        def iter_candidates(self, *a):
            yield {1}, "region_subnational", "AU-VIC"
            yield {2}, "region_country", "AU"

    seen = []

    def fake_predict(image, species_class_ids=None, probs=None, **kw):
        seen.append(probs)
        return [{"class_id": 5}] if species_class_ids == {2} else []

    monkeypatch.setattr(bi, "_class_probs", fake_class_probs)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: Geo())
    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    from PIL import Image
    found, tier, _count, region = bi._identify_with_tiers(
        Image.new("RGB", (64, 64)), 3, None, None, "AU", "AU-VIC", True, None, None)
    assert found == [{"class_id": 5}] and tier == "region_country" and region == "AU"
    assert calls == [True]                            # 只推理一次 / one inference
    assert len(seen) == 2 and all(p is probs for p in seen)


def test_birdid_adapter_passes_no_compare_flag(monkeypatch):
    """视频与补救扫描共用的适配器不再传 compare 参数 / The adapter no longer passes compare."""
    import numpy as np

    from core.birdid_adapter import BirdIDAdapter

    seen = {}

    def fake_identify(**kwargs):
        seen.update(kwargs)
        return {"success": True, "results": []}

    monkeypatch.setattr(bi, "identify_bird", fake_identify)
    BirdIDAdapter().identify(np.zeros((64, 64, 3), dtype=np.uint8), top_k=3)
    assert seen and "compare" not in seen
