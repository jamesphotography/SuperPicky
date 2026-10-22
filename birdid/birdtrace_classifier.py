# -*- coding: utf-8 -*-
"""
BirdTrace 识鸟模型 / BirdTrace bird-ID model.

BirdTrace FastViT-MA36（作者 Warden，https://huggingface.co/warden123/BirdTrace-FastViT-MA36-10K ，
CC BY-NC 4.0，iNaturalist 数据自训）：10480 类、384×384 RGB 输入、输出原始 logits。

推理：timm 自带的 fastvit_ma36 结构，经 timm 的 Apple 权重转换函数加载 BirdTrace 发布的 safetensors（Apple
ml-fastvit 原始命名），再做 FastViT 推理融合（reparameterize，结果不变、实测 MPS 35→22 ms/张），跑在最佳
设备上（Apple Silicon=MPS，Windows=CUDA，其余 CPU）。softmax 后按 birdtrace_class_map.json 把概率累加到鸟种库
类别号（birdid.class_mapping），之后的取前 K 名、鸟名、地区过滤、罕见度见 bird_identifier。

BirdTrace FastViT-MA36 (by Warden, CC BY-NC 4.0, trained on iNaturalist): 10480 classes, 384x384 RGB input,
raw logits. Built with timm's fastvit_ma36, loaded from the released safetensors (Apple ml-fastvit names) via
timm's checkpoint converter, then reparameterised for inference (same outputs, MPS 35->22 ms per image), on the
best available device. Softmax probabilities are summed onto species-DB class ids via birdtrace_class_map.json.
"""
import json
import os
import threading
from typing import List, Optional

import numpy as np
from PIL import Image

from birdid.class_mapping import aggregate_probs

