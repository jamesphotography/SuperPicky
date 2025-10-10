"""
鸟类检测器 - 核心层
负责AI模型加载、图像处理和鸟类检测
"""
import os
import cv2
import numpy as np
from ultralytics import YOLO
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from .config_manager import config_manager
from .file_manager import file_manager
from improved_sharpness import improved_sharpness_calculator, select_largest_bird


@dataclass
class DetectionResult:
    """检测结果数据类"""
    found_bird: bool
    bird_selected: bool
    confidence: float
    bird_area_ratio: float
    bird_center_x: float
    bird_center_y: float
    sharpness: float
    real_sharpness: float
    is_dominant: bool
    is_centered: bool
    is_sharp: bool
    class_id: int
    crop_saved: bool = False
    # 新增改进算法字段
    laplacian_var: float = 0.0
    sobel_var: float = 0.0
    fft_high_freq: float = 0.0
    contrast: float = 0.0
    edge_density: float = 0.0
    background_complexity: float = 0.0
    motion_blur: float = 0.0
    normalized_new: float = 0.0
    composite_score: float = 0.0
    result_new: bool = False


@dataclass  
class ProcessingThresholds:
    """处理阈值数据类"""
    ai_confidence: float
    area_threshold: float  
    sharpness_threshold: float
    center_threshold: float


