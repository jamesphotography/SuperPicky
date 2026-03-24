#!/usr/bin/env python3
"""
鸟类识别核心模块
从 SuperBirdID 移植，提供鸟类检测与分类识别功能
"""

__version__ = "1.0.0"

import torch
import torchvision.transforms as transforms
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from PIL.ExifTags import TAGS, GPSTAGS
import cv2
import io
import os
import sys
from typing import Optional, List, Dict, Tuple, Set
from tools.i18n import t as _t
from config import get_best_device

# ==================== 设备配置 ====================

CLASSIFIER_DEVICE = get_best_device()

# ==================== 可选依赖检测 ====================

# RAW格式支持
try:
    import rawpy
    import imageio
    RAW_SUPPORT = True
except ImportError:
    RAW_SUPPORT = False

# YOLO检测支持
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

# ==================== 路径配置 ====================

# birdid 模块目录
BIRDID_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录
PROJECT_ROOT = os.path.dirname(BIRDID_DIR)


def get_birdid_path(relative_path: str) -> str:
    """获取 birdid 模块内的资源路径"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包环境
        return os.path.join(sys._MEIPASS, 'birdid', relative_path)
    return os.path.join(BIRDID_DIR, relative_path)


def get_project_path(relative_path: str) -> str:
    """获取项目根目录下的资源路径"""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(PROJECT_ROOT, relative_path)


def get_user_data_dir() -> str:
    """获取用户数据目录"""
    if sys.platform == 'darwin':
        user_data_dir = os.path.expanduser('~/Documents/SuperPicky_Data')
    elif sys.platform == 'win32':
        user_data_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'SuperPicky_Data')
    else:
        user_data_dir = os.path.join(os.path.expanduser('~'), 'Documents', 'SuperPicky_Data')
    os.makedirs(user_data_dir, exist_ok=True)
    return user_data_dir


# ==================== 模型路径 ====================
# 鸟类识别专用模型和数据（在 birdid/ 目录下）
# OSEA ResNet34 模型（替代旧 birdid2024）
MODEL_PATH = get_project_path('models/model20240824.pth')
# 旧模型路径（保留作为回退）
MODEL_PATH_LEGACY = get_birdid_path('models/birdid2024.pt')
MODEL_PATH_ENC = get_birdid_path('models/birdid2024.pt.enc')
# OSEA 模型类别数
OSEA_NUM_CLASSES = 11000
DATABASE_PATH = get_birdid_path('data/bird_reference.sqlite')

# YOLO 模型（共用项目根目录的模型）
YOLO_MODEL_PATH = get_project_path('models/yolo11l-seg.pt')

# ==================== 全局变量（懒加载）====================
_classifier = None
_db_manager = None
_yolo_detector = None

# V4.0.5: 离线物种过滤
_avonet_filter = None  # AvonetFilter 单例


# ==================== 模型加密解密 ====================

def decrypt_model(encrypted_path: str, password: str) -> bytes:
    """解密模型文件"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    with open(encrypted_path, 'rb') as f:
        encrypted_data = f.read()

    salt = encrypted_data[:16]
    iv = encrypted_data[16:32]
    ciphertext = encrypted_data[32:]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    key = kdf.derive(password.encode())

    cipher = Cipher(
        algorithms.AES(key),
        modes.CBC(iv),
        backend=default_backend()
    )
    decryptor = cipher.decryptor()
    plaintext_padded = decryptor.update(ciphertext) + decryptor.finalize()

    padding_length = plaintext_padded[-1]
    return plaintext_padded[:-padding_length]


def _load_torchscript_from_bytes(model_data: bytes):
    """Load TorchScript from bytes to avoid Windows non-ASCII temp path issues."""
    buffer = io.BytesIO(model_data)
    return torch.jit.load(buffer, map_location='cpu')


# ==================== 懒加载函数 ====================

