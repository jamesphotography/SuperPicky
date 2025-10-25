#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V3.1 - Post Digital Adjustment Engine
后期评分调整引擎 - 基于已有CSV数据重新计算星级评分
"""

import os
import csv
from typing import List, Dict, Set, Optional, Tuple


class PostAdjustmentEngine:
    """后期评分调整引擎"""

    def __init__(self, directory: str):
        """
        初始化引擎

        Args:
            directory: 照片目录路径
        """
        self.directory = directory
        self.report_path = os.path.join(directory, "_tmp", "report.csv")
        self.photos_data: List[Dict] = []
        self.image_extensions = ['.nef', '.NEF', '.cr2', '.CR2', '.arw', '.ARW',
                                '.jpg', '.JPG', '.jpeg', '.JPEG', '.dng', '.DNG']

    def load_report(self) -> Tuple[bool, str]:
        """
        加载 report.csv

        Returns:
            (成功标志, 错误消息或成功消息)
        """
        # 检查文件是否存在
        if not os.path.exists(self.report_path):
            return False, f"未找到分析报告文件：{self.report_path}"

        try:
            with open(self.report_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)

                # 验证必需字段
                required_fields = ['文件名', '是否有鸟', '置信度', '归一化锐度',
                                  'NIMA美学', 'BRISQUE技术', '评分']

                if not all(field in reader.fieldnames for field in required_fields):
                    return False, "CSV文件格式不正确，缺少必需字段"

                # 读取所有数据
                all_photos = list(reader)

                # 只加载有鸟的照片（评分 >= 0）
                self.photos_data = [
                    photo for photo in all_photos
                    if photo['是否有鸟'] == '是'
                ]

                total_count = len(all_photos)
                bird_count = len(self.photos_data)

                return True, f"成功加载 {bird_count} 张有鸟照片数据（共 {total_count} 张）"

        except Exception as e:
            return False, f"读取CSV文件失败：{str(e)}"

    def find_image_file(self, filename_without_ext: str) -> Optional[str]:
        """
        根据文件名（无扩展名）查找实际图片文件

        Args:
            filename_without_ext: 不含扩展名的文件名

        Returns:
            完整文件路径，或None（如果文件不存在）
        """
        # 优先级：NEF/CR2/ARW（RAW） > JPG/JPEG > DNG
        priority_extensions = ['.nef', '.NEF', '.cr2', '.CR2', '.arw', '.ARW']
        secondary_extensions = ['.jpg', '.JPG', '.jpeg', '.JPEG']
        tertiary_extensions = ['.dng', '.DNG']

        all_extensions = priority_extensions + secondary_extensions + tertiary_extensions

        for ext in all_extensions:
            file_path = os.path.join(self.directory, filename_without_ext + ext)
            if os.path.exists(file_path):
                return file_path

        return None  # 文件不存在

    def recalculate_ratings(
        self,
        photos: List[Dict],
        min_confidence: float,
        min_sharpness: float,
        min_nima: float,
        max_brisque: float,
        sharpness_threshold: float,
        nima_threshold: float
    ) -> List[Dict]:
        """
        根据新阈值重新计算所有照片的星级

        实现与 ai_model.py:347-378 相同的评分逻辑

        Args:
            photos: 照片数据列表
            min_confidence: 0星阈值 - 置信度
            min_sharpness: 0星阈值 - 锐度
            min_nima: 0星阈值 - 美学
            max_brisque: 0星阈值 - 失真
            sharpness_threshold: 2/3星阈值 - 锐度
            nima_threshold: 2/3星阈值 - 美学

        Returns:
            新的照片数据列表（含新星级）
        """
        new_photos = []

        for photo in photos:
            # 解析CSV中的数据
            try:
                conf = float(photo['置信度'])
                sharpness = float(photo['归一化锐度'])

                # 处理 "-" 值（某些照片可能没有美学/失真评分）
                nima_str = photo['NIMA美学']
                brisque_str = photo['BRISQUE技术']

                nima_score = float(nima_str) if nima_str != '-' else None
                brisque_score = float(brisque_str) if brisque_str != '-' else None

            except (ValueError, KeyError) as e:
                # 数据格式错误，跳过该照片
                print(f"警告: 照片 {photo.get('文件名', 'unknown')} 数据格式错误: {e}")
                continue

            # 判定星级（与 ai_model.py:357-378 完全一致）
            # 0星判定（技术质量差）
            if conf < min_confidence or \
               (brisque_score is not None and brisque_score > max_brisque) or \
               (nima_score is not None and nima_score < min_nima) or \
               sharpness < min_sharpness:
                rating = 0
            # 3星判定（优选：锐度和美学双达标）
            elif sharpness >= sharpness_threshold and \
                 (nima_score is not None and nima_score >= nima_threshold):
                rating = 3
            # 2星判定（良好：锐度或美学达标其一）
            elif sharpness >= sharpness_threshold or \
                 (nima_score is not None and nima_score >= nima_threshold):
                rating = 2
            # 1星（普通）
            else:
                rating = 1

            # 添加新星级到数据
            photo_copy = photo.copy()
            photo_copy['新星级'] = rating
            new_photos.append(photo_copy)

        return new_photos

    def recalculate_picked(
        self,
        star_3_photos: List[Dict],
        picked_percentage: int
    ) -> Set[str]:
        """
        重新计算精选旗标（3星照片的双Top%交集）

        实现与 main.py:355-399 相同的精选逻辑

        Args:
            star_3_photos: 3星照片列表
            picked_percentage: 精选百分比 (10-50)

        Returns:
            应设置精选旗标的文件名集合（不含扩展名）
        """
        if len(star_3_photos) == 0:
            return set()

        # 计算需要选取的数量（至少1张）
        top_percent = picked_percentage / 100.0
        top_count = max(1, int(len(star_3_photos) * top_percent))

        # 按美学排序，取Top N%
        # 需要过滤掉没有美学评分的照片
        photos_with_nima = [
            p for p in star_3_photos
            if p['NIMA美学'] != '-'
        ]

        if len(photos_with_nima) == 0:
            return set()

        sorted_by_nima = sorted(
            photos_with_nima,
            key=lambda x: float(x['NIMA美学']),
            reverse=True
        )
        nima_top_files = set([photo['文件名'] for photo in sorted_by_nima[:top_count]])

        # 按锐度排序，取Top N%
        sorted_by_sharpness = sorted(
            star_3_photos,
            key=lambda x: float(x['归一化锐度']),
            reverse=True
        )
        sharpness_top_files = set([photo['文件名'] for photo in sorted_by_sharpness[:top_count]])

        # 计算交集（同时在美学和锐度Top N%中的照片）
        picked_files = nima_top_files & sharpness_top_files

        return picked_files

    def get_statistics(self, photos: List[Dict]) -> Dict[str, int]:
        """
        统计各星级照片数量

        Args:
            photos: 照片数据列表（必须包含'新星级'字段）

        Returns:
            {'star_3': 50, 'star_2': 80, 'star_1': 200, 'star_0': 120, 'total': 450}
        """
        stats = {
            'star_0': 0,
            'star_1': 0,
            'star_2': 0,
            'star_3': 0,
            'total': len(photos)
        }

        for photo in photos:
            rating = photo.get('新星级', photo.get('评分', 0))
            if isinstance(rating, str):
                rating = int(rating)

            if rating == 0:
                stats['star_0'] += 1
            elif rating == 1:
                stats['star_1'] += 1
            elif rating == 2:
                stats['star_2'] += 1
            elif rating == 3:
                stats['star_3'] += 1

        return stats
