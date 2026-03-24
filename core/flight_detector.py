#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flight Detector - 飞版检测模块
使用 EfficientNet-B3 模型检测鸟类是否处于飞行状态

V3.4 新增功能
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Union
import numpy as np

import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image
from config import get_best_device


@dataclass
class FlightResult:
    """飞版检测结果"""
    is_flying: bool         # 是否飞行
    confidence: float       # 置信度 (0-1)


class FlightDetector:
    """
    飞版检测器
    
    使用 EfficientNet-B3 二分类模型判断鸟类是否处于飞行状态。
    模型训练自 superFlier 项目，使用 RMSprop + last_block freeze 策略。
    """
    
    # 模型配置
    IMAGE_SIZE = 384  # 训练时的输入尺寸
    THRESHOLD = 0.5   # 默认分类阈值
    
    def __init__(self, model_path: Optional[str] = None):
        """
        初始化检测器
        
        Args:
            model_path: 模型文件路径，如果为 None 则使用默认路径
        """
        self.model = None
        self.device = None
        self.model_loaded = False
        
        # 确定模型路径（支持 PyInstaller 打包）
        if model_path is None:
            import sys
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller 打包后的路径
                self.model_path = Path(sys._MEIPASS) / "models" / "superFlier_efficientnet.pth"
            else:
                # 开发环境：项目根目录/models/
                project_root = Path(__file__).parent.parent
                self.model_path = project_root / "models" / "superFlier_efficientnet.pth"
        else:
            self.model_path = Path(model_path)
        
        # 图像预处理（与训练时一致）
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
        
        必须与训练时的结构完全一致：
        - 使用 Dropout(0.2)
        - 输出层为 Linear(in_features, 1) + Sigmoid
        """
        model = models.efficientnet_b3(weights=None)  # 不需要预训练权重
        in_features = model.classifier[1].in_features
        
        # 替换分类头（与 grid_search.py 中的 DROPOUT=0.2 一致）
        model.classifier = nn.Sequential(
            nn.Dropout(0.2),
            nn.Linear(in_features, 1),
            nn.Sigmoid()
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
        
        self.device = get_best_device()
        
        # 构建并加载模型
        self.model = self._build_model()
        
        try:
            state_dict = torch.load(
                self.model_path,
                map_location=self.device,
                weights_only=True
            )
            self.model.load_state_dict(state_dict)
        except Exception as e:
            raise RuntimeError(f"加载飞版检测模型失败: {e}")
        
        self.model.to(self.device)
        self.model.eval()
        self.model_loaded = True
    
    def detect(
        self, 
        image: Union[np.ndarray, Image.Image, str],
        threshold: float = None
    ) -> FlightResult:
        """
        检测图像中的鸟是否处于飞行状态
        
        Args:
            image: 输入图像，支持以下格式：
                   - numpy.ndarray (BGR 或 RGB，由 OpenCV 或其他库读取)
                   - PIL.Image
                   - str (图像文件路径)
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
        
        # 处理不同输入类型
        if isinstance(image, str):
            # 文件路径
            pil_image = Image.open(image).convert('RGB')
        elif isinstance(image, np.ndarray):
            # numpy 数组（假设是 BGR，需要转换）
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
        
        # 预处理
        image_tensor = self.transform(pil_image).unsqueeze(0).to(self.device)
        
        # 推理
        with torch.no_grad():
            prob = self.model(image_tensor).item()
        
        return FlightResult(
            is_flying=prob > threshold,
            confidence=prob
        )
    
    def detect_batch(
        self,
        images: list,
        threshold: float = None,
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
        
        # 分批处理
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
                
                batch_tensors.append(self.transform(pil_image))
            
            if not batch_tensors:
                continue
            
            # 组合为批次
            batch_tensor = torch.stack(batch_tensors).to(self.device)
            
            # 推理
            with torch.no_grad():
                probs = self.model(batch_tensor).squeeze().cpu().numpy()
            
            # 处理单个元素的情况
            if probs.ndim == 0:
                probs = [probs.item()]
            
            for prob in probs:
                results.append(FlightResult(
                    is_flying=prob > threshold,
                    confidence=float(prob)
                ))
        
        return results


# 全局单例（延迟初始化）
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
