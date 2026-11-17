#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flight Detector - 飞版检测模块。
Flight Detector module.

使用 EfficientNet-B3 模型检测鸟类是否处于飞行。
Uses an EfficientNet-B3 model to determine whether a bird is in flight.
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Union, cast
import numpy as np

import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image
from config import (
    get_best_device,
    get_install_scoped_resource_path,
    get_packaged_model_relative_path,
    get_runtime_app_root,
    get_runtime_meipass,
)


@dataclass
class FlightResult:
    """飞版检测结果"""
    is_flying: bool
    confidence: float


class FlightDetector:
    """
    飞版检测器
    使用 EfficientNet-B3 二分类模型判断鸟类是否处于飞行状态
    """

    IMAGE_SIZE = 384
    THRESHOLD = 0.5
    
    def __init__(self, model_path: Optional[str] = None):
        """
        初始化检测器

        Args:
            model_path: 模型文件路径，如果为 None 则使用默认路径
        """
        self.model: Optional[nn.Module] = None
        self.device: Optional[torch.device] = None
        self.model_loaded = False

        if model_path is None:
            import sys
            if getattr(sys, 'frozen', False) and sys.platform == 'win32':
                self.model_path = get_install_scoped_resource_path(
                    "models/superFlier_efficientnet.pth",
                    packaged_relative_path=get_packaged_model_relative_path("models/superFlier_efficientnet.pth"),
                )
            else:
                meipass = get_runtime_meipass()
                if meipass is not None:
                    self.model_path = Path(meipass) / "models" / "superFlier_efficientnet.pth"
                else:
                    project_root = get_runtime_app_root()
                    if project_root is None:
                        project_root = str(Path(__file__).parent.parent)
                    self.model_path = Path(project_root) / "models" / "superFlier_efficientnet.pth"
        else:
            self.model_path = Path(model_path)

        self.transform = transforms.Compose([
            transforms.Resize((self.IMAGE_SIZE, self.IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225]
            )
        ])
    
    def _build_model(self) -> nn.Module:
        """
        构建 EfficientNet-B3 模型结构
        """
        model = cast(nn.Module, models.efficientnet_b3(weights=None))
        classifier = cast(nn.Sequential, getattr(model, "classifier"))
        classifier_linear = cast(nn.Linear, classifier[1])
        in_features = classifier_linear.in_features

        setattr(
            model,
            "classifier",
            nn.Sequential(
            nn.Dropout(0.2),
            nn.Linear(in_features, 1),
            nn.Sigmoid()
            ),
        )

        return model
    
    def load_model(self) -> None:
        """
        加载模型权重

        Raises:
            FileNotFoundError: 模型文件不存在
            RuntimeError: 模型加载失败
        """
        if not self.model_path.exists():
            raise FileNotFoundError(f"飞版检测模型未找到: {self.model_path}")

        from config import move_model_to_device
        self.device = torch.device(str(get_best_device()))
        model = self._build_model()

        # 先在 CPU 上加载权重，再搬到目标设备；GPU 出错时退回 CPU（见 move_model_to_device）
        # Load weights on the CPU, then move; fall back to the CPU on GPU errors
        try:
            state_dict = torch.load(
                self.model_path,
                map_location="cpu",
                weights_only=True
            )
            model.load_state_dict(state_dict)
        except Exception as e:
            raise RuntimeError(f"[Flight] 加载飞版检测模型失败 / failed to load flight model: {e}")

        model, self.device, _ = move_model_to_device(model, self.device, "Flight", half=False)
        model.eval()
        self.model = model
        self.model_loaded = True
    
    def detect(
        self,
        image: Union[np.ndarray, Image.Image, str],
        threshold: Optional[float] = None
    ) -> FlightResult:
        """
        检测图像中的鸟是否处于飞行状态

        Args:
            image: 输入图像，支持 numpy.ndarray、PIL.Image 或文件路径
            threshold: 分类阈值，默认使用 self.THRESHOLD (0.5)

        Returns:
            FlightResult: 包含 is_flying 和 confidence

        Raises:
            RuntimeError: 模型未加载
        """
        if not self.model_loaded:
            raise RuntimeError("飞版检测模型未加载，请先调用 load_model()")

        if threshold is None:
            threshold = self.THRESHOLD

        if isinstance(image, str):
            pil_image = Image.open(image).convert('RGB')
        elif isinstance(image, np.ndarray):
            import cv2
            if len(image.shape) == 3 and image.shape[2] == 3:
                rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            else:
                rgb_image = image
            pil_image = Image.fromarray(rgb_image)
        elif isinstance(image, Image.Image):
            pil_image = image.convert('RGB')
        else:
            raise ValueError(f"不支持的图像类型: {type(image)}")

        # 预处理 / preprocessing
        transformed_tensor = cast(torch.Tensor, self.transform(pil_image))
        image_tensor = transformed_tensor.unsqueeze(0).to(self.device)

        if self.model is None:
            raise RuntimeError("飞版检测模型尚未初始化")

        # 推理 / inference
        with torch.no_grad():
            prob = self.model(image_tensor).item()
        del image_tensor  # 立即释放 MPS/CUDA 显存，避免长批次累积 / release MPS/CUDA memory immediately

        return FlightResult(
            is_flying=prob > threshold,
            confidence=prob
        )
    
    def detect_batch(
        self,
        images: list,
        threshold: Optional[float] = None,
        batch_size: int = 8
    ) -> list:
        """
        批量检测多张图像

        Args:
            images: 图像列表（支持混合类型）
            threshold: 分类阈值
            batch_size: 批处理大小

        Returns:
            list[FlightResult]: 检测结果列表
        """
        if not self.model_loaded:
            raise RuntimeError("飞版检测模型未加载，请先调用 load_model()")

        if threshold is None:
            threshold = self.THRESHOLD

        results = []

        for i in range(0, len(images), batch_size):
            batch = images[i:i + batch_size]
            batch_tensors = []

            for img in batch:
                if isinstance(img, str):
                    pil_image = Image.open(img).convert('RGB')
                elif isinstance(img, np.ndarray):
                    import cv2
                    rgb_image = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    pil_image = Image.fromarray(rgb_image)
                elif isinstance(img, Image.Image):
                    pil_image = img.convert('RGB')
                else:
                    continue

                batch_tensors.append(cast(torch.Tensor, self.transform(pil_image)))

            if not batch_tensors:
                continue

            if self.device is None:
                raise RuntimeError("飞版检测设备尚未初始化")
            batch_tensor = torch.stack(batch_tensors).to(self.device)

            if self.model is None:
                raise RuntimeError("飞版检测模型尚未初始化")
            model = self.model
            with torch.no_grad():
                probs = model(batch_tensor).squeeze().cpu().numpy() # type: ignore

            if probs.ndim == 0:
                probs = [probs.item()]

            for prob in probs:
                results.append(FlightResult(
                    is_flying=prob > threshold,
                    confidence=float(prob)
                ))

        return results


_flight_detector_instance: Optional[FlightDetector] = None


def get_flight_detector() -> FlightDetector:
    """
    获取全局飞版检测器实例（单例模式）

    Returns:
        FlightDetector: 全局检测器实例
    """
    global _flight_detector_instance
    if _flight_detector_instance is None:
        _flight_detector_instance = FlightDetector()
    return _flight_detector_instance
