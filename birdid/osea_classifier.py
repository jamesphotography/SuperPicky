#!/usr/bin/env python3
"""
OSEA ResNet34 鸟类分类器

基于 OSEA 开源模型 (https://github.com/bird-feeder/OSEA)
支持 10,964 种鸟类识别

优化策略 (基于 test_preprocessing.py 实验):
- 中心裁剪预处理 (Resize 256 + CenterCrop 224): 置信度提升 ~15%
- 可选 TTA 模式 (原图 + 水平翻转): 额外提升 ~0.5%，但推理时间翻倍
"""

__version__ = "1.0.0"

import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import torch
from PIL import Image
from torchvision import models, transforms
from config import get_best_device


def _torch_load_compat(path: str, *, map_location: str, weights_only: bool):
    """torch.load wrapper that works across PyTorch versions."""
    try:
        return torch.load(path, map_location=map_location, weights_only=weights_only)
    except TypeError:
        # Older PyTorch does not support weights_only
        return torch.load(path, map_location=map_location)


def _should_retry_without_weights_only(error: Exception) -> bool:
    message = str(error)
    return (
        "weights_only" in message
        or "Weights only load failed" in message
        or "WeightsUnpickler" in message
    )


def _extract_state_dict(loaded_obj):
    if isinstance(loaded_obj, dict):
        if "state_dict" in loaded_obj:
            return loaded_obj["state_dict"]
        if "model_state_dict" in loaded_obj:
            return loaded_obj["model_state_dict"]
        return loaded_obj
    if isinstance(loaded_obj, torch.nn.Module):
        return loaded_obj.state_dict()
    raise TypeError(f"不支持的模型格式: {type(loaded_obj)}")


