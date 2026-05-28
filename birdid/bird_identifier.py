#!/usr/bin/env python3
"""
鸟类识别核心模块。
Core bird-identification module.

从 SuperBirdID 移植，负责鸟类检测、分类与离线资源路径兼容。
Ported from SuperBirdID and responsible for bird detection, classification,
and compatibility with offline resource paths.
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
from typing import Any, Optional, List, Dict, Tuple, Set, cast
from tools.i18n import t as _t
from config import (
    get_best_device,
    get_lazy_registry,
    get_app_config_dir,
    get_install_scoped_resource_path,
    get_packaged_model_relative_path,
    get_runtime_meipass,
)

CLASSIFIER_DEVICE = torch.device(str(get_best_device()))

RESAMPLING_LANCZOS = Image.Resampling.LANCZOS

try:
    import rawpy
    import imageio

    RAW_SUPPORT = True
except ImportError:
    rawpy = cast(Any, None)
    imageio = cast(Any, None)
    RAW_SUPPORT = False

try:
    from ultralytics import YOLO

    YOLO_AVAILABLE = True
except ImportError:
    YOLO = cast(Any, None)
    YOLO_AVAILABLE = False

BIRDID_DIR = os.path.dirname(os.path.abspath(__file__))


# 项目根目录（code_updates overlay 场景下 __file__ 指向 code_updates/birdid/，需通过 sys.path 找真实根）
# Project root lookup: in code_updates overlay scenarios __file__ points to
# code_updates/birdid/, so fall back to sys.path entries to locate the real root.
def _find_project_root() -> str:
    candidate = os.path.dirname(BIRDID_DIR)
    if os.path.exists(os.path.join(candidate, "models", "model20240824.pth")):
        return candidate
    for p in sys.path:
        if (
            p
            and os.path.isdir(p)
            and os.path.exists(os.path.join(p, "models", "model20240824.pth"))
        ):
            return p
    return candidate


def _find_birdid_dir() -> str:
    if os.path.exists(os.path.join(BIRDID_DIR, "data", "bird_reference.sqlite")):
        return BIRDID_DIR
    for p in sys.path:
        if p and os.path.isdir(p):
            candidate = os.path.join(p, "birdid")
            if os.path.exists(os.path.join(candidate, "data", "bird_reference.sqlite")):
                return candidate
    return BIRDID_DIR


PROJECT_ROOT = _find_project_root()
BIRDID_DIR = _find_birdid_dir()


def get_birdid_path(relative_path: str) -> str:
    """
    返回 `birdid/` 目录下的资源路径。
    Return a resource path under the `birdid/` directory.

    Windows Lite 构建需要从安装目录 `_internal` 读取资源，其余冻结环境仍跟随
    PyInstaller bundle 目录；源码环境则回退到仓库内的 `birdid/` 目录。
    Windows Lite builds read from the install-scoped `_internal` tree, other
    frozen builds still follow the PyInstaller bundle, and source runs fall back
    to the repository `birdid/` directory.
    """
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        return str(
            get_install_scoped_resource_path(os.path.join("birdid", relative_path))
        )
    if getattr(sys, "frozen", False):
        meipass = get_runtime_meipass()
        if meipass is not None:
            return os.path.join(meipass, "birdid", relative_path)
    return os.path.join(BIRDID_DIR, relative_path)


def get_project_path(relative_path: str) -> str:
    """
    返回项目级资源路径。
    Return a project-level resource path.

    这里统一兼容 Windows Lite 安装目录、普通 PyInstaller bundle 与源码目录，
    避免各调用方再自行拼接 `_MEIPASS` 路径。
    This helper centralizes path selection for Windows Lite installs, regular
    PyInstaller bundles, and source checkouts so callers do not rebuild
    `_MEIPASS`-based paths themselves.
    """
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        packaged_relative_path = None
        if relative_path.startswith("models/"):
            packaged_relative_path = get_packaged_model_relative_path(relative_path)
        return str(
            get_install_scoped_resource_path(
                relative_path, packaged_relative_path=packaged_relative_path
            )
        )
    if getattr(sys, "frozen", False):
        meipass = get_runtime_meipass()
        if meipass is not None:
            return os.path.join(meipass, relative_path)
    return os.path.join(PROJECT_ROOT, relative_path)


def get_user_data_dir() -> str:
    user_data_dir = str(get_app_config_dir())
    os.makedirs(user_data_dir, exist_ok=True)
    return user_data_dir


MODEL_PATH = get_project_path("models/model20240824.pth")
MODEL_PATH_LEGACY = get_birdid_path("models/birdid2024.pt")
MODEL_PATH_ENC = get_birdid_path("models/birdid2024.pt.enc")
OSEA_NUM_CLASSES = 11000
DATABASE_PATH = get_birdid_path("data/bird_reference.sqlite")
YOLO_MODEL_PATH = get_project_path("models/yolo11l-seg.pt")


def decrypt_model(encrypted_path: str, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    with open(encrypted_path, "rb") as f:
        encrypted_data = f.read()

    salt = encrypted_data[:16]
    iv = encrypted_data[16:32]
    ciphertext = encrypted_data[32:]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend(),
    )
    key = kdf.derive(password.encode())

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    plaintext_padded = decryptor.update(ciphertext) + decryptor.finalize()

    padding_length = plaintext_padded[-1]
    return plaintext_padded[:-padding_length]


def _load_torchscript_from_bytes(model_data: bytes):
    buffer = io.BytesIO(model_data)
    return torch.jit.load(buffer, map_location="cpu")


def get_classifier():
    registry = get_lazy_registry()

    def _factory():
        import torchvision.models as models

        if os.path.exists(MODEL_PATH):
            model = models.resnet34(num_classes=OSEA_NUM_CLASSES)
            state_dict = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
            model.load_state_dict(state_dict)
            model = model.to(device=CLASSIFIER_DEVICE)
            model.eval()
            return model

        SECRET_PASSWORD = "SuperBirdID_2024_AI_Model_Encryption_Key_v1"
        if os.path.exists(MODEL_PATH_ENC):
            model_data = decrypt_model(MODEL_PATH_ENC, SECRET_PASSWORD)
            model = _load_torchscript_from_bytes(model_data)
        elif os.path.exists(MODEL_PATH_LEGACY):
            try:
                model = torch.jit.load(MODEL_PATH_LEGACY, map_location="cpu")
            except RuntimeError as e:
                if "open file failed" not in str(e) or "fopen" not in str(e):
                    raise
                with open(MODEL_PATH_LEGACY, "rb") as f:
                    model_data = f.read()
                model = _load_torchscript_from_bytes(model_data)
        else:
            raise RuntimeError(f"未找到分类模型: {MODEL_PATH} 或 {MODEL_PATH_LEGACY}")

        model = model.to(CLASSIFIER_DEVICE)
        model.eval()  # noqa: model.eval() is a PyTorch API call, not Python eval()
        print(_t("logs.birdid_fallback_model"))
        return model

    return registry.get_or_create("birdid.classifier", _factory)


def get_bird_model():
    return get_classifier()


def get_database_manager():
    registry = get_lazy_registry()

    def _factory():
        try:
            from birdid.bird_database_manager import BirdDatabaseManager

            if os.path.exists(DATABASE_PATH):
                return BirdDatabaseManager(DATABASE_PATH)
        except Exception as e:
            pass
        return False

    result = registry.get_or_create("birdid.database_manager", _factory)
    return result if result is not False else None


def get_yolo_detector():
    if not YOLO_AVAILABLE:
        return None
    registry = get_lazy_registry()
    return registry.get_or_create(
        "birdid.yolo_detector",
        lambda: (
            YOLOBirdDetector(YOLO_MODEL_PATH)
            if os.path.exists(YOLO_MODEL_PATH)
            else None
        ),
    )


def get_species_filter():
    registry = get_lazy_registry()

    def _factory():
        try:
            from birdid.avonet_filter import AvonetFilter

            filt = AvonetFilter()
            if filt.is_available():
                return filt
        except Exception as e:
            pass
        return None

    return registry.get_or_create("birdid.avonet_filter", _factory)


class YOLOBirdDetector:
    def __init__(self, model_path: Optional[str] = None):
        if not YOLO_AVAILABLE:
            self.model = None
            return

        if model_path is None:
            model_path = YOLO_MODEL_PATH

        model_path = os.path.abspath(model_path)
        if not os.path.exists(model_path):
            self.model = None
            return

        try:
            self.model = YOLO(model_path)
        except Exception as e:
            self.model = None

    def detect_and_crop_bird(
        self,
        image_input,
        confidence_threshold: float = 0.25,
        padding_ratio: float = 0.15,
        fill_color: Tuple[int, int, int] = (0, 0, 0),
    ) -> Tuple[Optional[Image.Image], str]:
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

                        if class_id == 14:
                            detections.append(
                                {
                                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                                    "confidence": float(confidence),
                                }
                            )

            if not detections:
                return None, "未检测到鸟类"

            best = max(detections, key=lambda x: x["confidence"])
            img_width, img_height = image.size

            x1, y1, x2, y2 = best["bbox"]
            bbox_width = x2 - x1
            bbox_height = y2 - y1

            max_side = max(bbox_width, bbox_height)
            target_side = int(max_side * (1 + padding_ratio))

            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            half = target_side // 2

            sq_x1 = cx - half
            sq_y1 = cy - half
            sq_x2 = cx + half
            sq_y2 = cy + half

            crop_x1 = max(0, sq_x1)
            crop_y1 = max(0, sq_y1)
            crop_x2 = min(img_width, sq_x2)
            crop_y2 = min(img_height, sq_y2)

            cropped = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            crop_w, crop_h = cropped.size

            if crop_w != crop_h:
                sq_size = max(crop_w, crop_h)
                square = Image.new("RGB", (sq_size, sq_size), fill_color)
                paste_x = (sq_size - crop_w) // 2
                paste_y = (sq_size - crop_h) // 2
                square.paste(cropped, (paste_x, paste_y))
                cropped = square

            info = f"conf={best['confidence']:.3f}, size={cropped.size}"

            return cropped, info

        except Exception as e:
            return None, f"检测失败: {e}"


def load_image(image_path: str) -> Image.Image:
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"文件不存在: {image_path}")

    ext = os.path.splitext(image_path)[1].lower()

    raw_extensions = [
        ".cr2",
        ".cr3",
        ".nef",
        ".nrw",
        ".arw",
        ".srf",
        ".dng",
        ".raf",
        ".orf",
        ".rw2",
        ".pef",
        ".srw",
        ".raw",
        ".rwl",
        ".3fr",
        ".fff",
        ".erf",
        ".mef",
        ".mos",
        ".mrw",
        ".x3f",
        ".hif",
        ".heif",
        ".heic",
    ]

    heif_extensions = {".hif", ".heif", ".heic"}

    if ext in raw_extensions:
        if ext in heif_extensions:
            return _load_heif(image_path)
        if RAW_SUPPORT:
            thumb_format_enum = getattr(rawpy, "ThumbFormat", None)
            jpeg_thumb_format = getattr(thumb_format_enum, "JPEG", None)
            bitmap_thumb_format = getattr(thumb_format_enum, "BITMAP", None)
            rawpy_internal = getattr(rawpy, "_rawpy", None)
            unsupported_error = getattr(
                rawpy_internal, "LibRawFileUnsupportedError", None
            )
            try:
                with rawpy.imread(image_path) as raw:
                    try:
                        thumb = raw.extract_thumb()
                        if thumb.format == jpeg_thumb_format:
                            from io import BytesIO

                            img = Image.open(BytesIO(thumb.data)).convert("RGB")
                            return img
                        elif thumb.format == bitmap_thumb_format:
                            img = Image.fromarray(thumb.data).convert("RGB")
                            return img
                    except Exception as e:
                        pass

                    rgb = raw.postprocess(
                        use_camera_wb=True,
                        output_bps=8,
                        no_auto_bright=False,
                        auto_bright_thr=0.01,
                        half_size=True,
                    )
                    img = Image.fromarray(rgb)
                    return img
            except Exception as e:
                if unsupported_error is not None and isinstance(e, unsupported_error):
                    return _load_raw_via_exiftool(image_path)
                raise Exception(f"RAW处理失败: {e}")
        else:
            raise ImportError("需要安装 rawpy 来处理 RAW 格式")
    else:
        return Image.open(image_path).convert("RGB")


def _load_raw_via_exiftool(image_path: str) -> Image.Image:
    """
    使用 ExifTool 从 RAW 文件提取可解码预览图。
    Extract a decodable preview image from a RAW file via ExifTool.
    """
    import subprocess
    from io import BytesIO

    possible_paths = []
    if getattr(sys, "frozen", False):
        meipass = get_runtime_meipass()
        if meipass is not None:
            possible_paths.append(os.path.join(meipass, "exiftools_mac", "exiftool"))
    possible_paths += [
        os.path.join(PROJECT_ROOT, "exiftools_mac", "exiftool"),
        "/opt/homebrew/bin/exiftool",
        "/usr/local/bin/exiftool",
        "exiftool",
    ]
    exiftool = next((p for p in possible_paths if os.path.isfile(p)), "exiftool")

    for tag in ["-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"]:
        try:
            result = subprocess.run(
                [exiftool, "-b", tag, image_path], capture_output=True, timeout=15
            )
            if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
                img = Image.open(BytesIO(result.stdout)).convert("RGB")
                return img
        except Exception as e:
            continue

    raise Exception(
        f"\u6682\u4e0d\u652f\u6301\u6b64 RAW \u683c\u5f0f\uff08{os.path.basename(image_path)}\uff09\u3002"
        "Sony A7M5 \u7b49\u76f8\u673a\u7684 NeXt/Compressed RAW 2 \u683c\u5f0f\u76ee\u524d\u7b2c\u4e09\u65b9\u5e93\u5c1a\u672a\u5b8c\u6574\u652f\u6301\uff0c"
        "\u5c06\u0627\u5728\u540e\u7eed\u7248\u672c\u4e2d\u4fee\u590d\u3002\u5efa\u8bae\u4e34\u65f6\u4f7f\u7528\u65e0\u538b\u7f29 RAW \u6216 JPEG \u683c\u5f0f\u62cd\u6444\u3002"
    )


def _load_heif(image_path: str) -> Image.Image:
    try:
        import pillow_heif

        heif_file = pillow_heif.read_heif(image_path)
        if heif_file.data is None:
            raise ValueError("HEIF 解码结果缺少像素数据")
        img = Image.frombytes(
            heif_file.mode,
            heif_file.size,
            heif_file.data,
            "raw",
        ).convert("RGB")
        return img
    except ImportError:
        raise Exception(
            "请安装 pillow-heif 来支持 HIF/HEIC 格式： pip install pillow-heif"
        )
    except Exception as e:
        raise Exception(f"HEIF 解码失败 ({os.path.basename(image_path)}): {e}")


def extract_gps_from_exif(
    image_path: str,
) -> Tuple[Optional[float], Optional[float], str]:
    import subprocess
    import json as json_module

    try:
        exiftool_paths = [
            "/usr/local/bin/exiftool",
            "/opt/homebrew/bin/exiftool",
            "exiftool",
        ]

        exiftool_path = None
        for path in exiftool_paths:
            try:
                result = subprocess.run(
                    [path, "-ver"], capture_output=True, text=False, timeout=5
                )
                if result.returncode == 0:
                    stdout_bytes = result.stdout
                    decoded_output = None
                    for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
                        try:
                            decoded_output = stdout_bytes.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue

                    if decoded_output is None:
                        decoded_output = stdout_bytes.decode("latin-1")

                    if decoded_output.strip():
                        exiftool_path = path
                        break
            except:
                continue

        if exiftool_path:
            result = subprocess.run(
                [
                    exiftool_path,
                    "-j",
                    "-GPSLatitude",
                    "-GPSLongitude",
                    "-GPSLatitudeRef",
                    "-GPSLongitudeRef",
                    image_path,
                ],
                capture_output=True,
                text=False,
                timeout=10,
            )

            if result.returncode == 0 and result.stdout:
                stdout_bytes = result.stdout
                decoded_output = None
                for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
                    try:
                        decoded_output = stdout_bytes.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue

                if decoded_output is None:
                    decoded_output = stdout_bytes.decode("latin-1")

                data = json_module.loads(decoded_output)
                if data and len(data) > 0:
                    gps_data = data[0]

                    lat_str = gps_data.get("GPSLatitude", "")
                    lon_str = gps_data.get("GPSLongitude", "")
                    lat_ref = gps_data.get("GPSLatitudeRef", "N")
                    lon_ref = gps_data.get("GPSLongitudeRef", "E")

                    if lat_str and lon_str:

                        def parse_dms(dms_str):
                            import re

                            match = re.search(
                                r'(\d+)\s*deg\s*(\d+)\'\s*([\d.]+)"?', str(dms_str)
                            )
                            if match:
                                d, m, s = (
                                    float(match.group(1)),
                                    float(match.group(2)),
                                    float(match.group(3)),
                                )
                                return d + m / 60 + s / 3600
                            try:
                                return float(dms_str)
                            except:
                                return None

                        lat = parse_dms(lat_str)
                        lon = parse_dms(lon_str)

                        if lat is not None and lon is not None:
                            if lat_ref and lat_ref.upper().startswith("S"):
                                lat = -lat
                            if lon_ref and lon_ref.upper().startswith("W"):
                                lon = -lon
                            return lat, lon, f"GPS: {lat:.6f}, {lon:.6f}"
    except Exception as e:
        pass

    try:
        image = Image.open(image_path)
        exif_data = image.getexif()

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
            if ref in ["S", "W"]:
                decimal = -decimal
            return decimal

        lat = None
        lon = None

        if "GPSLatitude" in gps_info and "GPSLatitudeRef" in gps_info:
            lat = convert_to_degrees(
                gps_info["GPSLatitude"], gps_info["GPSLatitudeRef"]
            )

        if "GPSLongitude" in gps_info and "GPSLongitudeRef" in gps_info:
            lon = convert_to_degrees(
                gps_info["GPSLongitude"], gps_info["GPSLongitudeRef"]
            )

        if lat is not None and lon is not None:
            return lat, lon, f"GPS: {lat:.6f}, {lon:.6f}"

        return None, None, "GPS坐标不完整"

    except Exception as e:
        return None, None, f"GPS解析失败: {e}"


def smart_resize(image: Image.Image, target_size: int = 224) -> Image.Image:
    width, height = image.size
    max_dim = max(width, height)

    if max_dim < 1000:
        return image.resize((target_size, target_size), RESAMPLING_LANCZOS)

    resized = image.resize((256, 256), RESAMPLING_LANCZOS)
    left = (256 - target_size) // 2
    top = (256 - target_size) // 2
    return resized.crop((left, top, left + target_size, top + target_size))


def apply_enhancement(image: Image.Image, method: str = "unsharp_mask") -> Image.Image:
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


OSEA_TRANSFORM = transforms.Compose(
    [
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
)

OSEA_TRANSFORM_DIRECT = transforms.Compose(
    [
        transforms.Resize(
            (224, 224), interpolation=transforms.InterpolationMode.LANCZOS
        ),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
)


def predict_bird(
    image: Image.Image,
    top_k: int = 5,
    species_class_ids: Optional[Set[int]] = None,
    is_yolo_cropped: bool = False,
    name_format: Optional[str] = None,
) -> List[Dict]:
    model = get_classifier()
    db_manager = get_database_manager()

    if image.mode != "RGB":
        image = image.convert("RGB")
    transform = OSEA_TRANSFORM_DIRECT if is_yolo_cropped else OSEA_TRANSFORM
    transformed_tensor = cast(torch.Tensor, transform(image))
    input_tensor = transformed_tensor.unsqueeze(0).to(CLASSIFIER_DEVICE)

    with torch.no_grad():
        output = model(input_tensor)[0]

    num_classes = min(10964, output.shape[0])
    output = output[:num_classes]

    TEMPERATURE = 0.9
    best_probs = torch.nn.functional.softmax(output / TEMPERATURE, dim=0)

    k = min(100 if species_class_ids else top_k, len(best_probs))
    top_probs, top_indices = torch.topk(best_probs, k)

    results = []
    for i in range(len(top_indices)):
        class_id = top_indices[i].item()
        confidence = top_probs[i].item() * 100
        min_confidence = 0.3 if species_class_ids else 1.0
        if confidence < min_confidence:
            continue

        cn_name = None
        en_name = None
        scientific_name = None
        ebird_code = None
        description = None

        if db_manager:
            info = db_manager.get_bird_by_class_id(class_id)
            if info:
                cn_name = info.get("chinese_simplified")
                en_name = info.get("english_name")
                scientific_name = info.get("scientific_name")
                ebird_code = info.get("ebird_code")
                description = info.get("short_description_zh")

        if not cn_name:
            cn_name = f"Unknown (ID: {class_id})"
            en_name = f"Unknown (ID: {class_id})"

        if name_format and name_format != "default" and db_manager:
            avilist_info = db_manager.get_avilist_names_by_class_id(class_id)
            if avilist_info and avilist_info.get("match_type") != "no_match":
                if name_format == "scientific":
                    en_name = (
                        avilist_info.get("scientific_name_avilist")
                        or scientific_name
                        or en_name
                    )
                else:
                    col = f"en_name_{name_format}"
                    alt_name = avilist_info.get(col)
                    if alt_name:
                        en_name = alt_name
                    elif name_format != "avilist" and avilist_info.get(
                        "en_name_avilist"
                    ):
                        en_name = avilist_info["en_name_avilist"]

        region_match = False
        if species_class_ids:
            if class_id in species_class_ids:
                region_match = True
            else:
                continue

        rarity_index = (
            db_manager.get_rarity_by_class_id(class_id) if db_manager else None
        )
        iucn_category = (
            db_manager.get_iucn_by_class_id(class_id) if db_manager else None
        )

        results.append(
            {
                "class_id": class_id,
                "cn_name": cn_name,
                "en_name": en_name,
                "scientific_name": scientific_name,
                "rarity_index": rarity_index,
                "iucn_category": iucn_category,
                "confidence": confidence,
                "ebird_code": ebird_code,
                "region_match": region_match,
                "description": description or "",
            }
        )

        if len(results) >= top_k:
            break

    return results


def identify_bird(
    image_path: str,
    use_yolo: bool = True,
    use_gps: bool = True,
    use_ebird: bool = True,
    country_code: Optional[str] = None,
    region_code: Optional[str] = None,
    top_k: int = 5,
    name_format: Optional[str] = None,
    preloaded_crop: Optional[Image.Image] = None,
) -> Dict:
    result = {
        "success": False,
        "image_path": image_path,
        "results": [],
        "yolo_info": None,
        "gps_info": None,
        "ebird_info": None,
        "error": None,
    }

    try:
        is_yolo_cropped = False
        if preloaded_crop is not None:
            image = preloaded_crop
            is_yolo_cropped = True
            result["yolo_info"] = {"preloaded": True}
        else:
            image = load_image(image_path)

        if preloaded_crop is None and use_yolo and YOLO_AVAILABLE:
            width, height = image.size
            if max(width, height) > 640:
                detector = get_yolo_detector()
                if detector:
                    cropped, info = detector.detect_and_crop_bird(image)
                    if cropped:
                        image = cropped
                        result["yolo_info"] = info
                        result["cropped_image"] = cropped
                        is_yolo_cropped = True
                    else:
                        result["success"] = True
                        result["results"] = []
                        result["yolo_info"] = {"bird_count": 0}
                        return result

        species_class_ids = None
        lat = lon = None
        species_filter = None

        if use_ebird:
            try:
                species_filter = get_species_filter()
                if species_filter:
                    if use_gps:
                        lat, lon, gps_msg = extract_gps_from_exif(image_path)
                        if lat and lon:
                            result["gps_info"] = {
                                "latitude": lat,
                                "longitude": lon,
                                "info": gps_msg,
                            }
                            species_class_ids = species_filter.get_species_by_gps(
                                lat, lon
                            )

                    if species_class_ids is None and (region_code or country_code):
                        effective_region = region_code or country_code
                        try:
                            ebird_ids, actual_region = (
                                species_filter.get_species_by_region_ebird(
                                    effective_region
                                )
                            )
                            if ebird_ids:
                                species_class_ids = ebird_ids
                        except Exception as _e:
                            pass
                        if not species_class_ids:
                            species_class_ids = species_filter.get_species_by_region(
                                effective_region
                            )

                    if species_class_ids:
                        result["ebird_info"] = {
                            "enabled": True,
                            "species_count": len(species_class_ids),
                            "data_source": "avonet.db (offline)",
                            "region_code": (
                                region_code or country_code
                                if not result.get("gps_info")
                                else None
                            ),
                        }

            except Exception as e:
                pass

        results = predict_bird(
            image,
            top_k=top_k,
            species_class_ids=species_class_ids,
            is_yolo_cropped=is_yolo_cropped,
            name_format=name_format,
        )

        if not results and species_class_ids:
            country_cls_ids = None
            country_cc = None
            if lat is not None and lon is not None and species_filter is not None:
                try:
                    country_cls_ids, country_cc = (
                        species_filter.get_species_by_country_ebird(lat, lon)
                    )
                except Exception as _e:
                    pass

            if country_cls_ids:
                results = predict_bird(
                    image,
                    top_k=top_k,
                    species_class_ids=country_cls_ids,
                    is_yolo_cropped=is_yolo_cropped,
                    name_format=name_format,
                )
                if results:
                    if not result.get("ebird_info"):
                        result["ebird_info"] = {}
                    result["ebird_info"]["country_fallback"] = True
                    result["ebird_info"]["country_code"] = country_cc

            if not results:
                results = predict_bird(
                    image,
                    top_k=top_k,
                    species_class_ids=None,
                    is_yolo_cropped=is_yolo_cropped,
                    name_format=name_format,
                )
                if results and result.get("ebird_info"):
                    result["ebird_info"]["gps_fallback"] = True

        result["success"] = True
        result["results"] = results

    except Exception as e:
        result["error"] = str(e)

    return result


def quick_identify(image_path: str, top_k: int = 3) -> List[Dict]:
    result = identify_bird(image_path, top_k=top_k)
    return result.get("results", [])


if __name__ == "__main__":
    print("BirdIdentifier 模块测试")
    print(f"YOLO 可用: {YOLO_AVAILABLE}")
    print(f"RAW 支持: {RAW_SUPPORT}")
    print(f"模型路径: {MODEL_PATH}")
    print(f"数据库路径: {DATABASE_PATH}")
