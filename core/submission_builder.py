# -*- coding: utf-8 -*-
"""
纠错样本打包器（Submission Builder）。

把一批 (照片, model_class_id, 中文名, 可选溯源) 打成训练工具收件箱格式的 zip：
  images/<stem>.jpg（长边 1280、q85、无 EXIF/GPS） + labels.csv + submission.json

纯逻辑，无 Qt 依赖，可独立单测。

Submission Builder: packages a batch of (photo, model_class_id, chinese name,
optional provenance) into a zip in the training-tool inbox format:
  images/<stem>.jpg (long edge 1280, q85, no EXIF/GPS) + labels.csv + submission.json

Pure logic, no Qt dependency, independently unit-testable.
"""
import csv
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from PIL import Image

from birdid.bird_identifier import load_image

# ── 常量（守门线，集中易改）/ Constants (guardrails, centralized for easy change) ──
LONG_EDGE = 1280          # 导出长边（必须 > 640，训练工具才走 YOLO 裁鸟）/ export long edge (must be > 640 for the training tool to run YOLO crop)
JPEG_QUALITY = 85         # 导出 JPEG 质量 / export JPEG quality
EMAIL_SIZE_WARN_MB = 20   # 预估 zip 超此值提示用户少选/走网盘 / warn user to select fewer photos or use cloud storage if estimated zip exceeds this


@dataclass
class SubmissionItem:
    """
    一张待提交照片。

    属性:
    photo_path (str): 原始照片路径（支持 RAW/HEIF/JPEG，经 load_image 统一解码）
    model_class_id (int): 纠正后的模型类别 ID
    chinese (str): 纠正后的中文名
    wrong_cn (Optional[str]): 原识别错误的中文名（溯源用，可为空）
    birdid_confidence (Optional[float]): 原识别置信度（溯源用，可为空）
    is_the_failed_one (bool): 是否是本次触发提交的那张失败样本

    A single photo pending submission.

    Attributes:
    photo_path (str): source photo path (RAW/HEIF/JPEG all supported via load_image)
    model_class_id (int): corrected model class ID
    chinese (str): corrected Chinese name
    wrong_cn (Optional[str]): the originally mis-identified Chinese name (provenance, may be None)
    birdid_confidence (Optional[float]): the original identification confidence (provenance, may be None)
    is_the_failed_one (bool): whether this is the specific failed sample that triggered the submission
    """
    photo_path: str
    model_class_id: int
    chinese: str
    wrong_cn: Optional[str] = None
    birdid_confidence: Optional[float] = None
    is_the_failed_one: bool = False


@dataclass
class BuildResult:
    """
    打包结果。

    属性:
    zip_path (str): 生成的 zip 文件绝对路径
    count (int): 成功打包的照片数
    skipped (List[Tuple[str, str]]): 打不开/损坏被跳过的 (路径, 原因) 列表
    est_size_mb (float): zip 文件预估大小（MB）

    Build result.

    Attributes:
    zip_path (str): absolute path of the generated zip file
    count (int): number of successfully packaged photos
    skipped (List[Tuple[str, str]]): (path, reason) list of photos skipped due to open/decode failure
    est_size_mb (float): estimated zip file size in MB
    """
    zip_path: str
    count: int
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (path, 原因) / (path, reason)
    est_size_mb: float = 0.0


def _resize_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    """
    等比缩放到长边=long_edge（只缩不放大）。

    参数:
    img (Image.Image): 待缩放的图像
    long_edge (int): 目标长边像素数

    返回:
    Image.Image: 缩放后的图像（若原图长边已 <= long_edge 则原样返回）

    Proportionally resize so the longer edge equals long_edge (never upscales).

    Parameters:
    img (Image.Image): image to resize
    long_edge (int): target long-edge pixel count

    Return:
    Image.Image: resized image (returned unchanged if the original long edge is already <= long_edge)
    """
    w, h = img.size
    m = max(w, h)
    if m <= long_edge:
        return img
    scale = long_edge / float(m)
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def _encode_clean_jpeg(img: Image.Image) -> bytes:
    """
    编码为 JPEG（q85，不写任何 EXIF/GPS）。

    参数:
    img (Image.Image): 待编码的图像

    返回:
    bytes: JPEG 字节流（无 EXIF/GPS 元数据）

    Encode to JPEG (q85, writes no EXIF/GPS at all).

    Parameters:
    img (Image.Image): image to encode

    Return:
    bytes: JPEG byte stream (no EXIF/GPS metadata)
    """
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _unique_stem(base: str, used: set) -> str:
    """
    stem 去重：冲突时加 _2/_3…（导出器按小写 stem 建索引，必须唯一）。

    参数:
    base (str): 原始文件名 stem（不含扩展名）
    used (set): 已使用的 stem 集合（小写），会被就地更新

    返回:
    str: 去重后的唯一 stem

    Deduplicate stems: append _2/_3... on conflict (the exporter indexes by
    lowercase stem, so uniqueness is required).

    Parameters:
    base (str): original filename stem (without extension)
    used (set): set of already-used stems (lowercase), updated in place

    Return:
    str: a unique stem after deduplication
    """
    stem = base
    n = 2
    while stem.lower() in used:
        stem = f"{base}_{n}"
        n += 1
    used.add(stem.lower())
    return stem


