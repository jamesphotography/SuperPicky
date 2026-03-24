"""
关键点检测器模块
使用 CUB-200 训练的 ResNet50 模型检测鸟类关键点（左眼、右眼、喙）
"""

import os
import math
import torch
import torch.nn as nn
import torchvision.models as models
from torchvision import transforms
from PIL import Image
import numpy as np
import cv2
from dataclasses import dataclass
from typing import Optional, Tuple
from config import get_best_device


@dataclass
class KeypointResult:
    """关键点检测结果"""
    left_eye: Tuple[float, float]      # (x, y) 归一化坐标
    right_eye: Tuple[float, float]
    beak: Tuple[float, float]
    left_eye_vis: float                # 可见性概率 0-1
    right_eye_vis: float
    beak_vis: float
    
    # 派生属性
    both_eyes_hidden: bool             # 双眼是否都不可见（保留兼容）
    all_keypoints_hidden: bool         # 所有关键点（双眼+鸟喙）都不可见
    best_eye_visibility: float         # 双眼中较高的置信度 max(左眼, 右眼)
    visible_eye: Optional[str]         # 'left', 'right', 'both', None
    head_sharpness: float              # 头部区域锐度


class PartLocalizer(nn.Module):
    """关键点定位模型"""
    def __init__(self, num_parts=3, hidden_dim=512, dropout=0.2):
        super().__init__()
        self.num_parts = num_parts
        self.backbone = models.resnet50(weights=None)
        in_features = self.backbone.fc.in_features
        self.backbone.fc = nn.Identity()

        self.head = nn.Sequential(
            nn.Linear(in_features, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.BatchNorm1d(hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.coord_head = nn.Linear(hidden_dim // 2, num_parts * 2)
        self.vis_head = nn.Linear(hidden_dim // 2, num_parts)

    def forward(self, x):
        features = self.head(self.backbone(x))
        coords = torch.sigmoid(self.coord_head(features)).view(-1, self.num_parts, 2)
        vis = torch.sigmoid(self.vis_head(features))
        return coords, vis


class KeypointDetector:
    """鸟类关键点检测器"""
    
    # 默认配置
    IMG_SIZE = 416
    VISIBILITY_THRESHOLD = 0.3  # 至少一个关键点可见性需≥0.3才不算"全部不可见"
    RADIUS_MULTIPLIER = 1.2         # 有喙时的半径系数
    NO_BEAK_RADIUS_RATIO = 0.15     # 无喙时用检测框的15%
    
    @staticmethod
    def _get_default_model_path() -> str:
        """获取默认模型路径（支持 PyInstaller 打包）"""
        import sys
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller 打包后的路径
            return os.path.join(sys._MEIPASS, 'models', 'cub200_keypoint_resnet50_slim.pth')
        else:
            # 开发环境：从当前文件向上找项目根目录
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            return os.path.join(project_root, 'models', 'cub200_keypoint_resnet50_slim.pth')
    
    def __init__(self, model_path: str = None):
        """
        初始化关键点检测器
        
        Args:
            model_path: 模型文件路径，默认使用自动检测的路径
        """
        self.model_path = model_path or self._get_default_model_path()
        # 使用统一的设备检测逻辑
        self.device = get_best_device()
        self.model = None
        self.transform = transforms.Compose([
            transforms.Resize((self.IMG_SIZE, self.IMG_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
        
    def load_model(self):
        """加载模型（懒加载）"""
        if self.model is not None:
            return
            
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"关键点模型不存在: {self.model_path}")
        
        self.model = PartLocalizer()
        checkpoint = torch.load(self.model_path, map_location=self.device, weights_only=True)
        
        if isinstance(checkpoint, dict) and 'model_state_dict' in checkpoint:
            self.model.load_state_dict(checkpoint['model_state_dict'])
        else:
            self.model.load_state_dict(checkpoint)
            
        self.model.to(self.device)
        
        # V4.0.5: 启用 FP16 半精度推理，提速约 30-50%
        # MPS 和 CUDA 都支持 FP16
        if self.device.type in ('mps', 'cuda'):
            self.model = self.model.half()
            self._use_fp16 = True
        else:
            self._use_fp16 = False
            
        self.model.eval()
    
    def detect(self, bird_crop: np.ndarray, box: Tuple[int, int, int, int] = None, 
               seg_mask: np.ndarray = None) -> Optional[KeypointResult]:
        """
        检测鸟类关键点并计算头部锐度
        
        Args:
            bird_crop: 裁剪的鸟区域图像 (RGB格式)
            box: 原始检测框 (x1, y1, x2, y2)，用于fallback半径计算
            seg_mask: 分割掩码（原图尺寸），用于精确计算头部区域
            
        Returns:
            KeypointResult 或 None（如果检测失败）
        """
        self.load_model()
        
        if bird_crop is None or bird_crop.size == 0:
            return None
        
        # 转换为PIL并进行推理
        pil_crop = Image.fromarray(bird_crop)
        tensor = self.transform(pil_crop).unsqueeze(0).to(self.device)
        
        # V4.0.5: 使用 FP16 和 inference_mode 优化推理
        if hasattr(self, '_use_fp16') and self._use_fp16:
            tensor = tensor.half()
        
        with torch.inference_mode():
            coords, vis = self.model(tensor)
        
        coords = coords[0].cpu().numpy()
        vis = vis[0].cpu().numpy()
        
        # 解析结果
        left_eye = (float(coords[0, 0]), float(coords[0, 1]))
        right_eye = (float(coords[1, 0]), float(coords[1, 1]))
        beak = (float(coords[2, 0]), float(coords[2, 1]))
        
        left_eye_vis = float(vis[0])
        right_eye_vis = float(vis[1])
        beak_vis = float(vis[2])
        
        # 判断可见性
        left_visible = left_eye_vis >= self.VISIBILITY_THRESHOLD
        right_visible = right_eye_vis >= self.VISIBILITY_THRESHOLD
        beak_visible = beak_vis >= self.VISIBILITY_THRESHOLD
        
        # 保留旧属性（兼容）
        both_eyes_hidden = not left_visible and not right_visible
        # 新逻辑：只有当双眼和鸟喙都不可见时才算"全部不可见"
        all_keypoints_hidden = not left_visible and not right_visible and not beak_visible
        
        if left_visible and right_visible:
            visible_eye = 'both'
        elif left_visible:
            visible_eye = 'left'
        elif right_visible:
            visible_eye = 'right'
        else:
            visible_eye = None
        
        # 计算头部锐度
        head_sharpness = 0.0
        if visible_eye is not None:
            head_sharpness = self._calculate_head_sharpness(
                bird_crop, left_eye, right_eye, beak,
                left_eye_vis, right_eye_vis, beak_visible,
                box, seg_mask
            )
        
        # V3.8: 计算双眼中较高的置信度，用于评分封顶逻辑
        best_eye_visibility = max(left_eye_vis, right_eye_vis)
        
        return KeypointResult(
            left_eye=left_eye,
            right_eye=right_eye,
            beak=beak,
            left_eye_vis=left_eye_vis,
            right_eye_vis=right_eye_vis,
            beak_vis=beak_vis,
            both_eyes_hidden=both_eyes_hidden,
            all_keypoints_hidden=all_keypoints_hidden,
            best_eye_visibility=best_eye_visibility,
            visible_eye=visible_eye,
            head_sharpness=head_sharpness
        )
    
    def _calculate_head_sharpness(
        self, 
        bird_crop: np.ndarray,
        left_eye: Tuple[float, float],
        right_eye: Tuple[float, float],
        beak: Tuple[float, float],
        left_eye_vis: float,
        right_eye_vis: float,
        beak_visible: bool,
        box: Tuple[int, int, int, int] = None,
        seg_mask: np.ndarray = None
    ) -> float:
        """
        计算头部区域锐度
        
        使用眼睛为圆心，眼喙距离×1.2为半径，与seg掩码取交集
        """
        h, w = bird_crop.shape[:2]

        # 如果双眼都不可见（如鸟侧面、头部转向等）：
        # 模型坐标仍然大致指向头部位置，用置信度较高的那只眼做 fallback
        # 沿用与正常流程完全相同的"圆形区域 Sobel"算法，结果 ×0.8 作为惩罚
        # 这样与正常眼睛检测的锐度值在同一量级，不会因用全身 Sobel 而虚高
        if left_eye_vis < self.VISIBILITY_THRESHOLD and right_eye_vis < self.VISIBILITY_THRESHOLD:
            eye = left_eye if left_eye_vis >= right_eye_vis else right_eye
            eye_px = (int(eye[0] * w), int(eye[1] * h))
            beak_px = (int(beak[0] * w), int(beak[1] * h))
            if beak_vis >= self.VISIBILITY_THRESHOLD:
                radius = int(self._distance(eye_px, beak_px) * self.RADIUS_MULTIPLIER)
            elif box is not None:
                box_size = max(box[2], box[3])
                radius = int(box_size * self.NO_BEAK_RADIUS_RATIO)
            else:
                radius = int(max(w, h) * self.NO_BEAK_RADIUS_RATIO)
            radius = max(10, min(radius, min(w, h) // 2))
            circle_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(circle_mask, eye_px, radius, 255, -1)
            if seg_mask is not None and seg_mask.shape[:2] == (h, w):
                head_mask = cv2.bitwise_and(circle_mask, seg_mask)
            else:
                head_mask = circle_mask
            LOW_VIS_PENALTY = 0.8  # 眼睛不可见时降分但不误杀
            return self._calculate_sharpness(bird_crop, head_mask) * LOW_VIS_PENALTY


        # 选择眼睛：用更远离喙的那只眼
        if left_eye_vis >= self.VISIBILITY_THRESHOLD and right_eye_vis >= self.VISIBILITY_THRESHOLD:
            # 两眼都可见，选更远离喙的
            left_dist = self._distance(left_eye, beak)
            right_dist = self._distance(right_eye, beak)
            eye = left_eye if left_dist >= right_dist else right_eye
        elif left_eye_vis >= self.VISIBILITY_THRESHOLD:
            eye = left_eye
        else:
            # 只有一只眼可见（右眼）
            eye = right_eye
        
        # 转换为像素坐标
        eye_px = (int(eye[0] * w), int(eye[1] * h))
        beak_px = (int(beak[0] * w), int(beak[1] * h))
        
        # 计算半径
        if beak_visible:
            radius = int(self._distance(eye_px, beak_px) * self.RADIUS_MULTIPLIER)
        elif box is not None:
            # 无喙时用检测框的15%
            # box 格式是 (x, y, w, h)，所以 box[2]=width, box[3]=height
            box_size = max(box[2], box[3])
            radius = int(box_size * self.NO_BEAK_RADIUS_RATIO)
        else:
            # 最后fallback：用裁剪区域的15%
            radius = int(max(w, h) * self.NO_BEAK_RADIUS_RATIO)
        
        # 确保半径合理
        radius = max(10, min(radius, min(w, h) // 2))
        
        # 创建圆形掩码
        circle_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(circle_mask, eye_px, radius, 255, -1)
        
        # 如果有seg掩码，取交集
        if seg_mask is not None:
            # seg_mask可能需要裁剪到bird_crop区域
            # 这里假设bird_crop已经是裁剪后的，seg_mask也已相应处理
            if seg_mask.shape[:2] == (h, w):
                head_mask = cv2.bitwise_and(circle_mask, seg_mask)
            else:
                head_mask = circle_mask
        else:
            head_mask = circle_mask
        
        # 计算锐度
        return self._calculate_sharpness(bird_crop, head_mask)
    
    def _calculate_sharpness(self, image: np.ndarray, mask: np.ndarray) -> float:
        """
        计算掩码区域的锐度（Tenengrad + 对数归一化）
        
        V3.7 改动: 使用 Tenengrad (Sobel梯度) 替代 Laplacian以减少噪点干扰
        并使用对数归一化将结果映射到 0-1000 范围
        """
        if mask.sum() == 0:
            return 0.0
        
        # 转灰度
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
        
        # Tenengrad 算子 (Sobel梯度平方和)
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        gradient_magnitude = gx ** 2 + gy ** 2
        
        # 只取掩码区域的平均值
        mask_pixels = mask > 0
        if mask_pixels.sum() == 0:
            return 0.0
            
        raw_sharpness = float(gradient_magnitude[mask_pixels].mean())
        
        # 对数归一化到 0-1000
        # V4.0 修复: 降低 MIN_VAL，之前 1460 太高导致锐利照片也返回 0
        # 测试显示: 锐利照片梯度平均值约 800-2000
        MIN_VAL = 100.0   # 降低阈值，保留更多低锐度信息
        MAX_VAL = 154016.0
        
        if raw_sharpness <= MIN_VAL:
            return 0.0
        if raw_sharpness >= MAX_VAL:
            return 1000.0
            
        log_val = math.log(raw_sharpness) - math.log(MIN_VAL)
        log_max = math.log(MAX_VAL) - math.log(MIN_VAL)
        
        return (log_val / log_max) * 1000.0
    
    @staticmethod
    def _distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        """计算两点距离"""
        return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)


# 便捷函数
_detector_instance = None

def get_keypoint_detector() -> KeypointDetector:
    """获取全局关键点检测器实例"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = KeypointDetector()
    return _detector_instance
