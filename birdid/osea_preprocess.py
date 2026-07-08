# -*- coding: utf-8 -*-
"""
OSEA 分类器预处理的单一事实源（SSOT）。

历史教训：OSEA_TRANSFORM / OSEA_TRANSFORM_DIRECT 与 softmax 温度 0.9 曾在
bird_identifier.py（GUI 主选片流程）与 osea_classifier.py（CLI --model osea
路径）各复制一份，靠注释「需保持完全一致」人工同步。两边单测各自都能通过，
漂移不会被自动发现——正是这种漂移造成过「同一张图两条路径识别结果不一致」。
本模块收敛全部共享定义，两边只允许 import，不得再本地定义；
test_osea_preprocess_shared.py 用对象同一性锁定这一约束。

Single source of truth for OSEA classifier preprocessing.

Lesson learned: the transforms and the 0.9 softmax temperature used to be
copy-pasted into bird_identifier.py (GUI picking pipeline) and
osea_classifier.py (CLI --model osea path), kept in sync only by a comment.
Each side's tests passed independently, so drift went undetected — exactly
what caused the "same image, different results on the two paths" bug. This
module owns the shared definitions; both consumers must import from here,
and test_osea_preprocess_shared.py pins that with object-identity checks.
"""
from torchvision import transforms

# softmax 温度：<1 让分布更尖锐。与 OSEA train/serve 对齐验证时定为 0.9，
# 见记忆 osea-train-serve-skew（8/8 逐位一致验证）。
# Softmax temperature: <1 sharpens the distribution. Fixed at 0.9 during the
# OSEA train/serve alignment (bitwise-identical verification, 8/8).
OSEA_TEMPERATURE: float = 0.9

# 未裁剪整图：短边缩到 256 后中心裁 224（经典 ImageNet 式预处理）。
# Uncropped full frame: resize short side to 256, then center-crop 224.
OSEA_TRANSFORM = transforms.Compose(
    [
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
)

# YOLO 已裁剪成紧凑方形图：直接 LANCZOS 到 224x224，不再二次裁切。
# Already YOLO-cropped tight square: LANCZOS straight to 224x224, no
# second crop.
OSEA_TRANSFORM_DIRECT = transforms.Compose(
    [
        transforms.Resize(
            (224, 224), interpolation=transforms.InterpolationMode.LANCZOS
        ),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
)