def get_classifier():
    """懒加载分类模型（OSEA ResNet34）"""
    global _classifier
    if _classifier is None:
        import torchvision.models as models

        if os.path.exists(MODEL_PATH):
            # 加载 OSEA ResNet34 模型
            model = models.resnet34(num_classes=OSEA_NUM_CLASSES)
            state_dict = torch.load(MODEL_PATH, map_location='cpu', weights_only=True)
            model.load_state_dict(state_dict)
            model = model.to(CLASSIFIER_DEVICE)
            model.eval()
            _classifier = model
            print(f"[BirdID] OSEA ResNet34 model loaded, device: {CLASSIFIER_DEVICE}")
        else:
            # 回退到旧的 birdid2024 模型
            SECRET_PASSWORD = "SuperBirdID_2024_AI_Model_Encryption_Key_v1"
            if os.path.exists(MODEL_PATH_ENC):
                model_data = decrypt_model(MODEL_PATH_ENC, SECRET_PASSWORD)
                _classifier = _load_torchscript_from_bytes(model_data)
            elif os.path.exists(MODEL_PATH_LEGACY):
                try:
                    _classifier = torch.jit.load(MODEL_PATH_LEGACY, map_location='cpu')
                except RuntimeError as e:
                    if 'open file failed' not in str(e) or 'fopen' not in str(e):
                        raise
                    with open(MODEL_PATH_LEGACY, 'rb') as f:
                        model_data = f.read()
                    _classifier = _load_torchscript_from_bytes(model_data)
            else:
                raise RuntimeError(f"未找到分类模型: {MODEL_PATH} 或 {MODEL_PATH_LEGACY}")
            _classifier.eval()
            print(_t("logs.birdid_fallback_model"))
    return _classifier


def get_bird_model():
    """获取识鸟模型（get_classifier 的别名，用于模型预加载）"""
    return get_classifier()


def get_database_manager():
    """懒加载数据库管理器"""
    global _db_manager
    if _db_manager is None:
        try:
            from birdid.bird_database_manager import BirdDatabaseManager
            if os.path.exists(DATABASE_PATH):
                _db_manager = BirdDatabaseManager(DATABASE_PATH)
        except Exception as e:
            print(_t("logs.db_load_failed", e=e))
            _db_manager = False
    return _db_manager if _db_manager is not False else None


def get_yolo_detector():
    """懒加载YOLO检测器"""
    global _yolo_detector
    if _yolo_detector is None and YOLO_AVAILABLE:
        if os.path.exists(YOLO_MODEL_PATH):
            _yolo_detector = YOLOBirdDetector(YOLO_MODEL_PATH)
    return _yolo_detector


def get_species_filter():
    """懒加载 AvonetFilter（单例模式）"""
    global _avonet_filter
    if _avonet_filter is None:
        try:
            from birdid.avonet_filter import AvonetFilter
            _avonet_filter = AvonetFilter()
            if _avonet_filter.is_available():
                print(_t("logs.avonet_loaded"))
            else:
                _avonet_filter = None
        except Exception as e:
            print(_t("logs.avonet_init_failed", e=e))
            return None
    return _avonet_filter


# ==================== YOLO 鸟类检测器 ====================