BIRDTRACE_INPUT = 384
BIRDTRACE_ARCH = "fastvit_ma36"
BIRDTRACE_WEIGHTS_FILE = "birdtrace_fastvit_ma36_10k.safetensors"
BIRDTRACE_CLASS_MAP_FILE = "birdtrace_class_map.json"
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def square_input(image: Image.Image, is_yolo_cropped: bool, size: int = BIRDTRACE_INPUT) -> Image.Image:
    """
    取图：YOLO 方形裁剪图（带真实背景）直接缩放；原图先取中心正方形。BirdTrace 未公开训练时的裁法，这种取法
    在 2026-10-21 五批人工核对数据上验证过。

    Framing: resize YOLO square crops (real background) directly; center-square full images first. BirdTrace
    does not publish its training crop; this framing was validated on five hand-checked sets (2026-10-21).

    参数 / Parameters:
        image (Image.Image): 输入图 / Input image.
        is_yolo_cropped (bool): 是否已是 YOLO 方形裁剪 / Whether already a YOLO square crop.
        size (int): 输出边长 / Output side length.

    返回 / Returns:
        Image.Image: size×size 的 RGB 图 / RGB image of size x size.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    if not is_yolo_cropped:
        w, h = image.size
        side = min(w, h)
        image = image.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
    return image.resize((size, size), Image.BICUBIC)


def birdtrace_available(weights_path: str, class_map_path: str) -> bool:
    """
    权重与对照表都在才算可用 / Available only when both the weights and the class map exist.

    参数 / Parameters:
        weights_path (str): safetensors 权重路径 / Weights path.
        class_map_path (str): 对照表 JSON 路径 / Class-map JSON path.

    返回 / Returns:
        bool
    """
    return os.path.isfile(weights_path) and os.path.isfile(class_map_path)


class BirdTraceClassifier:
    """
    BirdTrace 推理 + 类别映射 / BirdTrace inference plus class mapping.
    """

    def __init__(self, weights_path: str, class_map_path: str) -> None:
        """
        先在 CPU 上建模型、加载权重、推理融合，再搬到最佳设备（get_best_device）；搬设备或首推失败时退回 CPU。
        Build, load and reparameterise on CPU first, then move to the best device; fall back to CPU on failure.

        参数 / Parameters:
            weights_path (str): safetensors 权重路径 / Weights path.
            class_map_path (str): 对照表 JSON 路径 / Class-map JSON path.

        异常 / Raises:
            FileNotFoundError: 缺权重或对照表 / Weights or class map missing.
            RuntimeError: 权重与模型结构对不上 / Weights do not match the architecture.
        """
        import timm  # noqa: PLC0415  只在用到时导入 / lazy import
        import torch  # noqa: PLC0415
        from safetensors.torch import load_file  # noqa: PLC0415
        from timm.models.fastvit import checkpoint_filter_fn  # noqa: PLC0415
        from timm.utils.model import reparameterize_model  # noqa: PLC0415

        from config import get_best_device  # noqa: PLC0415

        for p in (weights_path, class_map_path):
            if not os.path.isfile(p):
                raise FileNotFoundError(f"BirdTrace: 缺少文件 / missing: {p}")
        with open(class_map_path, encoding="utf-8") as f:
            self.class_map: List[int] = json.load(f)["class_map"]

        state = load_file(weights_path, device="cpu")
        state = {k[len("backbone."):] if k.startswith("backbone.") else k: v for k, v in state.items()}
        model = timm.create_model(BIRDTRACE_ARCH, pretrained=False, num_classes=len(self.class_map))
        # 发布的是 Apple ml-fastvit 原始命名，timm 的转换函数换成 timm 命名；strict 保证一一对上
        # Released with Apple ml-fastvit names; timm's converter renames them, strict=True checks a full match.
        model.load_state_dict(checkpoint_filter_fn(state, model), strict=True)
        model = reparameterize_model(model.eval())

        device = torch.device(str(get_best_device()))
        try:
            model = model.to(device)
            with torch.no_grad():
                model(torch.zeros(1, 3, BIRDTRACE_INPUT, BIRDTRACE_INPUT, device=device))
        except Exception as e:  # noqa: BLE001
            print(f"[BirdTrace] {device} 初始化失败，改用 CPU / {device} init failed, falling back to CPU: {e}")
            device = torch.device("cpu")
            model = model.to(device)
        self._model = model
        self._device = device
        self._mean = torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1)
        self._std = torch.tensor(IMAGENET_STD).view(1, 3, 1, 1)
        self._lock = threading.Lock()
        mapped = len({c for c in self.class_map if c >= 0})
        print(f"[BirdTrace] {len(self.class_map)} 类，对上鸟种库 {mapped} 类，设备 / device {device}")

    def logits(self, image: Image.Image, is_yolo_cropped: bool) -> np.ndarray:
        """
        单张图 → BirdTrace 自己 10480 类的原始 logits / Raw logits over BirdTrace's own classes.

        参数 / Parameters:
            image (Image.Image): 输入图 / Input image.
            is_yolo_cropped (bool): 是否已是 YOLO 方形裁剪 / Whether already a YOLO square crop.

        返回 / Returns:
            np.ndarray: (10480,) float64
        """
        import torch  # noqa: PLC0415

        x = np.asarray(square_input(image, is_yolo_cropped), dtype=np.float32) / 255.0
        t = (torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1)[None])) - self._mean) / self._std
        with self._lock, torch.no_grad():
            out = self._model(t.to(self._device))[0].float().cpu().numpy()
        return out.astype(np.float64)

    def probs(self, image: Image.Image, is_yolo_cropped: bool) -> np.ndarray:
        """
        单张图 → 鸟种库类别上的概率 / Probabilities over species-DB classes.

        参数 / Parameters:
            image (Image.Image): 输入图 / Input image.
            is_yolo_cropped (bool): 是否已是 YOLO 方形裁剪 / Whether already a YOLO square crop.

        返回 / Returns:
            np.ndarray: (NUM_CLASSES,)
        """
        logits = self.logits(image, is_yolo_cropped)
        e = np.exp(logits - logits.max())
        return aggregate_probs(e / e.sum(), self.class_map)


_BIRDTRACE: Optional[BirdTraceClassifier] = None
_BIRDTRACE_LOCK = threading.Lock()


def get_birdtrace_classifier(weights_path: str, class_map_path: str) -> BirdTraceClassifier:
    """
    懒加载单例 / Lazily created singleton.

    参数 / Parameters:
        weights_path (str): safetensors 权重路径 / Weights path.
        class_map_path (str): 对照表 JSON 路径 / Class-map JSON path.

    返回 / Returns:
        BirdTraceClassifier
    """
    global _BIRDTRACE
    with _BIRDTRACE_LOCK:
        if _BIRDTRACE is None:
            _BIRDTRACE = BirdTraceClassifier(weights_path, class_map_path)
        return _BIRDTRACE
