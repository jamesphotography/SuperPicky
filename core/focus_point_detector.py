#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Focus Point Detector - 对焦点检测器
用于从 Nikon Z8/Z9 RAW 文件中提取对焦点坐标，并验证是否落在鸟的 Bounding Box 内

支持功能：
- 读取 Nikon MakerNotes 中的对焦数据
- 坐标归一化 (0.0-1.0)
- 竖拍旋转处理
- DX 裁切修正
- 与 YOLO Bounding Box 碰撞检测
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import subprocess
import json
import math
import numpy as np
import atexit
import sys
import os


# ============ Exiftool 常驻进程管理 ============
# 使用 -stay_open 模式保持进程常驻，避免每次启动的开销
_exiftool_process = None


def _start_exiftool_process(exiftool_path: str = 'exiftool'):
    """启动 exiftool 常驻进程"""
    global _exiftool_process
    try:
        # V3.9.4: 在 Windows 上隐藏控制台窗口
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
        
        _exiftool_process = subprocess.Popen(
            [exiftool_path, '-charset', 'utf8', '-stay_open', 'True', '-@', '-'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,  # 使用 bytes 模式，避免自动解码
            creationflags=creationflags,  # 隐藏窗口
            bufsize=0  # 无缓冲（二进制模式不支持行缓冲）
        )
        return _exiftool_process
    except Exception:
        return None


def _stop_exiftool_process():
    """停止 exiftool 常驻进程"""
    global _exiftool_process
    if _exiftool_process is not None:
        try:
            _exiftool_process.stdin.write('-stay_open\nFalse\n'.encode('utf-8'))
            _exiftool_process.stdin.flush()
            _exiftool_process.wait(timeout=5)
        except Exception:
            try:
                _exiftool_process.kill()
            except Exception:
                pass
        _exiftool_process = None


# 程序退出时自动清理进程
atexit.register(_stop_exiftool_process)


@dataclass
class FocusPointResult:
    """对焦点检测结果"""
    x: float                    # 归一化 X 坐标 (0.0-1.0)
    y: float                    # 归一化 Y 坐标 (0.0-1.0)
    raw_x: int                  # 原始像素 X 坐标
    raw_y: int                  # 原始像素 Y 坐标
    area_width: int             # 对焦区域宽度
    area_height: int            # 对焦区域高度
    af_mode: str                # AF 模式 (AF-C, AF-S 等)
    area_mode: str              # 区域模式 (Auto, 3D-Tracking 等)
    focus_result: any           # 合焦结果 (1=Focus 或 "Focus")
    is_valid: bool              # 是否有效对焦数据
    
    @property
    def is_focused(self) -> bool:
        """是否成功合焦 (FocusResult: 1=Focus, 其他=未合焦)"""
        # ExifTool -n 返回数值: 1 = Focus
        if isinstance(self.focus_result, (int, float)):
            return int(self.focus_result) == 1
        return str(self.focus_result).lower() == 'focus'


class FocusPointDetector:
    """
    对焦点检测器 - 多相机品牌支持
    
    支持相机:
    - Nikon Z8/Z9/Z6/Z7 + D 系列 (NEF/NRW) ✅
    - Sony A1/A7R5/A7M4/A9/A6xxx (ARW) ✅
    - Canon R1/R3/R5/R6/R7/R8 + EOS 系列 (CR3/CR2) ✅
    - Olympus/OM System OM-1/OM-5/E-M1 (ORF) ✅
    - Fujifilm X-T5/X-H2/GFX (RAF) ✅
    - Panasonic S5/GH6/G9 (RW2) ✅
    """
    
    # 通用标签 + 各品牌特有标签
    COMMON_TAGS = ['Make', 'Model', 'FocusMode', 'Orientation']
    
    NIKON_TAGS = [
        'AFAreaMode',
        'AFAreaXPosition',
        'AFAreaYPosition',
        'AFAreaWidth',
        'AFAreaHeight',
        'AFImageWidth',
        'AFImageHeight',
        'FocusResult',
        'CropArea',
        'CropHiSpeed',  # V4.2: DX Crop 模式的正确偏移信息
    ]
    
    SONY_TAGS = [
        'FocusLocation',
        'AFAreaMode',
        'FocusFrameSize',
    ]
    
    # Canon: 基于 Focus-Points 仓库分析
    # 坐标是从图像中心偏移的！需要 +imageWidth/2 和 +imageHeight/2
    CANON_TAGS = [
        'AFAreaMode',
        'AFPointsInFocus',      # 合焦点索引 (0-based, 逗号分隔)
        'AFAreaXPositions',     # X 坐标列表 (空格分隔, 从中心偏移)
        'AFAreaYPositions',     # Y 坐标列表 (空格分隔, 从中心偏移)
        'AFAreaWidths',         # 对焦框宽度列表
        'AFAreaHeights',        # 对焦框高度列表
        'AFImageWidth',         # AF 图像宽度
        'AFImageHeight',        # AF 图像高度
        'ExifImageWidth',       # 备用图像宽度
        'ExifImageHeight',      # 备用图像高度
    ]
    
    # Olympus/OM System: 基于 Focus-Points 仓库分析
    # 新 OM 系统: AF Frame Size + AF Focus Area
    # 旧 Olympus: AF Point Selected (归一化 0-1)
    OLYMPUS_TAGS = [
        'AFPointSelected',      # 归一化坐标 "x y" (0.0-1.0)
        'AFFrameSize',          # AF 框尺寸 "w h"
        'AFFocusArea',          # 对焦区域 "x y w h"
        'AFSelectedArea',       # 选择区域
        'AFAreaMode',
        'ExifImageWidth',
        'ExifImageHeight',
    ]
    
    # Fujifilm: 基于 Focus-Points 仓库分析
    # Focus Pixel: 像素坐标 "x y" (基于 RawImageCroppedSize)
    FUJIFILM_TAGS = [
        'FocusPixel',           # 像素坐标 "x y"
        'FocusMode',
        'AFMode',
        'AFAreaMode',
        'RawImageCroppedSize',  # V3.9: 正确尺寸 (如 7728x5152)
        'ExifImageWidth',       # 备用
        'ExifImageHeight',      # 备用
    ]
    
    # Panasonic: 基于 Focus-Points 仓库分析  
    # AF Point Position: 归一化坐标 "0.x 0.y" (0.0-1.0)
    PANASONIC_TAGS = [
        'AFPointPosition',      # 归一化坐标 "0.5 0.5"
        'AFAreaSize',           # 对焦框尺寸 (归一化)
        'FocusMode',
        'AFAreaMode',
        'ExifImageWidth',
        'ExifImageHeight',
    ]
    
    def __init__(self, exiftool_path: str = 'exiftool'):
        """
        初始化检测器
        
        Args:
            exiftool_path: ExifTool 可执行文件路径
        """
        self.exiftool_path = exiftool_path
    
    def detect(self, raw_path: str) -> Optional[FocusPointResult]:
        """
        检测对焦点
        
        Args:
            raw_path: RAW 文件路径 (NEF/ARW/CR3/ORF)
            
        Returns:
            FocusPointResult 或 None (无对焦数据)
        """
        # 先读取通用标签确定相机品牌
        exif_data = self._read_exif(raw_path, self.COMMON_TAGS)
        if exif_data is None:
            return None
        
        make = str(exif_data.get('Make', '')).upper()
        
        # 根据品牌选择解析方法
        if 'NIKON' in make:
            return self._detect_nikon(raw_path, exif_data)
        elif 'SONY' in make:
            return self._detect_sony(raw_path, exif_data)
        elif 'CANON' in make:
            return self._detect_canon(raw_path, exif_data)
        elif 'OLYMPUS' in make or 'OM DIGITAL' in make:
            return self._detect_olympus(raw_path, exif_data)
        elif 'FUJIFILM' in make or 'FUJI' in make:
            return self._detect_fujifilm(raw_path, exif_data)
        elif 'PANASONIC' in make:
            return self._detect_panasonic(raw_path, exif_data)
        else:
            return None  # 不支持的相机品牌
    
    def _detect_nikon(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """Nikon Z8/Z9 对焦点检测"""
        exif_data = self._read_exif(raw_path, self.NIKON_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查是否为 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if not focus_mode or 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None  # 手动对焦无数据
        
        # 获取对焦坐标
        af_x = exif_data.get('AFAreaXPosition')
        af_y = exif_data.get('AFAreaYPosition')
        af_img_w = exif_data.get('AFImageWidth')
        af_img_h = exif_data.get('AFImageHeight')
        
        if af_x is None or af_y is None or af_img_w is None or af_img_h is None:
            return None  # 缺少关键数据
        
        try:
            raw_x = int(af_x)
            raw_y = int(af_y)
            img_w = int(af_img_w)
            img_h = int(af_img_h)
        except (ValueError, TypeError):
            return None
        
        # 处理 DX 裁切
        raw_x, raw_y, img_w, img_h = self._apply_crop_correction(
            raw_x, raw_y, img_w, img_h, exif_data
        )
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取其他信息
        area_w = int(exif_data.get('AFAreaWidth', 0))
        area_h = int(exif_data.get('AFAreaHeight', 0))
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        focus_result = exif_data.get('FocusResult', 1)  # Nikon: 1=Focus
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=focus_result,
            is_valid=True
        )
    
    def _detect_sony(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """Sony A1/A7R5/A7M4 对焦点检测"""
        exif_data = self._read_exif(raw_path, self.SONY_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查是否为 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        # Sony FocusMode: 1=MF, 2=AF-S, 3=AF-C, 4=AF-A 等
        if focus_mode == '1' or 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None  # 手动对焦无数据
        
        # Sony FocusLocation 格式: "imgW imgH focusX focusY"
        focus_location = exif_data.get('FocusLocation', '')
        if not focus_location:
            return None
        
        try:
            parts = str(focus_location).split()
            if len(parts) >= 4:
                img_w = int(parts[0])
                img_h = int(parts[1])
                raw_x = int(parts[2])
                raw_y = int(parts[3])
            else:
                return None
        except (ValueError, IndexError):
            return None
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取对焦框大小 + 合焦标志
        # FocusFrameSize 格式（-n 模式）: "width height validity_flag"
        # 第 3 个值：0 = 焦框无效 (n/a)，非 0 = 焦框有效 → 用作合焦标志
        frame_size = exif_data.get('FocusFrameSize', '')
        area_w, area_h = 0, 0
        focus_result = 1  # 默认合焦（无法获取标志时保守假设）
        if frame_size:
            try:
                fs_parts = str(frame_size).split()
                if len(fs_parts) >= 2:
                    area_w = int(fs_parts[0])
                    area_h = int(fs_parts[1])
                if len(fs_parts) >= 3:
                    focus_result = 1 if int(fs_parts[2]) != 0 else 0
            except (ValueError, IndexError):
                pass

        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))

        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=focus_result,
            is_valid=True
        )
    
    def _detect_canon(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Canon EOS R 系列 / DSLR 对焦点检测
        
        Canon 坐标系统: 从图像中心偏移！
        实际坐标 = 中心 + 偏移量
        """
        exif_data = self._read_exif(raw_path, self.CANON_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None
        
        # 获取图像尺寸
        img_w = exif_data.get('AFImageWidth') or exif_data.get('ExifImageWidth')
        img_h = exif_data.get('AFImageHeight') or exif_data.get('ExifImageHeight')
        if img_w is None or img_h is None:
            return None
        img_w, img_h = int(img_w), int(img_h)
        
        # 获取对焦点坐标列表
        x_positions = exif_data.get('AFAreaXPositions', '')
        y_positions = exif_data.get('AFAreaYPositions', '')
        if not x_positions or not y_positions:
            return None
        
        try:
            x_list = [int(x) for x in str(x_positions).split()]
            y_list = [int(y) for y in str(y_positions).split()]
        except (ValueError, AttributeError):
            return None
        
        if not x_list or not y_list:
            return None
        
        # 获取合焦点索引
        # exiftool -n 模式下 AFPointsInFocus 返回空格分隔的位掩码数组，例如 '1 0 0 0 ...'
        # 其中值为 1 的位的下标即合焦 AF 点的索引（第0位=1 表示 AF点0合焦）
        # 也兼容少数相机返回逗号分隔索引列表的情况
        af_points_in_focus = exif_data.get('AFPointsInFocus', '')
        focus_indices = []
        if af_points_in_focus is not None and str(af_points_in_focus).strip():
            af_str = str(af_points_in_focus).strip()
            try:
                if ',' in af_str:
                    # 逗号分隔的索引列表：'0,1' → [0, 1]
                    focus_indices = [int(i.strip()) for i in af_str.split(',') if i.strip().isdigit()]
                else:
                    # 空格分隔的位掩码数组：'1 0 0 0 ...' → 找值为1的下标
                    values = af_str.split()
                    focus_indices = [i for i, v in enumerate(values) if v in ('1', '1.0')]
            except (ValueError, IndexError):
                focus_indices = []
        
        # 如果有合焦点，使用第一个合焦点；否则使用第一个点
        if focus_indices and focus_indices[0] < len(x_list):
            idx = focus_indices[0]
        else:
            idx = 0
        
        # Canon 坐标从中心偏移
        # X: 正值向右，负值向左 → 正常相加
        # Y: 取决于相机类型：
        #   - 紧凑型相机（PowerShot/IXUS）: yDirection = 1（正常方向）
        #   - EOS 系列（DSLR/无反）: yDirection = -1（Y轴反转）
        # 参考: Focus-Points 项目 CanonDelegates.lua
        
        # 检测是否为紧凑型相机
        model = str(common_data.get('Model', '')).upper()
        is_compact = 'POWERSHOT' in model or 'IXUS' in model or 'ELPH' in model
        y_direction = 1 if is_compact else -1  # 紧凑型: +1, EOS: -1
        
        raw_x = img_w // 2 + x_list[idx]
        raw_y = img_h // 2 + (y_list[idx] * y_direction)
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取对焦框尺寸
        widths = exif_data.get('AFAreaWidths', '')
        heights = exif_data.get('AFAreaHeights', '')
        area_w, area_h = 0, 0
        try:
            if widths:
                w_list = [int(w) for w in str(widths).split()]
                area_w = w_list[idx] if idx < len(w_list) else w_list[0] if w_list else 0
            if heights:
                h_list = [int(h) for h in str(heights).split()]
                area_h = h_list[idx] if idx < len(h_list) else h_list[0] if h_list else 0
        except (ValueError, IndexError):
            pass
        
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1 if focus_indices else 0,  # 有合焦点则合焦
            is_valid=True
        )
    
    def _detect_olympus(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Olympus/OM System 对焦点检测
        
        Olympus 坐标系统:
        - AF Point Selected: 归一化坐标 "x y" (0.0-1.0)
        - AF Focus Area: 像素坐标 "x y w h"
        """
        exif_data = self._read_exif(raw_path, self.OLYMPUS_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or focus_mode == 'MF; MF':
            return None
        
        # 获取图像尺寸
        img_w = exif_data.get('ExifImageWidth')
        img_h = exif_data.get('ExifImageHeight')
        
        # 尝试方法 1: AF Point Selected (归一化坐标)
        af_point_selected = exif_data.get('AFPointSelected', '')
        if af_point_selected and af_point_selected != '0 0':
            try:
                parts = str(af_point_selected).split()
                if len(parts) >= 2:
                    norm_x = float(parts[0])
                    norm_y = float(parts[1])
                    
                    # 检查是否为百分比格式
                    if '%' in str(af_point_selected):
                        # 格式如 "(50%,50%)"
                        import re
                        match = re.search(r'\((\d+)%,(\d+)', str(af_point_selected))
                        if match:
                            norm_x = int(match.group(1)) / 100
                            norm_y = int(match.group(2)) / 100
                    
                    # 计算像素坐标
                    if img_w and img_h:
                        raw_x = int(norm_x * int(img_w))
                        raw_y = int(norm_y * int(img_h))
                    else:
                        raw_x, raw_y = 0, 0
                    
                    # 处理竖拍旋转
                    orientation = exif_data.get('Orientation', 1)
                    norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
                    
                    area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
                    
                    return FocusPointResult(
                        x=norm_x,
                        y=norm_y,
                        raw_x=raw_x,
                        raw_y=raw_y,
                        area_width=0,
                        area_height=0,
                        af_mode=focus_mode,
                        area_mode=area_mode,
                        focus_result=1,
                        is_valid=True
                    )
            except (ValueError, IndexError):
                pass
        
        # 尝试方法 2: AF Focus Area (像素坐标)
        af_focus_area = exif_data.get('AFFocusArea', '')
        af_frame_size = exif_data.get('AFFrameSize', '')
        if af_focus_area and af_frame_size:
            try:
                area_parts = str(af_focus_area).split()
                frame_parts = str(af_frame_size).split()
                
                if len(area_parts) >= 4 and len(frame_parts) >= 2:
                    frame_w = int(frame_parts[0])
                    frame_h = int(frame_parts[1])
                    area_x = int(area_parts[0])
                    area_y = int(area_parts[1])
                    area_w = int(area_parts[2])
                    area_h = int(area_parts[3])
                    
                    # 计算中心点并归一化
                    center_x = area_x + area_w // 2
                    center_y = area_y + area_h // 2
                    norm_x = center_x / frame_w if frame_w > 0 else 0.5
                    norm_y = center_y / frame_h if frame_h > 0 else 0.5
                    
                    # 处理竖拍旋转
                    orientation = exif_data.get('Orientation', 1)
                    norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
                    
                    area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
                    
                    return FocusPointResult(
                        x=norm_x,
                        y=norm_y,
                        raw_x=center_x,
                        raw_y=center_y,
                        area_width=area_w,
                        area_height=area_h,
                        af_mode=focus_mode,
                        area_mode=area_mode,
                        focus_result=1,
                        is_valid=True
                    )
            except (ValueError, IndexError):
                pass
        
        return None
    
    def _detect_fujifilm(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Fujifilm X/GFX 系列对焦点检测
        
        Fujifilm 坐标系统:
        - Focus Pixel: 像素坐标 "x y"
        """
        exif_data = self._read_exif(raw_path, self.FUJIFILM_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None
        
        # 获取 Focus Pixel
        focus_pixel = exif_data.get('FocusPixel', '')
        if not focus_pixel:
            return None
        
        try:
            parts = str(focus_pixel).split()
            if len(parts) >= 2:
                raw_x = int(parts[0])
                raw_y = int(parts[1])
            else:
                return None
        except (ValueError, IndexError):
            return None
        
        # 获取图像尺寸
        # 重要：富士 FocusPixel 的坐标系是相机内嵌 JPEG 预览图的尺寸（如 4416×2944），
        # 而不是 RAW 全尺寸（如 7728×5152）。
        # V3.9 曾错误地使用 RawImageCroppedSize（7728×5152）作为分母，
        # 导致归一化坐标从正确的 ~0.50 偏移到错误的 ~0.29，焦点显示严重错位。
        # 正确做法：直接用 ExifImageWidth/Height（相机写入的内嵌预览 JPEG 尺寸）作为分母。
        img_w = exif_data.get('ExifImageWidth')
        img_h = exif_data.get('ExifImageHeight')
        

        if img_w is None or img_h is None:
            return None
        img_w, img_h = int(img_w), int(img_h)
        
        # 归一化坐标
        norm_x = raw_x / img_w if img_w > 0 else 0.5
        norm_y = raw_y / img_h if img_h > 0 else 0.5
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        area_mode = str(exif_data.get('AFAreaMode') or exif_data.get('AFMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=0,
            area_height=0,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1,
            is_valid=True
        )
    
    def _detect_panasonic(self, raw_path: str, common_data: dict) -> Optional[FocusPointResult]:
        """
        Panasonic LUMIX S/G 系列对焦点检测
        
        Panasonic 坐标系统:
        - AF Point Position: 归一化坐标 "0.5 0.5" (0.0-1.0)
        """
        exif_data = self._read_exif(raw_path, self.PANASONIC_TAGS)
        if exif_data is None:
            return None
        exif_data.update(common_data)
        
        # 检查 AF 模式
        focus_mode = str(exif_data.get('FocusMode', '')).strip()
        if 'MF' in focus_mode.upper() or 'MANUAL' in focus_mode.upper():
            return None
        
        # 获取 AF Point Position (归一化坐标)
        af_point_position = exif_data.get('AFPointPosition', '')
        if not af_point_position:
            return None
        
        # 检查是否为无效值
        if '4.194e' in str(af_point_position):  # Panasonic 的 "未找到" 标记
            return None
        
        try:
            parts = str(af_point_position).split()
            if len(parts) >= 2:
                norm_x = float(parts[0])
                norm_y = float(parts[1])
            else:
                return None
        except (ValueError, IndexError):
            return None
        
        # 获取图像尺寸计算像素坐标
        img_w = exif_data.get('ExifImageWidth')
        img_h = exif_data.get('ExifImageHeight')
        if img_w and img_h:
            raw_x = int(norm_x * int(img_w))
            raw_y = int(norm_y * int(img_h))
        else:
            raw_x, raw_y = 0, 0
        
        # 处理竖拍旋转
        orientation = exif_data.get('Orientation', 1)
        norm_x, norm_y = self._apply_orientation_correction(norm_x, norm_y, orientation)
        
        # 获取对焦框尺寸
        area_w, area_h = 0, 0
        af_area_size = exif_data.get('AFAreaSize', '')
        if af_area_size and img_w and img_h:
            try:
                size_parts = str(af_area_size).split()
                if len(size_parts) >= 2:
                    area_w = int(float(size_parts[0]) * int(img_w))
                    area_h = int(float(size_parts[1]) * int(img_h))
            except (ValueError, IndexError):
                pass
        
        area_mode = str(exif_data.get('AFAreaMode', 'Unknown'))
        
        return FocusPointResult(
            x=norm_x,
            y=norm_y,
            raw_x=raw_x,
            raw_y=raw_y,
            area_width=area_w,
            area_height=area_h,
            af_mode=focus_mode,
            area_mode=area_mode,
            focus_result=1,
            is_valid=True
        )
    
    def _read_exif(self, file_path: str, tags: list) -> Optional[dict]:
        """读取指定的 EXIF 标签（使用常驻进程模式）"""
        global _exiftool_process
        
        # -stay_open 模式按换行分割参数；文件名包含换行会导致参数注入
        # 此时回退到单次调用（argv 列表）以保证文件名按原样传递
        if any(ch in file_path for ch in ('\n', '\r')):
            return self._read_exif_single(file_path, tags)
        
        # 使用全局常驻进程
        if _exiftool_process is None or _exiftool_process.poll() is not None:
            _exiftool_process = _start_exiftool_process(self.exiftool_path)
        
        if _exiftool_process is None:
            # 回退到单次调用模式
            return self._read_exif_single(file_path, tags)
        
        try:
            # 构建参数
            args = ['-j', '-n']
            for tag in tags:
                args.append(f'-{tag}')
            args.append(file_path)
            
            # 发送命令到常驻进程
            cmd_str = '\n'.join(args) + '\n-execute\n'
            _exiftool_process.stdin.write(cmd_str.encode('utf-8'))
            _exiftool_process.stdin.flush()
            
            # 读取响应（直到 {ready} 标记）
            output_bytes = b""
            while True:
                line_bytes = _exiftool_process.stdout.readline()
                if not line_bytes:
                    break
                output_bytes += line_bytes
                if b'{ready}' in line_bytes:
                    break
            
            # 尝试多种编码解码
            decoded_output = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    decoded_output = output_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            
            if decoded_output is None:
                # 如果所有编码都失败，使用 latin-1 作为最后手段（不会失败）
                decoded_output = output_bytes.decode('latin-1')
            
            # 解析 JSON
            output = decoded_output.strip()
            if output:
                data = json.loads(output)
                return data[0] if data else None
            return None
        except Exception:
            return self._read_exif_single(file_path, tags)
    
    def _read_exif_single(self, file_path: str, tags: list) -> Optional[dict]:
        """读取 EXIF（单次调用模式，作为回退）"""
        cmd = [self.exiftool_path, '-j', '-n']
        for tag in tags:
            cmd.append(f'-{tag}')
        cmd.append(file_path)
        
        try:
            # V3.9.4: 在 Windows 上隐藏控制台窗口
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            result = subprocess.run(
                [self.exiftool_path, '-charset', 'utf8'] + cmd[1:], 
                capture_output=True, 
                text=False,  # 使用 bytes 模式，避免自动解码
                timeout=30, 
                creationflags=creationflags
            )
            if result.returncode != 0:
                return None
            
            stdout_bytes = result.stdout or b""
            if not stdout_bytes.strip():
                return None
            
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
                
            data = json.loads(decoded_output)
            return data[0] if data else None
        except Exception:
            return None
    
    def _apply_crop_correction(
        self, 
        x: int, 
        y: int, 
        img_w: int, 
        img_h: int, 
        exif_data: dict
    ) -> Tuple[int, int, int, int]:
        """
        处理 DX 裁切修正
        
        V4.2: 优先使用 CropHiSpeed（包含正确的 DX 偏移）
        CropHiSpeed 格式 (Nikon -n): "mode full_w full_h crop_w crop_h crop_left crop_top"
        例如: "2 8280 5520 5408 3608 1436 956" 表示 DX 裁切
        
        CropArea 格式: "left top width height" (通常不准确，作为后备)
        """
        # V4.2: 优先使用 CropHiSpeed（Nikon DX 裁切的正确偏移）
        crop_hi_speed = exif_data.get('CropHiSpeed', '')
        if crop_hi_speed:
            try:
                parts = str(crop_hi_speed).split()
                # 格式: mode full_w full_h crop_w crop_h crop_left crop_top
                if len(parts) >= 7:
                    crop_mode = int(parts[0])
                    full_w = int(parts[1])
                    full_h = int(parts[2])
                    crop_w = int(parts[3])
                    crop_h = int(parts[4])
                    crop_left = int(parts[5])
                    crop_top = int(parts[6])
                    
                    # mode: 0=Off, 1=高速, 2=DX 1.3x, 3=DX 1.5x 等
                    if crop_mode > 0 and crop_left > 0 and crop_top > 0:
                        # 计算对焦点在裁切区域内的相对坐标
                        # AF 坐标是基于 AFImageWidth/Height 的，需要缩放到 full_w/full_h
                        scale_x = full_w / img_w if img_w > 0 else 1.0
                        scale_y = full_h / img_h if img_h > 0 else 1.0
                        
                        # 将 AF 坐标转换到全尺寸图片坐标
                        x_full = int(x * scale_x)
                        y_full = int(y * scale_y)
                        
                        # 减去裁切偏移
                        x_crop = x_full - crop_left
                        y_crop = y_full - crop_top
                        
                        return x_crop, y_crop, crop_w, crop_h
            except (ValueError, IndexError):
                pass
        
        # 后备: 使用 CropArea
        crop_area = exif_data.get('CropArea', '')
        if not crop_area:
            return x, y, img_w, img_h
        
        try:
            parts = str(crop_area).split()
            if len(parts) >= 4:
                crop_left = int(parts[0])
                crop_top = int(parts[1])
                crop_w = int(parts[2])
                crop_h = int(parts[3])
                
                # 如果裁切区域与图像尺寸差异大于 5%，才进行修正
                if abs(crop_w - img_w) > img_w * 0.05 or abs(crop_h - img_h) > img_h * 0.05:
                    # DX 模式：坐标需要减去偏移量
                    x = x - crop_left
                    y = y - crop_top
                    img_w = crop_w
                    img_h = crop_h
        except (ValueError, IndexError):
            pass
        
        return x, y, img_w, img_h
    
    def _apply_orientation_correction(
        self, 
        x: float, 
        y: float, 
        orientation: int
    ) -> Tuple[float, float]:
        """
        处理竖拍旋转
        
        Orientation 值:
        - 1: Horizontal (正常)
        - 6: Rotate 90 CW (顺时针旋转 90°)
        - 8: Rotate 270 CW (顺时针旋转 270°)
        """
        if orientation == 6:
            # 顺时针 90°: (x, y) -> (y, 1-x)
            return y, 1.0 - x
        elif orientation == 8:
            # 顺时针 270° (逆时针 90°): (x, y) -> (1-y, x)
            return 1.0 - y, x
        else:
            return x, y


def verify_focus_in_bbox(
    focus: Optional[FocusPointResult], 
    bbox: Tuple[int, int, int, int],
    img_dims: Tuple[int, int],
    seg_mask: np.ndarray = None,
    head_center: Tuple[int, int] = None,
    head_radius: int = None,
) -> Tuple[float, float]:
    """
    验证对焦点位置，返回锐度权重和美学权重
    
    V4.0: 采用 4 层检测策略，返回两个权重：
    1. 头部区域内 → 锐度 1.1, 美学 1.0 (最佳)
    2. SEG 掩码内 → 锐度 0.9, 美学 1.0 (轻微惩罚)
    3. BBox 内 → 锐度 0.7, 美学 0.9 (中度惩罚)
    4. BBox 外 → 锐度 0.5, 美学 0.8 (严重惩罚)
    
    Args:
        focus: 对焦点检测结果
        bbox: YOLO 检测的 Bounding Box (x, y, w, h) - 左上角坐标
        img_dims: 图像尺寸 (width, height)
        seg_mask: 分割掩码（原图尺寸），可选
        head_center: 头部圆心 (x, y) 像素坐标，可选
        head_radius: 头部区域半径（像素），可选
        
    Returns:
        (sharpness_weight, topiq_weight) 权重因子元组:
        - (1.1, 1.0): 对焦点在头部区域内
        - (0.9, 1.0): 对焦点在 SEG 掩码内（但不在头部）
        - (0.8, 0.9): 对焦点在 BBox 内（但不在 SEG）
        - (0.5, 0.8): 对焦点在 BBox 外
        - (1.0, 1.0): 无对焦数据
    """
    if focus is None or not focus.is_valid:
        return (1.0, 1.0)  # 无数据，不影响评分
    
    if not focus.is_focused:
        return (0.8, 0.9)  # 未合焦，轻微惩罚
    
    # 转换对焦点为像素坐标
    img_w, img_h = img_dims
    focus_px = (int(focus.x * img_w), int(focus.y * img_h))

    # 检查是否在头部区域内
    if head_center is not None and head_radius is not None:
        dist = math.sqrt((focus_px[0] - head_center[0])**2 + (focus_px[1] - head_center[1])**2)
        if dist <= head_radius:
            return (1.1, 1.0)  # V4.0: 对焦在头部区域，锐度+10%奖励

    # 检查是否在 SEG 掩码内
    if seg_mask is not None:
        # 确保坐标在图像范围内
        fx, fy = focus_px
        if 0 <= fy < seg_mask.shape[0] and 0 <= fx < seg_mask.shape[1]:
            if seg_mask[fy, fx] > 0:
                return (0.9, 1.0)  # 对焦在鸟身上（但不在头部），轻微惩罚

    # 检查是否在 BBox 内
    bx, by, bw, bh = bbox
    in_bbox = (bx <= focus_px[0] <= bx + bw) and (by <= focus_px[1] <= by + bh)
    
    if in_bbox:
        return (0.8, 0.9)  # V4.0: BBox内，锐度×0.8，美学×0.9（远距小鸟减轻惩罚）
    else:
        return (0.5, 0.8)  # V4.0: BBox外，锐度×0.5，美学×0.8


# 全局单例
_focus_detector: Optional[FocusPointDetector] = None


def _get_exiftool_path() -> str:
    """获取 exiftool 路径（支持 PyInstaller 打包）"""
    # V3.9.4: 处理 Windows 平台的可执行文件后缀
    is_windows = sys.platform.startswith('win')
    exe_name = 'exiftool.exe' if is_windows else 'exiftool'

    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包后 - V4.0.2: 使用平台特定目录
        if is_windows:
            path = os.path.join(sys._MEIPASS, 'exiftools_win', exe_name)
        else:
            path = os.path.join(sys._MEIPASS, 'exiftools_mac', exe_name)
        if not os.path.exists(path):
            # 备选路径
            fallback = os.path.join(sys._MEIPASS, 'exiftools_mac', 'exiftool')
            if os.path.exists(fallback):
                path = fallback
        print(f"🔍 FocusPointDetector: 使用打包 exiftool: {path}")
        return path
    else:
        # 开发环境：V4.0.4 优先使用项目目录的 exiftool（确保支持最新相机如 Nikon Z6-3）
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # 优先检查平台特定目录
        if is_windows:
            project_exiftool = os.path.join(project_root, 'exiftools_win', exe_name)
        else:
            project_exiftool = os.path.join(project_root, 'exiftools_mac', exe_name)
        
        if os.path.exists(project_exiftool):
            return project_exiftool
        
        # 回退：检查项目根目录
        if is_windows:
            win_path = os.path.join(project_root, 'exiftool.exe')
            if os.path.exists(win_path):
                return win_path
        else:
            mac_path = os.path.join(project_root, 'exiftool')
            if os.path.exists(mac_path):
                return mac_path
        
        # 最后回退：使用系统 exiftool
        import shutil
        system_exiftool = shutil.which('exiftool')
        if system_exiftool:
            return system_exiftool
        
        return 'exiftool'  # 让系统报错


def get_focus_detector() -> FocusPointDetector:
    """获取对焦点检测器单例"""
    global _focus_detector
    if _focus_detector is None:
        _focus_detector = FocusPointDetector(exiftool_path=_get_exiftool_path())
    return _focus_detector