class YOLOBirdDetector:
    """YOLO 鸟类检测器"""

    def __init__(self, model_path: str = None):
        if not YOLO_AVAILABLE:
            self.model = None
            return

        if model_path is None:
            model_path = YOLO_MODEL_PATH

        try:
            self.model = YOLO(model_path)
        except Exception as e:
            print(_t("logs.yolo_load_failed", e=e))
            self.model = None

    def detect_and_crop_bird(
        self,
        image_input,
        confidence_threshold: float = 0.25,
        padding_ratio: float = 0.15,
        fill_color: Tuple[int, int, int] = (0, 0, 0)
    ) -> Tuple[Optional[Image.Image], str]:
        """
        检测并裁剪鸟类区域（智能正方形裁剪 + Letterboxing）

        处理流程:
        1. YOLO 检测获取 bounding box
        2. 智能正方形扩展: max_side * (1 + padding_ratio)
        3. 边界限制: 裁剪区域不超出图片范围
        4. Letterboxing: 如果裁剪后非正方形，用 fill_color 填充成正方形

        Args:
            image_input: 文件路径或 PIL Image
            confidence_threshold: 置信度阈值
            padding_ratio: padding 比例（基于 bbox 最大边长），默认 0.15 (15%)
            fill_color: Letterboxing 填充颜色，默认黑色 (0, 0, 0)

        Returns:
            (裁剪后的正方形图像, 检测信息) 或 (None, 错误信息)
        """
        if self.model is None:
            return None, "YOLO模型未可用"

        try:
            if isinstance(image_input, str):
                image = load_image(image_input)
            elif isinstance(image_input, Image.Image):
                image = image_input
            else:
                return None, "不支持的图像输入类型"

            img_array = np.array(image)
            results = self.model(img_array, conf=confidence_threshold)

            detections = []
            for result in results:
                boxes = result.boxes
                if boxes is not None:
                    for box in boxes:
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        confidence = box.conf[0].cpu().numpy()
                        class_id = int(box.cls[0].cpu().numpy())

                        # COCO 数据集中鸟类的 class_id 是 14
                        if class_id == 14:
                            detections.append({
                                'bbox': [int(x1), int(y1), int(x2), int(y2)],
                                'confidence': float(confidence)
                            })

            if not detections:
                return None, _t("logs.no_bird_detected")

            best = max(detections, key=lambda x: x['confidence'])
            img_width, img_height = image.size

            # Phase 1: 获取 bbox
            x1, y1, x2, y2 = best['bbox']
            bbox_width = x2 - x1
            bbox_height = y2 - y1

            # Phase 2: 智能正方形扩展 (基于最大边长 + padding_ratio)
            max_side = max(bbox_width, bbox_height)
            target_side = int(max_side * (1 + padding_ratio))

            # 以 bbox 中心为基准扩展
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            half = target_side // 2

            sq_x1 = cx - half
            sq_y1 = cy - half
            sq_x2 = cx + half
            sq_y2 = cy + half

            # Phase 3: 边界限制
            crop_x1 = max(0, sq_x1)
            crop_y1 = max(0, sq_y1)
            crop_x2 = min(img_width, sq_x2)
            crop_y2 = min(img_height, sq_y2)

            cropped = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            crop_w, crop_h = cropped.size

            # Phase 4: Letterboxing (如果裁剪后非正方形)
            if crop_w != crop_h:
                sq_size = max(crop_w, crop_h)
                square = Image.new('RGB', (sq_size, sq_size), fill_color)
                paste_x = (sq_size - crop_w) // 2
                paste_y = (sq_size - crop_h) // 2
                square.paste(cropped, (paste_x, paste_y))
                cropped = square

            info = f"conf={best['confidence']:.3f}, size={cropped.size}"

            return cropped, info

        except Exception as e:
            return None, f"检测失败: {e}"


# ==================== 图像加载 ====================

