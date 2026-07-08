# -*- coding: utf-8 -*-
"""OSEA 预处理必须收敛到单一共享模块，杜绝双份复制漂移。

OSEA preprocessing must live in one shared module — no more duplicated
definitions that drift apart.

背景 / Background:
OSEA_TRANSFORM / OSEA_TRANSFORM_DIRECT 与温度参数 0.9 曾在
birdid/bird_identifier.py 与 birdid/osea_classifier.py 各复制一份，靠注释
「需保持完全一致」人工同步——历史上正是这种漂移导致 CLI --model osea 与
GUI 主流程识别结果不一致。这组测试用对象同一性（is）锁定两处引用的是
birdid/osea_preprocess.py 里的同一个对象，任何一边改回本地定义都会立即红。

The transforms and the 0.9 softmax temperature used to be copy-pasted in two
files, kept in sync only by a comment. These tests pin object identity (is)
against the shared birdid/osea_preprocess.py so any regression to local
definitions fails immediately.
"""
import inspect

from birdid import bird_identifier, osea_classifier
from birdid.osea_preprocess import (
    OSEA_TEMPERATURE,
    OSEA_TRANSFORM,
    OSEA_TRANSFORM_DIRECT,
)


def test_transforms_are_the_same_objects():
    """两个模块引用的 transform 必须是共享模块里的同一个对象。"""
    assert bird_identifier.OSEA_TRANSFORM is OSEA_TRANSFORM
    assert bird_identifier.OSEA_TRANSFORM_DIRECT is OSEA_TRANSFORM_DIRECT
    assert osea_classifier.OSEA_TRANSFORM is OSEA_TRANSFORM
    assert osea_classifier.OSEA_TRANSFORM_DIRECT is OSEA_TRANSFORM_DIRECT


def test_temperature_single_source():
    """温度参数唯一来源是共享常量：方法默认值与主流程都必须引用它。"""
    assert OSEA_TEMPERATURE == 0.9

    sig_predict = inspect.signature(osea_classifier.OSEAClassifier.predict)
    assert sig_predict.parameters["temperature"].default == OSEA_TEMPERATURE
    sig_tta = inspect.signature(osea_classifier.OSEAClassifier.predict_with_tta)
    assert sig_tta.parameters["temperature"].default == OSEA_TEMPERATURE

    # predict_bird 不得再内联硬编码温度，必须引用共享常量
    # predict_bird must reference the shared constant, not an inline literal
    src = inspect.getsource(bird_identifier.predict_bird)
    assert "OSEA_TEMPERATURE" in src
    assert "TEMPERATURE = 0.9" not in src


def test_transform_parameters_unchanged():
    """收敛过程不得改变 transform 的实际参数（防止重构顺手改行为）。"""
    import torchvision.transforms as T

    steps = OSEA_TRANSFORM.transforms
    assert isinstance(steps[0], T.Resize) and steps[0].size == 256
    assert isinstance(steps[1], T.CenterCrop) and list(steps[1].size) == [224, 224]

    direct_steps = OSEA_TRANSFORM_DIRECT.transforms
    assert isinstance(direct_steps[0], T.Resize)
    assert tuple(direct_steps[0].size) == (224, 224)
    assert direct_steps[0].interpolation == T.InterpolationMode.LANCZOS
