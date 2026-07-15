# -*- coding: utf-8 -*-
"""
分类器推理锁存在性单测（轻量：不加载模型）。

Classifier inference-lock presence test (lightweight: no model load).
"""
import threading


def test_classifier_infer_lock_exists():
    import birdid.bird_identifier as bi
    assert isinstance(bi._CLASSIFIER_INFER_LOCK, type(threading.Lock()))
