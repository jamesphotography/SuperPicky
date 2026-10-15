"""
predict_bird 注入颜值键单测：monkeypatch 打桩分类器+假 db_manager，
断言返回结果含 aesthetic_index 且值来自 db_manager，避开真实模型/库。
Verify predict_bird injects aesthetic_index via a stubbed classifier + fake DB.
"""
import pytest
import torch
from PIL import Image

import birdid.bird_identifier as bi


class _StubModel:
    """返回定值 logits：class_id 100 极高，其余 0。"""
    def __call__(self, x):
        v = torch.zeros(1, 10964)
        v[0, 100] = 10.0
        return v


class _FakeDB:
    def get_bird_by_class_id(self, cid):
        return {"english_name": "Stub Bird", "scientific_name": "Stubus avis",
                "chinese_simplified": "桩鸟", "ebird_code": "stub",
                "short_description_zh": ""}
    def get_gbif_rarity_by_class_id(self, cid, cc=None): return 50.0
    def get_iucn_by_class_id(self, cid): return None
    def get_aesthetic_by_class_id(self, cid): return 88.5
    def get_avilist_names_by_class_id(self, cid): return None


def test_predict_bird_injects_aesthetic(monkeypatch):
    monkeypatch.setattr(bi, "get_classifier", lambda: _StubModel())
    monkeypatch.setattr(bi, "get_database_manager", lambda: _FakeDB())
    out = bi.predict_bird(Image.new("RGB", (224, 224)), top_k=1)
    assert out, "predict_bird 应返回至少一个候选"
    assert out[0]["aesthetic_index"] == 88.5
