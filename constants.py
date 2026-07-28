#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky 常量定义
统一管理全局常量，避免重复定义
"""

# 应用版本号
APP_VERSION = "4.5.0RC10"


# 评分对应的文件夹名称映射（向后兼容，默认中文）
RATING_FOLDER_NAMES = {
    3: "3星_优选",
    2: "2星_良好",
    1: "1星_普通",
    0: "0星_放弃",
    -1: "0星_放弃"  # 无鸟照片也放入0星目录
}

# 英文文件夹名称
RATING_FOLDER_NAMES_EN = {
    3: "3star_excellent",
    2: "2star_good",
    1: "1star_average",
    0: "0star_reject",
    -1: "0star_reject"
}

def get_rating_folder_names():
    """
    获取当前语言的评分文件夹名称映射

    Returns:
        dict: {评分: 文件夹名称}
    """
    try:
        from tools.i18n import get_i18n
        i18n = get_i18n()
        if i18n.current_lang.startswith('en'):
            return RATING_FOLDER_NAMES_EN.copy()
    except Exception:
        pass
    return RATING_FOLDER_NAMES.copy()

def get_rating_folder_name(rating: int) -> str:
    """
    获取指定评分的文件夹名称（根据当前语言）

    Args:
        rating: 评分 (-1 to 3)

    Returns:
        str: 文件夹名称
    """
    folders = get_rating_folder_names()
    return folders.get(rating, folders.get(0, "0star_reject"))

# 支持的 RAW 文件扩展名（小写）
RAW_EXTENSIONS = ['.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.pef', '.dng', '.3fr', '.iiq']

# 元数据强制写 XMP 侧车（不重写 RAW 本体）的专有 RAW 格式。
# DNG 除外：Lightroom/C1 惯例是 DNG 读写内嵌 XMP、不认侧车；专有 RAW 则
# 侧车优先。实测嵌入式重写 RAW 本体 ~190ms/张 vs 侧车 ~10ms/张（A/B 快 33%），
# 且不动本体更安全（曾有 ExFAT overwrite_original 丢原图事故）。
# Proprietary RAW formats whose metadata is force-written to XMP sidecars
# (never rewriting the RAW body). DNG is excluded: LR/C1 convention reads
# embedded XMP in DNG and ignores sidecars, while proprietary RAW prefers
# sidecars. Measured: embedded body rewrite ~190ms/photo vs sidecar ~10ms
# (33% faster end-to-end), and leaving the body untouched is safer.
SIDECAR_RAW_EXTENSIONS = [ext for ext in RAW_EXTENSIONS if ext != '.dng']

# 支持的 HEIF 文件扩展名（小写）- Sony HIF / Apple HEIC 等
HEIF_EXTENSIONS = ['.hif', '.heif', '.heic']

# 支持的 JPG 文件扩展名（小写）
JPG_EXTENSIONS = ['.jpg', '.jpeg']

# 所有支持的图片扩展名（用于文件查找，包含大小写）
IMAGE_EXTENSIONS = (
    [ext.lower() for ext in RAW_EXTENSIONS] +
    [ext.upper() for ext in RAW_EXTENSIONS] +
    [ext.lower() for ext in HEIF_EXTENSIONS] +
    [ext.upper() for ext in HEIF_EXTENSIONS] +
    [ext.lower() for ext in JPG_EXTENSIONS] +
    [ext.upper() for ext in JPG_EXTENSIONS]
)

# V4.3 Phase 1: 视频分析支持的扩展名
# macOS 优先：OpenCV 原生支持 + AVFoundation 后端解码
# Supported video extensions for video analysis (Phase 1, macOS first)
VIDEO_EXTENSIONS = ['.mp4', '.mov', '.m4v']
VIDEO_EXTENSIONS_ALL = (
    [ext.lower() for ext in VIDEO_EXTENSIONS] +
    [ext.upper() for ext in VIDEO_EXTENSIONS]
)
