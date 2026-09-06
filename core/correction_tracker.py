# -*- coding: utf-8 -*-
"""
纠错记录器（Correction Tracker）。

职责 / Responsibilities:
  1) 接住「改鸟种确认」事件 → 反查 model_class_id → 写一条 corrections 记录。
  2) 从当前项目 report_db 查同鸟种、可当正样本的其它照片。

⚠️ 时序 / Timing: 调用方必须在把新鸟种覆盖进 report_db 的 photos 表**之前**，
   先调 record_correction 抓住原预测（wrong_cn/wrong_en），否则原预测会丢。
"""
from typing import List, Optional, Dict, Any


class CorrectionTracker:
    """记录纠错并查同鸟种正样本。纯逻辑，无 Qt 依赖。"""

    def __init__(self, report_db: Any, bird_db_manager: Any) -> None:
        """
        参数 / Args:
            report_db: ReportDB 实例（当前项目）。
            bird_db_manager: BirdDatabaseManager 实例（名字→class_id 反查）。
        """
        self._db = report_db
        self._bird_db = bird_db_manager

    def record_correction(
        self,
        filename: str,
        wrong_cn: Optional[str],
        wrong_en: Optional[str],
        corrected_cn: Optional[str],
        corrected_en: Optional[str],
        corrected_latin: Optional[str],
        birdid_confidence: Optional[float] = None,
        photo_key: Any = None,
    ) -> Optional[int]:
        """
        反查 model_class_id 并写库。返回反查到的 class_id（未命中返回 None，
        仍写库但该列 None，供开发者事后人工归类）。

        photo_key 用于合并报告模式：库里跨子目录可能存在同名照片（相机计数器
        循环很常见），只给 filename 定位不到唯一子库，纠错记录会被丢掉。
        调用方把 (source_dir, filename) 传进来即可精确落库；单目录模式下
        ReportDB 忽略该字段。

        photo_key disambiguates same-named photos across sub-directories in
        merged mode; ignored by the single-directory ReportDB.
        """
        model_class_id: Optional[int] = None
        if corrected_latin or corrected_en:
            model_class_id = self._bird_db.get_class_id_by_scientific_name(
                corrected_latin or "", english_name=corrected_en
            )
        self._db.insert_correction({
            "photo_key": photo_key,
            "filename": filename,
            "wrong_cn": wrong_cn,
            "wrong_en": wrong_en,
            "corrected_model_class_id": model_class_id,
            "corrected_cn": corrected_cn,
            "corrected_en": corrected_en,
            "birdid_confidence": birdid_confidence,
        })
        return model_class_id

    def find_positive_samples(
        self,
        corrected_cn: Optional[str],
        corrected_en: Optional[str],
        exclude_filename: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查当前项目内同鸟种正样本（排除被改正图自身）。"""
        return self._db.get_photos_by_species(
            cn=corrected_cn, en=corrected_en, exclude_filename=exclude_filename
        )
