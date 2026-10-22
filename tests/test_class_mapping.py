# -*- coding: utf-8 -*-
"""
外部识鸟模型类别映射单测 / Unit tests for external-model class mapping.
"""
import numpy as np
import pytest

from birdid.class_mapping import NUM_CLASSES, aggregate_probs, build_class_map, norm_name


def test_norm_name_drops_apostrophes_underscores_and_case():
    assert norm_name("Abbotts_Babbler") == norm_name("Abbott's Babbler") == "abbottsbabbler"
    assert norm_name(None) == "" and norm_name("") == ""


def test_build_class_map_priority_override_code_sci_english():
    taxa = {
        "split1": ("Western Thing", "Novus occidentalis"),
        "code1": ("Coded Bird", "Avis codica"),
        "sci1": ("Sci Bird", "Avis scientifica"),
        "en1": ("Abbott's Babbler", "Unknown species"),
        "miss": ("Nobody", "Nullus"),
    }
    by_code = {"code1": 10, "split1": 99}
    by_sci = {"Avis parens": 20, "Avis scientifica": 30, "Avis codica": 40}
    by_en = {norm_name("Abbotts Babbler"): 50}
    out = build_class_map(["split1", "code1", "sci1", "en1", "miss"], taxa, by_code, by_sci, by_en,
                          overrides={"split1": "Avis parens"})
    assert out == [20, 10, 30, 50, -1]   # 拆分对照优先于代码 / override beats code


def test_build_class_map_rejects_out_of_range_ids():
    out = build_class_map(["a", "b"], {}, {"a": NUM_CLASSES, "b": -5}, {}, overrides={})
    assert out == [-1, -1]


def test_aggregate_sums_many_to_one_and_drops_unmapped():
    probs = np.array([0.5, 0.2, 0.2, 0.1])
    out = aggregate_probs(probs, [7, 7, -1, 3])
    assert out.shape == (NUM_CLASSES,)
    assert out[7] == pytest.approx(0.7) and out[3] == pytest.approx(0.1)
    assert out.sum() == pytest.approx(0.8)   # 对不上的 0.2 被丢弃 / unmapped mass dropped


def test_aggregate_rejects_length_mismatch():
    with pytest.raises(ValueError):
        aggregate_probs(np.ones(3), [0, 1])
