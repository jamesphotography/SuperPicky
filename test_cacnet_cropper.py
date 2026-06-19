#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import pytest
from core import cacnet_cropper as cc


def test_rescale_box():
    # 网络输入 224x224 上的框,映射回 448x336 原图
    box = cc._rescale_box((56, 56, 168, 168), 224, 224, 448, 336)
    assert box == (112, 84, 336, 252)


def test_missing_weight_raises():
    cropper = cc.CACNetCropper(weight_path="/nonexistent/cacnet_cropping.pth")
    with pytest.raises(FileNotFoundError):
        cropper._ensure_loaded()