def _is_git_lfs_pointer_file(file_path: str) -> bool:
    """Detect Git LFS pointer files to avoid cryptic torch pickle errors."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(256)
    except OSError:
        return False
    return header.startswith(b"version https://git-lfs.github.com/spec/")


def _load_osea_checkpoint(model_path: str):
    if _is_git_lfs_pointer_file(model_path):
        raise RuntimeError(
            f"检测到 Git LFS 指针文件（未下载实际模型权重）: {model_path}"
        )
    try:
        return _torch_load_compat(model_path, map_location="cpu", weights_only=True)
    except Exception as e:
        if _should_retry_without_weights_only(e):
            print("[OSEA] weights_only=True 加载失败，回退 weights_only=False（仅限可信模型）")
            return _torch_load_compat(model_path, map_location="cpu", weights_only=False)
        raise

# ==================== 路径配置 ====================

def _get_birdid_dir() -> Path:
    """获取 birdid 模块目录"""
    return Path(__file__).parent


def _get_project_root() -> Path:
    """获取项目根目录"""
    return _get_birdid_dir().parent


def _get_resource_path(relative_path: str) -> Path:
    """获取资源路径 (支持 PyInstaller 打包)"""
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        base = _get_project_root()
    return base / relative_path


# ==================== 设备配置 ====================


DEVICE = get_best_device()


# ==================== 预处理 transforms ====================

CENTER_CROP_TRANSFORM = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

BASELINE_TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


# ==================== OSEA 分类器 ====================

class OSEAClassifier:
    """
    OSEA ResNet34 鸟类分类器

    Attributes:
        model: ResNet34 模型
        bird_info: 物种信息列表 [[cn_name, en_name, scientific_name], ...]，从 bird_reference.sqlite 加载
        transform: 图像预处理 transform
        num_classes: 物种数量 (10964)
    """

    DEFAULT_MODEL_PATH = "models/model20240824.pth"
    DEFAULT_DB_PATH = "birdid/data/bird_reference.sqlite"

    def __init__(
        self,
        model_path: Optional[str] = None,
        use_center_crop: bool = True,
        device: Optional[torch.device] = None,
    ):
        """
        初始化 OSEA 分类器

        Args:
            model_path: 模型文件路径 (默认: models/model20240824.pth)
            use_center_crop: 是否使用中心裁剪预处理 (推荐: True)
            device: 计算设备 (默认: 自动检测)
        """
        self.device = device or DEVICE
        self.use_center_crop = use_center_crop
        self.transform = CENTER_CROP_TRANSFORM if use_center_crop else BASELINE_TRANSFORM

        self.model_path = model_path or str(_get_resource_path(self.DEFAULT_MODEL_PATH))
        self.model = self._load_model()

        self.db_path = str(_get_resource_path(self.DEFAULT_DB_PATH))
        self.bird_info = self._load_bird_info()
        self.num_classes = len(self.bird_info)

        print(f"[OSEA] Model loaded: {self.num_classes} species, device: {self.device}")

    def _load_model(self) -> torch.nn.Module:
        """加载 ResNet34 模型"""
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"OSEA 模型未找到: {self.model_path}")

        try:
            loaded = _load_osea_checkpoint(self.model_path)
            if isinstance(loaded, torch.nn.Module):
                model = loaded
            else:
                model = models.resnet34(num_classes=11000)
                state_dict = _extract_state_dict(loaded)
                model.load_state_dict(state_dict)
        except Exception as e:
            raise RuntimeError(f"OSEA 模型加载失败: {e}")

        model.to(self.device)

        return model

    def _load_bird_info(self) -> List[List[str]]:
        """从 bird_reference.sqlite 加载物种信息"""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"数据库文件未找到: {self.db_path}")

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT model_class_id, chinese_simplified, english_name, scientific_name "
                "FROM BirdCountInfo WHERE model_class_id IS NOT NULL ORDER BY model_class_id"
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        num_classes = 10964
        bird_info: List[List[str]] = [['Unknown', 'Unknown', ''] for _ in range(num_classes)]
        for class_id, cn_name, en_name, scientific_name in rows:
            if 0 <= class_id < num_classes:
                bird_info[class_id] = [
                    cn_name or 'Unknown',
                    en_name or 'Unknown',
                    scientific_name or '',
                ]
        return bird_info

    def predict(
        self,
        image: Image.Image,
        top_k: int = 5,
        temperature: float = 1.0,
        ebird_species_set: Optional[Set[str]] = None,
    ) -> List[Dict]:
        """
        预测鸟类物种

        Args:
            image: PIL Image 对象 (RGB)
            top_k: 返回前 K 个结果
            temperature: softmax 温度参数 (1.0 为标准, <1 更尖锐, >1 更平滑)
            ebird_species_set: eBird 物种代码集合 (用于过滤)

        Returns:
            识别结果列表 [{cn_name, en_name, scientific_name, confidence, class_id}, ...]
        """
        if image.mode != 'RGB':
            image = image.convert('RGB')

        input_tensor = self.transform(image).unsqueeze(0).to(self.device)

        self.model.eval()
        with torch.no_grad():
            output = self.model(input_tensor)[0]

        output = output[:self.num_classes]
        probs = torch.nn.functional.softmax(output / temperature, dim=0)

        k = min(100 if ebird_species_set else top_k, self.num_classes)
        top_probs, top_indices = torch.topk(probs, k)

        results = []
        for i in range(len(top_indices)):
            class_id = top_indices[i].item()
            confidence = top_probs[i].item() * 100

            min_confidence = 0.3 if ebird_species_set else 1.0
            if confidence < min_confidence:
                continue

            info = self.bird_info[class_id]
            cn_name = info[0]
            en_name = info[1]
            scientific_name = info[2] if len(info) > 2 else None

            ebird_match = False

            results.append({
                'class_id': class_id,
                'cn_name': cn_name,
                'en_name': en_name,
                'scientific_name': scientific_name,
                'confidence': confidence,
                'ebird_match': ebird_match,
            })

            if len(results) >= top_k:
                break

        return results

    def predict_with_tta(
        self,
        image: Image.Image,
        top_k: int = 5,
        temperature: float = 1.0,
        ebird_species_set: Optional[Set[str]] = None,
    ) -> List[Dict]:
        """
        使用 TTA (Test-Time Augmentation) 预测

        TTA 策略: 原图 + 水平翻转取平均
        推理时间翻倍，但可能提高准确率
        """
        if image.mode != 'RGB':
            image = image.convert('RGB')

        input1 = self.transform(image).unsqueeze(0).to(self.device)

        flipped = image.transpose(Image.FLIP_LEFT_RIGHT)
        input2 = self.transform(flipped).unsqueeze(0).to(self.device)

        self.model.eval()
        with torch.no_grad():
            output1 = self.model(input1)[0][:self.num_classes]
            output2 = self.model(input2)[0][:self.num_classes]

        avg_output = (output1 + output2) / 2
        probs = torch.nn.functional.softmax(avg_output / temperature, dim=0)

        k = min(100 if ebird_species_set else top_k, self.num_classes)
        top_probs, top_indices = torch.topk(probs, k)

        results = []
        for i in range(len(top_indices)):
            class_id = top_indices[i].item()
            confidence = top_probs[i].item() * 100

            min_confidence = 0.3 if ebird_species_set else 1.0
            if confidence < min_confidence:
                continue

            info = self.bird_info[class_id]
            cn_name = info[0]
            en_name = info[1]
            scientific_name = info[2] if len(info) > 2 else None

            results.append({
                'class_id': class_id,
                'cn_name': cn_name,
                'en_name': en_name,
                'scientific_name': scientific_name,
                'confidence': confidence,
                'ebird_match': False,
            })

            if len(results) >= top_k:
                break

        return results


# ==================== 全局单例 ====================

_osea_classifier: Optional[OSEAClassifier] = None


def get_osea_classifier() -> OSEAClassifier:
    """获取 OSEA 分类器单例"""
    global _osea_classifier
    if _osea_classifier is None:
        _osea_classifier = OSEAClassifier()
    return _osea_classifier


# ==================== 便捷函数 ====================

def osea_predict(image: Image.Image, top_k: int = 5) -> List[Dict]:
    """快速 OSEA 预测"""
    classifier = get_osea_classifier()
    return classifier.predict(image, top_k=top_k)


def osea_predict_file(image_path: str, top_k: int = 5) -> List[Dict]:
    """OSEA 预测 (从文件路径)"""
    from birdid.bird_identifier import load_image
    image = load_image(image_path)
    return osea_predict(image, top_k=top_k)


# ==================== 测试 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OSEA 鸟类分类器测试")
    parser.add_argument("image", help="测试图片路径")
    parser.add_argument("--top-k", type=int, default=5, help="返回前 K 个结果")
    parser.add_argument("--tta", action="store_true", help="使用 TTA 模式")
    args = parser.parse_args()

    from birdid.bird_identifier import load_image
    image = load_image(args.image)

    classifier = OSEAClassifier()

    if args.tta:
        results = classifier.predict_with_tta(image, top_k=args.top_k)
        print(f"\n[OSEA TTA 预测结果] 前 {args.top_k} 名:")
    else:
        results = classifier.predict(image, top_k=args.top_k)
        print(f"\n[OSEA 预测结果] 前 {args.top_k} 名:")

    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['cn_name']} ({r['en_name']})")
        print(f"     学名: {r['scientific_name']}")
        print(f"     置信度: {r['confidence']:.1f}%")