def load_image(image_path: str) -> Image.Image:
    """
    加载图像，支持标准格式和 RAW 格式
    对 RAW 文件优先提取内嵌 JPEG 预览图（更适合 YOLO 检测）
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"文件不存在: {image_path}")

    ext = os.path.splitext(image_path)[1].lower()

    raw_extensions = [
        '.cr2', '.cr3', '.nef', '.nrw', '.arw', '.srf', '.dng',
        '.raf', '.orf', '.rw2', '.pef', '.srw', '.raw', '.rwl',
        '.3fr', '.fff', '.erf', '.mef', '.mos', '.mrw', '.x3f',
        '.hif', '.heif', '.heic',   # Sony HIF / HEIF
    ]

    # HEIF 格式（rawpy 不支持）：直接补 pillow-heif 路径
    heif_extensions = {'.hif', '.heif', '.heic'}

    if ext in raw_extensions:
        if ext in heif_extensions:
            return _load_heif(image_path)
        if RAW_SUPPORT:
            try:
                with rawpy.imread(image_path) as raw:
                    # 优先尝试提取内嵌的 JPEG 预览图
                    try:
                        thumb = raw.extract_thumb()
                        if thumb.format == rawpy.ThumbFormat.JPEG:
                            # 直接使用内嵌的 JPEG
                            from io import BytesIO
                            img = Image.open(BytesIO(thumb.data)).convert("RGB")
                            print(_t("logs.raw_embedded_jpeg", w=img.size[0], h=img.size[1]))
                            return img
                        elif thumb.format == rawpy.ThumbFormat.BITMAP:
                            # 位图格式
                            img = Image.fromarray(thumb.data).convert("RGB")
                            print(_t("logs.raw_embedded_bitmap", w=img.size[0], h=img.size[1]))
                            return img
                    except Exception as e:
                        print(_t("logs.raw_preview_failed", e=e))
                    
                    # 如果无法提取预览，使用半尺寸后处理
                    rgb = raw.postprocess(
                        use_camera_wb=True,
                        output_bps=8,
                        no_auto_bright=False,
                        auto_bright_thr=0.01,
                        half_size=True  # 使用半尺寸，加快处理
                    )
                    img = Image.fromarray(rgb)
                    print(_t("logs.raw_half_size", w=img.size[0], h=img.size[1]))
                    return img
            except rawpy._rawpy.LibRawFileUnsupportedError:
                # LibRaw 不支持的格式（如 Sony A7M5 NeXt/Compressed RAW 2）
                # 回退：使用 exiftool -b -JpgFromRaw 提取相机内嵌 JPEG
                print(f"[RAW] rawpy 不支持此 RAW 格式，尝试 ExifTool JpgFromRaw 回退...")
                return _load_raw_via_exiftool(image_path)
            except Exception as e:
                raise Exception(f"RAW处理失败: {e}")
        else:
            raise ImportError("需要安装 rawpy 来处理 RAW 格式")
    else:
        return Image.open(image_path).convert("RGB")


def _load_raw_via_exiftool(image_path: str) -> "Image.Image":
    """
    使用 ExifTool 从 RAW 文件提取内嵌 JPEG。
    用于 LibRaw 不支持的格式（如 Sony A7M5 NeXt/Compressed RAW 2）。
    按优先级依次尝试：JpgFromRaw → PreviewImage → ThumbnailImage
    """
    import subprocess
    from io import BytesIO

    # 查找 exiftool（优先使用打包内的版本）
    possible_paths = []
    if getattr(sys, "frozen", False):
        possible_paths.append(os.path.join(sys._MEIPASS, "exiftools_mac", "exiftool"))
    possible_paths += [
        os.path.join(PROJECT_ROOT, "exiftools_mac", "exiftool"),
        "/opt/homebrew/bin/exiftool",
        "/usr/local/bin/exiftool",
        "exiftool",
    ]
    exiftool = next((p for p in possible_paths if os.path.isfile(p)), "exiftool")

    # 依次尝试各种嵌入图像标签
    for tag in ["-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"]:
        try:
            result = subprocess.run(
                [exiftool, "-b", tag, image_path],
                capture_output=True, timeout=15
            )
            if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
                img = Image.open(BytesIO(result.stdout)).convert("RGB")
                print(f"[RAW] ExifTool {tag} 提取成功: {img.size[0]}x{img.size[1]}")
                return img
        except Exception as e:
            print(f"[RAW] ExifTool {tag} 失败: {e}")
            continue

    raise Exception(
        f"\u6682\u4e0d\u652f\u6301\u6b64 RAW \u683c\u5f0f\uff08{os.path.basename(image_path)}\uff09\u3002"
        "Sony A7M5 \u7b49\u76f8\u673a\u7684 NeXt/Compressed RAW 2 \u683c\u5f0f\u76ee\u524d\u7b2c\u4e09\u65b9\u5e93\u5c1a\u672a\u5b8c\u6574\u652f\u6301\uff0c"
        "\u5c06\u5728\u540e\u7eed\u7248\u672c\u4e2d\u4fee\u590d\u3002\u5efa\u8bae\u4e34\u65f6\u4f7f\u7528\u65e0\u538b\u7f29 RAW \u6216 JPEG \u683c\u5f0f\u62cd\u6444\u3002"
    )


def _load_heif(image_path: str) -> "Image.Image":
    """
    \u4f7f\u7528 pillow-heif \u89e3\u7801 HEIF/HIF \u6587\u4ef6\uff08Sony HIF \u3001\u82f9\u679c HEIC \u7b49\uff09\u4e3a PIL Image\u3002
    """
    try:
        import pillow_heif
        heif_file = pillow_heif.read_heif(image_path)
        img = Image.frombytes(
            heif_file.mode,
            heif_file.size,
            heif_file.data,
            "raw",
        ).convert("RGB")
        print(f"[HEIF] pillow-heif \u89e3\u7801\u6210\u529f: {img.size[0]}x{img.size[1]}")
        return img
    except ImportError:
        raise Exception(
            "\u8bf7\u5b89\u88c5 pillow-heif \u6765\u652f\u6301 HIF/HEIC \u683c\u5f0f\uff1a pip install pillow-heif"
        )
    except Exception as e:
        raise Exception(f"HEIF \u89e3\u7801\u5931\u8d25 ({os.path.basename(image_path)}): {e}")

# ==================== GPS 提取 ====================

def extract_gps_from_exif(image_path: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    从图像 EXIF 提取 GPS 坐标
    支持 RAW 文件（使用 exiftool）

    Returns:
        (纬度, 经度, 信息) 或 (None, None, 错误信息)
    """
    import subprocess
    import json as json_module
    
    # 首先尝试使用 exiftool（支持 RAW 格式）
    try:
        # 查找 exiftool
        exiftool_paths = [
            '/usr/local/bin/exiftool',
            '/opt/homebrew/bin/exiftool',
            'exiftool',  # 在 PATH 中查找
        ]
        
        exiftool_path = None
        for path in exiftool_paths:
            try:
                result = subprocess.run([path, '-ver'], capture_output=True, text=False, timeout=5)
                if result.returncode == 0:
                    # 解码输出
                    stdout_bytes = result.stdout
                    # 尝试多种编码解码
                    decoded_output = None
                    for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                        try:
                            decoded_output = stdout_bytes.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue
                    
                    if decoded_output is None:
                        # 如果所有编码都失败，使用 latin-1 作为最后手段（不会失败）
                        decoded_output = stdout_bytes.decode('latin-1')
                    
                    # 检查是否成功获取版本
                    if decoded_output.strip():
                        exiftool_path = path
                        break
            except:
                continue
        
        if exiftool_path:
            # 使用 exiftool 提取 GPS 信息
            result = subprocess.run(
                [exiftool_path, '-j', '-GPSLatitude', '-GPSLongitude', '-GPSLatitudeRef', '-GPSLongitudeRef', image_path],
                capture_output=True,
                text=False,  # 使用 bytes 模式，避免自动解码
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout:
                stdout_bytes = result.stdout
                # 尝试多种编码解码
                decoded_output = None
                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                    try:
                        decoded_output = stdout_bytes.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                
                if decoded_output is None:
                    # 如果所有编码都失败，使用 latin-1 作为最后手段（不会失败）
                    decoded_output = stdout_bytes.decode('latin-1')
                
                data = json_module.loads(decoded_output)
                if data and len(data) > 0:
                    gps_data = data[0]
                    
                    lat_str = gps_data.get('GPSLatitude', '')
                    lon_str = gps_data.get('GPSLongitude', '')
                    lat_ref = gps_data.get('GPSLatitudeRef', 'N')
                    lon_ref = gps_data.get('GPSLongitudeRef', 'E')
                    
                    if lat_str and lon_str:
                        # 解析度分秒格式，如 "27 deg 25' 0.53\" S"
                        def parse_dms(dms_str):
                            import re
                            match = re.search(r'(\d+)\s*deg\s*(\d+)\'\s*([\d.]+)"?', str(dms_str))
                            if match:
                                d, m, s = float(match.group(1)), float(match.group(2)), float(match.group(3))
                                return d + m/60 + s/3600
                            # 尝试直接作为数字解析
                            try:
                                return float(dms_str)
                            except:
                                return None
                        
                        lat = parse_dms(lat_str)
                        lon = parse_dms(lon_str)
                        
                        if lat is not None and lon is not None:
                            # 处理南纬 (S 或 South)
                            if lat_ref and lat_ref.upper().startswith('S'):
                                lat = -lat
                            # 处理西经 (W 或 West)
                            if lon_ref and lon_ref.upper().startswith('W'):
                                lon = -lon
                            print(_t("logs.gps_extracted", lat=f"{lat:.6f}", lon=f"{lon:.6f}"))
                            return lat, lon, f"GPS: {lat:.6f}, {lon:.6f}"
    except Exception as e:
        print(_t("logs.gps_failed", e=e))
    
    # 回退到 PIL（仅支持 JPEG 等常规格式）
    try:
        image = Image.open(image_path)
        exif_data = image._getexif()

        if not exif_data:
            return None, None, "无EXIF数据"

        gps_info = {}
        for tag, value in exif_data.items():
            decoded_tag = TAGS.get(tag, tag)
            if decoded_tag == "GPSInfo":
                for gps_tag in value:
                    gps_decoded = GPSTAGS.get(gps_tag, gps_tag)
                    gps_info[gps_decoded] = value[gps_tag]
                break

        if not gps_info:
            return None, None, "无GPS数据"

        def convert_to_degrees(coord, ref):
            d, m, s = coord
            decimal = d + (m / 60.0) + (s / 3600.0)
            if ref in ['S', 'W']:
                decimal = -decimal
            return decimal

        lat = None
        lon = None

        if 'GPSLatitude' in gps_info and 'GPSLatitudeRef' in gps_info:
            lat = convert_to_degrees(gps_info['GPSLatitude'], gps_info['GPSLatitudeRef'])

        if 'GPSLongitude' in gps_info and 'GPSLongitudeRef' in gps_info:
            lon = convert_to_degrees(gps_info['GPSLongitude'], gps_info['GPSLongitudeRef'])

        if lat is not None and lon is not None:
            return lat, lon, f"GPS: {lat:.6f}, {lon:.6f}"

        return None, None, "GPS坐标不完整"

    except Exception as e:
        return None, None, f"GPS解析失败: {e}"


# ==================== 图像预处理 ====================

def smart_resize(image: Image.Image, target_size: int = 224) -> Image.Image:
    """智能图像尺寸调整"""
    width, height = image.size
    max_dim = max(width, height)

    if max_dim < 1000:
        return image.resize((target_size, target_size), Image.LANCZOS)

    resized = image.resize((256, 256), Image.LANCZOS)
    left = (256 - target_size) // 2
    top = (256 - target_size) // 2
    return resized.crop((left, top, left + target_size, top + target_size))


def apply_enhancement(image: Image.Image, method: str = "unsharp_mask") -> Image.Image:
    """应用图像增强"""
    if method == "unsharp_mask":
        return image.filter(ImageFilter.UnsharpMask())
    elif method == "edge_enhance_more":
        return image.filter(ImageFilter.EDGE_ENHANCE_MORE)
    elif method == "contrast_edge":
        enhanced = ImageEnhance.Brightness(image).enhance(1.2)
        enhanced = ImageEnhance.Contrast(enhanced).enhance(1.3)
        return enhanced.filter(ImageFilter.EDGE_ENHANCE)
    elif method == "desaturate":
        return ImageEnhance.Color(image).enhance(0.5)
    return image


# ==================== OSEA 预处理 ====================

# CenterCrop 预处理: Resize(256) + CenterCrop(224) + ImageNet Normalize
# 用于原始大图（未经 YOLO 裁剪）
OSEA_TRANSFORM = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

# 直接缩放预处理: Resize(224, 224) with Lanczos + ImageNet Normalize
# 用于 YOLO 裁剪后的正方形图片（已经过 Letterboxing 处理）
# 使用 Lanczos 插值保证高质量缩放
OSEA_TRANSFORM_DIRECT = transforms.Compose([
    transforms.Resize((224, 224), interpolation=transforms.InterpolationMode.LANCZOS),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


# ==================== 核心识别函数 ====================

def predict_bird(
    image: Image.Image,
    top_k: int = 5,
    species_class_ids: Optional[Set[int]] = None,
    is_yolo_cropped: bool = False,
    name_format: str = None
) -> List[Dict]:
    """
    识别鸟类（OSEA ResNet34）

    Args:
        image: PIL Image 对象
        top_k: 返回前 K 个结果
        species_class_ids: 区域物种的 class_id 集合（用于过滤）
        is_yolo_cropped: 图片是否经过 YOLO 裁剪（用于选择预处理方式）

    Returns:
        识别结果列表 [{cn_name, en_name, confidence, ebird_code, ...}, ...]
    """
    model = get_classifier()
    db_manager = get_database_manager()

    # 根据是否经过 YOLO 裁剪选择预处理方式
    # - YOLO 裁剪后: 直接 Resize(224,224)，避免 CenterCrop 丢失特征
    # - 原始大图: Resize(256) + CenterCrop(224)，鸟在中心时效果更好
    if image.mode != 'RGB':
        image = image.convert('RGB')
    transform = OSEA_TRANSFORM_DIRECT if is_yolo_cropped else OSEA_TRANSFORM
    input_tensor = transform(image).unsqueeze(0).to(CLASSIFIER_DEVICE)

    # 推理
    with torch.no_grad():
        output = model(input_tensor)[0]

    # 截取有效类别数（模型输出可能多于实际物种数）
    num_classes = min(10964, output.shape[0])
    output = output[:num_classes]

    # Softmax（温度=0.9 更平滑：降低过高置信度，避免 99%+ 输出）
    TEMPERATURE = 0.9
    best_probs = torch.nn.functional.softmax(output / TEMPERATURE, dim=0)

    # 获取 top-k 结果
    k = min(100 if species_class_ids else top_k, len(best_probs))
    top_probs, top_indices = torch.topk(best_probs, k)

    results = []
    for i in range(len(top_indices)):
        class_id = top_indices[i].item()
        confidence = top_probs[i].item() * 100
        # 置信度阈值：使用区域过滤时降低阈值以保留更多候选
        min_confidence = 0.3 if species_class_ids else 1.0
        if confidence < min_confidence:
            continue

        cn_name = None
        en_name = None
        scientific_name = None
        ebird_code = None
        description = None

        # 优先从数据库获取
        if db_manager:
            info = db_manager.get_bird_by_class_id(class_id)
            if info:
                cn_name = info.get('chinese_simplified')
                en_name = info.get('english_name')
                scientific_name = info.get('scientific_name')
                ebird_code = info.get('ebird_code')
                description = info.get('short_description_zh')

        if not cn_name:
            cn_name = f"Unknown (ID: {class_id})"
            en_name = f"Unknown (ID: {class_id})"

        # AviList name format override
        if name_format and name_format != "default" and db_manager:
            avilist_info = db_manager.get_avilist_names_by_class_id(class_id)
            if avilist_info and avilist_info.get('match_type') != 'no_match':
                if name_format == "scientific":
                    en_name = avilist_info.get('scientific_name_avilist') or scientific_name or en_name
                else:
                    # Map format to column: avilist/clements/birdlife
                    col = f"en_name_{name_format}"
                    alt_name = avilist_info.get(col)
                    # Fallback chain: selected -> avilist -> keep default
                    if alt_name:
                        en_name = alt_name
                    elif name_format != "avilist" and avilist_info.get('en_name_avilist'):
                        en_name = avilist_info['en_name_avilist']

        # Avonet 地理过滤
        region_match = False
        if species_class_ids:
            if class_id in species_class_ids:
                region_match = True
            else:
                continue  # 不在区域物种列表中，跳过

        results.append({
            'class_id': class_id,
            'cn_name': cn_name,
            'en_name': en_name,
            'scientific_name': scientific_name,
            'confidence': confidence,
            'ebird_code': ebird_code,
            'region_match': region_match,
            'description': description or ''
        })

        if len(results) >= top_k:
            break

    return results


def identify_bird(
    image_path: str,
    use_yolo: bool = True,
    use_gps: bool = True,
    use_ebird: bool = True,
    country_code: str = None,
    region_code: str = None,
    top_k: int = 5,
    name_format: str = None
) -> Dict:
    """
    端到端鸟类识别

    Args:
        image_path: 图像路径
        use_yolo: 是否使用 YOLO 裁剪
        use_gps: 是否使用 GPS 自动检测区域
        use_ebird: 是否启用 eBird 区域过滤
        country_code: 手动指定国家代码（如 "AU"）
        region_code: 手动指定区域代码（如 "AU-SA"）
        top_k: 返回前 K 个结果

    Returns:
        识别结果字典
    """
    result = {
        'success': False,
        'image_path': image_path,
        'results': [],
        'yolo_info': None,
        'gps_info': None,
        'ebird_info': None,
        'error': None
    }

    try:
        # 加载图像
        image = load_image(image_path)

        # YOLO 裁剪
        is_yolo_cropped = False
        print(f"[YOLO] use_yolo={use_yolo}, YOLO_AVAILABLE={YOLO_AVAILABLE}")
        if use_yolo and YOLO_AVAILABLE:
            width, height = image.size
            print(f"[YOLO] image size: {width}x{height}")
            if max(width, height) > 640:
                detector = get_yolo_detector()
                print(f"[YOLO] detector={detector is not None}")
                if detector:
                    cropped, info = detector.detect_and_crop_bird(image)
                    print(f"[YOLO] detect result: cropped={cropped is not None}, info={info}")
                    if cropped:
                        image = cropped
                        result['yolo_info'] = info
                        result['cropped_image'] = cropped  # square-cropped PIL Image
                        is_yolo_cropped = True
                        print(f"[YOLO] ✅ Bird region cropped")
                    else:
                        print(f"[YOLO] ⚠️ No bird detected")
                        # strict mode: no bird found, short-circuit
                        result['success'] = True
                        result['results'] = []
                        result['yolo_info'] = {'bird_count': 0}
                        return result
            else:
                print(f"[YOLO] Image too small, skipping crop")
        else:
            print(f"[YOLO] YOLO not enabled or unavailable")

        # Avonet 地理过滤
        species_class_ids = None
        lat = lon = None      # GPS 坐标（供后续回退使用）
        species_filter = None  # 物种过滤器（供后续回退使用）

        if use_ebird:  # 参数名保持兼容，实际使用 Avonet
            try:
                species_filter = get_species_filter()
                if not species_filter:
                    print(_t("logs.avonet_unavailable"))
                else:
                    # 优先使用 GPS 坐标
                    if use_gps:
                        lat, lon, gps_msg = extract_gps_from_exif(image_path)
                        if lat and lon:
                            result['gps_info'] = {
                                'latitude': lat,
                                'longitude': lon,
                                'info': gps_msg
                            }
                            species_class_ids = species_filter.get_species_by_gps(lat, lon)
                            if species_class_ids:
                                print(f"[Avonet] GPS ({lat:.2f}, {lon:.2f}): {len(species_class_ids)} species")

                    # 回退到区域代码（优先 eBird 离线物种列表，其次 Avonet 边界）
                    if species_class_ids is None and (region_code or country_code):
                        effective_region = region_code or country_code
                        # 优先使用 eBird 离线物种 JSON（精确到州/省）
                        try:
                            ebird_ids, actual_region = species_filter.get_species_by_region_ebird(effective_region)
                            if ebird_ids:
                                species_class_ids = ebird_ids
                                print(f"[eBird] Region {effective_region}: {len(species_class_ids)} species (offline JSON)")
                        except Exception as _e:
                            print(f"[eBird] State filter failed: {_e}")
                        # 如果 eBird 数据不可用，回退到 Avonet 边界框查询
                        if not species_class_ids:
                            species_class_ids = species_filter.get_species_by_region(effective_region)
                            if species_class_ids:
                                print(f"[Avonet] Region {effective_region}: {len(species_class_ids)} species (bounds)")

                    # 记录过滤信息
                    if species_class_ids:
                        result['ebird_info'] = {  # 保持键名兼容
                            'enabled': True,
                            'species_count': len(species_class_ids),
                            'data_source': 'avonet.db (offline)',
                            'region_code': region_code or country_code if not result.get('gps_info') else None
                        }

            except Exception as e:
                print(f"[Avonet] Filter init failed: {e}")

        # 执行识别
        results = predict_bird(
            image,
            top_k=top_k,
            species_class_ids=species_class_ids,
            is_yolo_cropped=is_yolo_cropped,
            name_format=name_format
        )

        # GPS 过滤无匹配时，先尝试 eBird 国家级回退，再全局
        if not results and species_class_ids:
            print(f"[Avonet] ⚠️ No match after GPS filter ({len(species_class_ids)} species), trying eBird country fallback")

            # 第一步：eBird 国家级回退
            country_cls_ids = None
            country_cc = None
            if lat is not None and lon is not None and species_filter is not None:
                try:
                    country_cls_ids, country_cc = species_filter.get_species_by_country_ebird(lat, lon)
                except Exception as _e:
                    print(f"[eBird] Country fallback failed: {_e}")

            if country_cls_ids:
                print(f"[eBird] Trying country fallback: {country_cc} ({len(country_cls_ids)} species)")
                results = predict_bird(
                    image,
                    top_k=top_k,
                    species_class_ids=country_cls_ids,
                    is_yolo_cropped=is_yolo_cropped,
                    name_format=name_format
                )
                if results:
                    if not result.get('ebird_info'):
                        result['ebird_info'] = {}
                    result['ebird_info']['country_fallback'] = True
                    result['ebird_info']['country_code'] = country_cc

            # 第二步：仍无结果 → 全局模式
            if not results:
                print(f"[Avonet] ⚠️ Country fallback still no match, switching to global mode")
                results = predict_bird(
                    image,
                    top_k=top_k,
                    species_class_ids=None,
                    is_yolo_cropped=is_yolo_cropped,
                    name_format=name_format
                )
                if results and result.get('ebird_info'):
                    result['ebird_info']['gps_fallback'] = True

        result['success'] = True
        result['results'] = results

    except Exception as e:
        result['error'] = str(e)

    return result


# ==================== 便捷函数 ====================

def quick_identify(image_path: str, top_k: int = 3) -> List[Dict]:
    """
    快速识别（简化接口）

    Returns:
        识别结果列表
    """
    result = identify_bird(image_path, top_k=top_k)
    return result.get('results', [])


# ==================== 测试 ====================

if __name__ == "__main__":
    print("BirdIdentifier 模块测试")
    print(f"YOLO 可用: {YOLO_AVAILABLE}")
    print(f"RAW 支持: {RAW_SUPPORT}")
    print(f"模型路径: {MODEL_PATH}")
    print(f"数据库路径: {DATABASE_PATH}")