class BirdDetector:
    """鸟类检测器，处理AI模型和图像检测"""
    
    def __init__(self):
        self.config = config_manager
        self.file_manager = file_manager
        self._model: Optional[YOLO] = None
    
    # ============ 模型管理 ============
    def load_model(self) -> YOLO:
        """加载YOLO模型"""
        if self._model is None:
            model_path = self.config.get_model_path()
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"Model file not found: {model_path}")
            
            self._model = YOLO(str(model_path))
            
        return self._model
    
    def get_model(self) -> Optional[YOLO]:
        """获取已加载的模型"""
        return self._model
    
    # ============ 图像预处理 ============
    def preprocess_image(self, image_path: str, target_size: Optional[int] = None) -> np.ndarray:
        """
        预处理图像
        
        Args:
            image_path: 图像文件路径
            target_size: 目标尺寸，None则使用配置默认值
            
        Returns:
            预处理后的图像数组
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")
        
        if target_size is None:
            target_size = self.config.get_target_image_size()
        
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Failed to load image: {image_path}")
        
        h, w = img.shape[:2]
        scale = target_size / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return img
    
    def calculate_sharpness(self, image: np.ndarray) -> float:
        """计算图像清晰度"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        sharpness = cv2.norm(laplacian, cv2.NORM_L2)
        return sharpness
    
    # ============ 鸟类检测 ============
    def detect_birds_in_image(self, image_path: str, thresholds: ProcessingThresholds,
                             crop_temp_dir: Optional[str] = None) -> Optional[DetectionResult]:
        """
        在图像中检测鸟类
        
        Args:
            image_path: 图像文件路径
            thresholds: 处理阈值
            crop_temp_dir: 裁剪图片保存目录
            
        Returns:
            DetectionResult 或 None（如果处理失败）
        """
        # 验证输入
        if not self.config.is_supported_image_file(image_path):
            self.file_manager.write_log("ERROR: not a supported image file", os.path.dirname(image_path))
            return None
        
        try:
            # 加载模型和预处理图像
            model = self.load_model()
            image = self.preprocess_image(image_path)
            height, width, _ = image.shape
            
            # 运行检测
            results = model(image)
            detections = results[0].boxes.xyxy.cpu().numpy()
            confidences = results[0].boxes.conf.cpu().numpy()
            class_ids = results[0].boxes.cls.cpu().numpy()
            
            work_dir = os.path.dirname(image_path)
            
            # 选择面积最大的鸟类
            largest_bird = select_largest_bird(detections, confidences, class_ids, 
                                             self.config.get_bird_class_id())
            
            if largest_bird:
                detection, conf, class_id = largest_bird
                return self._process_bird_detection(
                    image, detection, conf, class_id, image_path,
                    thresholds, crop_temp_dir, width, height
                )
            
            # 没有发现鸟类
            return DetectionResult(
                found_bird=False, bird_selected=False, confidence=0.0,
                bird_area_ratio=0.0, bird_center_x=0.0, bird_center_y=0.0,
                sharpness=0.0, real_sharpness=0.0, is_dominant=False,
                is_centered=False, is_sharp=False, class_id=-1
            )
            
        except Exception as e:
            work_dir = os.path.dirname(image_path)
            self.file_manager.write_log(f"ERROR in bird detection: {e}", work_dir)
            return None
    
    def _process_bird_detection(self, image: np.ndarray, detection: np.ndarray,
                               confidence: float, class_id: int, image_path: str,
                               thresholds: ProcessingThresholds, crop_temp_dir: Optional[str],
                               width: int, height: int) -> DetectionResult:
        """处理单个鸟类检测结果"""
        x1, y1, x2, y2 = detection
        x, y, w, h = int(x1), int(y1), int(x2 - x1), int(y2 - y1)
        
        # 边界检查和调整
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = min(w, width - x)
        h = min(h, height - y)
        
        if w <= 0 or h <= 0:
            work_dir = os.path.dirname(image_path)
            self.file_manager.write_log(f"ERROR: Invalid crop region for {image_path}", work_dir)
            return DetectionResult(
                found_bird=True, bird_selected=False, confidence=confidence,
                bird_area_ratio=0.0, bird_center_x=0.0, bird_center_y=0.0,
                sharpness=0.0, real_sharpness=0.0, is_dominant=False,
                is_centered=False, is_sharp=False, class_id=int(class_id)
            )
        
        # 裁剪和分析鸟类区域
        crop_img = image[y:y + h, x:x + w]
        if crop_img is None or crop_img.size == 0:
            work_dir = os.path.dirname(image_path)
            self.file_manager.write_log(f"ERROR: Crop image is empty for {image_path}", work_dir)
            return DetectionResult(
                found_bird=True, bird_selected=False, confidence=confidence,
                bird_area_ratio=0.0, bird_center_x=0.0, bird_center_y=0.0,
                sharpness=0.0, real_sharpness=0.0, is_dominant=False,
                is_centered=False, is_sharp=False, class_id=int(class_id)
            )
        
        # 计算各项指标
        area_ratio = (w * h) / (width * height)
        center_x = (x + w / 2) / width
        center_y = (y + h / 2) / height
        
        # 原始锐度计算（保持兼容性）
        real_sharpness = self.calculate_sharpness(crop_img)
        s_area_ratio = round((area_ratio * 1000) ** (1 / 2), 2)
        sharpness = real_sharpness / s_area_ratio if s_area_ratio > 0 else 0
        
        # 新的改进锐度计算
        improved_metrics = improved_sharpness_calculator.calculate_comprehensive_sharpness(
            crop_img, area_ratio, (width, height)
        )
        
        # 判断各项条件
        is_confident = confidence > thresholds.ai_confidence
        is_dominant = area_ratio >= thresholds.area_threshold
        
        center_threshold = thresholds.center_threshold
        is_centered = (center_threshold <= center_x <= (1 - center_threshold) and
                      center_threshold <= center_y <= (1 - center_threshold))
        
        # 新算法的判断条件（使用综合评分）
        composite_threshold = 0.15
        is_sharp_new = improved_metrics['composite_score'] >= composite_threshold
        
        # 主要使用新算法的判定结果
        bird_selected = is_confident and is_dominant and is_sharp_new and is_centered
        bird_selected_new = bird_selected  # 统一使用新算法结果
        
        # 保存裁剪图片
        crop_saved = False
        if crop_temp_dir:
            try:
                filename = os.path.basename(image_path)
                crop_path = os.path.join(crop_temp_dir, 'Crop_' + filename)
                cv2.imwrite(crop_path, crop_img)
                crop_saved = True
            except Exception as e:
                work_dir = os.path.dirname(image_path)
                self.file_manager.write_log(f"ERROR saving crop image: {e}", work_dir)
        
        # 在原图上绘制检测框（可选）
        cv2.rectangle(image, (x, y), (x + w, y + h), (0, 0, 255), 2)
        
        # 记录检测信息
        work_dir = os.path.dirname(image_path)
        self.file_manager.write_log(
            f" AI: {confidence:.2f} - Class: {int(class_id)} - "
            f"Sharpness:{real_sharpness:.0f} - Area:{area_ratio * 100:.2f}% - "
            f"Center_x:{center_x:.2f} - Center_y:{center_y:.2f}",
            work_dir
        )
        
        return DetectionResult(
            found_bird=True,
            bird_selected=bird_selected,
            confidence=confidence,
            bird_area_ratio=area_ratio,
            bird_center_x=center_x,
            bird_center_y=center_y,
            sharpness=sharpness,  # 保留旧算法值用于兼容
            real_sharpness=real_sharpness,
            is_dominant=is_dominant,
            is_centered=is_centered,
            is_sharp=is_sharp_new,  # 使用新算法的锐度判断
            class_id=int(class_id),
            crop_saved=crop_saved,
            # 新增改进算法字段
            laplacian_var=improved_metrics['laplacian_var'],
            sobel_var=improved_metrics['sobel_var'],
            fft_high_freq=improved_metrics['fft_high_freq'],
            contrast=improved_metrics['contrast'],
            edge_density=improved_metrics['edge_density'],
            background_complexity=improved_metrics['background_complexity'],
            motion_blur=improved_metrics['motion_blur'],
            normalized_new=improved_metrics['normalized_new'],
            composite_score=improved_metrics['composite_score'],
            result_new=bird_selected_new
        )
    
    # ============ 结果转换 ============
    def detection_result_to_csv_data(self, result: DetectionResult, filename: str) -> Dict[str, Any]:
        """将检测结果转换为CSV数据格式 - 完全采用新算法"""
        file_prefix = os.path.splitext(filename)[0]
        
        return {
            # 基本信息
            "filename": file_prefix,
            "found_bird": result.found_bird,
            "AI score": f"{result.confidence:.2f}",
            "bird_centre_x": f"{result.bird_center_x:.2f}",
            "bird_centre_y": f"{result.bird_center_y:.2f}",
            "bird_area": f"{result.bird_area_ratio * 100:.2f}%",
            "s_bird_area": f"{round((result.bird_area_ratio * 1000) ** (1 / 2), 2)}%",
            # 新算法 - 核心指标
            "laplacian_var": f"{result.laplacian_var:.2f}",
            "sobel_var": f"{result.sobel_var:.2f}",
            "fft_high_freq": f"{result.fft_high_freq:.4f}",
            "contrast": f"{result.contrast:.2f}",
            "edge_density": f"{result.edge_density:.4f}",
            "background_complexity": f"{result.background_complexity:.4f}",
            "motion_blur": f"{result.motion_blur:.4f}",
            "normalized_new": f"{result.normalized_new:.2f}",
            "composite_score": f"{result.composite_score:.4f}",
            "result_new": result.result_new,  # 新算法的最终判定结果
            # 位置和基本判断
            "dominant_bool": result.is_dominant,
            "centred_bool": result.is_centered,
            "sharp_bool": result.is_sharp,  # 基于新算法的锐度判断
            "class_id": result.class_id
        }


# 全局鸟类检测器实例
bird_detector = BirdDetector()