def build_submission(
    items: List[SubmissionItem], out_dir: str, app_version: str = "unknown"
) -> BuildResult:
    """
    打包一批纠错样本到 out_dir/SuperPicky纠错_YYYYMMDD.zip。

    逐张 load_image → 缩长边 1280 → 抹 EXIF → JPEG q85 → 写入 images/；
    同时写 labels.csv（filename,model_class_id,chinese，stem 去重）和
    submission.json（含溯源字段）。打不开/损坏的图记入 skipped，不致命。

    参数:
    items (List[SubmissionItem]): 待打包的照片列表
    out_dir (str): 输出目录（zip 文件写在此目录下）
    app_version (str): 应用版本号，写入 submission.json 溯源

    返回:
    BuildResult: 打包结果（zip 路径、成功数、跳过列表、预估大小）

    Package a batch of correction samples into out_dir/SuperPicky纠错_YYYYMMDD.zip.

    For each item: load_image → resize long edge to 1280 → strip EXIF →
    encode JPEG q85 → write into images/; also writes labels.csv
    (filename,model_class_id,chinese, stem-deduplicated) and submission.json
    (with provenance fields). Photos that fail to open/decode are recorded in
    skipped and do not abort the build.

    Parameters:
    items (List[SubmissionItem]): list of photos to package
    out_dir (str): output directory (the zip file is written here)
    app_version (str): app version string, written into submission.json for provenance

    Return:
    BuildResult: build result (zip path, success count, skipped list, estimated size)
    """
    os.makedirs(out_dir, exist_ok=True)
    skipped: List[Tuple[str, str]] = []
    used_stems: set = set()
    label_rows: List[Tuple[str, int, str]] = []   # (stem, mcid, chinese)
    per_photo: List[dict] = []
    jpeg_blobs: List[Tuple[str, bytes]] = []       # (stem, jpeg bytes)

    for it in items:
        try:
            img = load_image(it.photo_path)
            img = _resize_long_edge(img, LONG_EDGE)
            blob = _encode_clean_jpeg(img)
        except Exception as e:  # 损坏/不支持 → 跳过不致命 / corrupt/unsupported -> skip, non-fatal
            skipped.append((it.photo_path, str(e)))
            continue
        base = os.path.splitext(os.path.basename(it.photo_path))[0]
        stem = _unique_stem(base, used_stems)
        jpeg_blobs.append((stem, blob))
        label_rows.append((stem, it.model_class_id, it.chinese))
        per_photo.append({
            "filename": stem,
            "corrected_model_class_id": it.model_class_id,
            "corrected_cn": it.chinese,
            "wrong_cn": it.wrong_cn,
            "birdid_confidence": it.birdid_confidence,
            "is_the_failed_one": it.is_the_failed_one,
        })

    # labels.csv（UTF-8）
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["filename", "model_class_id", "chinese"])
    for stem, mcid, zh in label_rows:
        writer.writerow([stem, mcid, zh])

    meta = {
        "app_version": app_version,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(jpeg_blobs),
        "per_photo": per_photo,
    }

    # zip 目标名（同日冲突加序号）/ target zip name (append sequence number if same-day conflict)
    day = datetime.now().strftime("%Y%m%d")
    zip_path = os.path.join(out_dir, f"SuperPicky纠错_{day}.zip")
    n = 2
    while os.path.exists(zip_path):
        zip_path = os.path.join(out_dir, f"SuperPicky纠错_{day}_{n}.zip")
        n += 1

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for stem, blob in jpeg_blobs:
            z.writestr(f"images/{stem}.jpg", blob)
        z.writestr("labels.csv", csv_buf.getvalue())
        z.writestr("submission.json",
                   json.dumps(meta, ensure_ascii=False, indent=2))

    est_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    return BuildResult(zip_path=zip_path, count=len(jpeg_blobs),
                       skipped=skipped, est_size_mb=est_size_mb)
