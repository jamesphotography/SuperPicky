#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量处理器
对递归扫描到的原子目录逐个调用 PhotoProcessor.process()，
汇总统计，支持增量跳过。
"""

import os
import json
import time
from typing import Callable, Dict, List, Optional, Sequence, Union
from dataclasses import dataclass, field

from tools.i18n import t
from core.recursive_scanner import DEFAULT_SCAN_MAX_DEPTH, ScannedDirectory, count_photos, is_processed, scan_directories


@dataclass
class BatchResult:
    """批量处理结果"""
    total_dirs: int = 0
    processed_dirs: int = 0
    skipped_dirs: int = 0
    failed_dirs: int = 0
    total_photos: int = 0
    total_time: float = 0.0
    dir_results: List[Dict] = field(default_factory=list)


class BatchProcessor:
    """
    批量处理器
    
    扫描根目录下的所有原子目录，逐个调用 PhotoProcessor 处理。
    """
    
    def __init__(
        self,
        root_dir: str,
        settings,  # ProcessingSettings
        skip_existing: bool = False,
        max_depth: int = DEFAULT_SCAN_MAX_DEPTH,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.root_dir = os.path.abspath(root_dir)
        self.settings = settings
        self.skip_existing = skip_existing
        self.max_depth = max_depth
        self.log = log_fn or print
    
    def scan(self) -> List[ScannedDirectory]:
        """扫描并返回待处理的原子目录摘要列表"""
        return scan_directories(self.root_dir, self.max_depth)
    
    def process(
        self,
        dirs: Sequence[Union[str, ScannedDirectory]],
        organize_files: bool = True,
        cleanup_temp: bool = True,
    ) -> BatchResult:
        """
        批量处理所有目录
        
        Args:
            dirs: 原子目录列表
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时文件
            
        Returns:
            BatchResult 汇总结果
        """
        from core.photo_processor import PhotoProcessor, ProcessingCallbacks
        
        normalized_dirs: List[ScannedDirectory] = []
        for entry in dirs:
            if isinstance(entry, ScannedDirectory):
                normalized_dirs.append(entry)
                continue
            normalized_dirs.append(
                ScannedDirectory(
                    path=entry,
                    depth=-1,
                    photo_count=count_photos(entry),
                )
            )

        result = BatchResult(total_dirs=len(normalized_dirs))
        batch_start = time.time()
        
        for i, scanned_dir in enumerate(normalized_dirs, 1):
            dir_path = scanned_dir.path
            dir_name = os.path.relpath(dir_path, self.root_dir)
            photo_count = scanned_dir.photo_count
            
            # 增量跳过
            if self.skip_existing and is_processed(dir_path):
                self.log(t("cli.batch_skip_processed", i=i, count=len(normalized_dirs), dir_name=dir_name, photo_count=photo_count))
                result.skipped_dirs += 1
                result.dir_results.append({
                    'dir': dir_name,
                    'status': 'skipped',
                    'photos': photo_count,
                })
                continue
            
            self.log(f"\n{'━' * 60}")
            self.log(t("cli.batch_processing_dir", i=i, count=len(normalized_dirs), dir_name=dir_name, photo_count=photo_count))
            self.log(f"{'━' * 60}")
            
            dir_start = time.time()
            
            try:
                # 创建回调
                callbacks = ProcessingCallbacks(
                    log=lambda msg, level="info": self.log(f"  {msg}"),
                    progress=None,
                )
                
                # 创建处理器并执行
                processor = PhotoProcessor(
                    dir_path=dir_path,
                    settings=self.settings,
                    callbacks=callbacks,
                )
                
                proc_result = processor.process(
                    organize_files=organize_files,
                    cleanup_temp=cleanup_temp,
                )
                
                dir_time = time.time() - dir_start
                stats = proc_result.stats
                
                self.log(t("cli.batch_dir_done", dir_time=dir_time, s3=stats.get('star_3', 0), s2=stats.get('star_2', 0), s1=stats.get('star_1', 0), s0=stats.get('star_0', 0), no_bird=stats.get('no_bird', 0)))
                
                result.processed_dirs += 1
                result.total_photos += stats.get('total', 0)
                result.dir_results.append({
                    'dir': dir_name,
                    'status': 'success',
                    'photos': stats.get('total', 0),
                    'star_3': stats.get('star_3', 0),
                    'star_2': stats.get('star_2', 0),
                    'star_1': stats.get('star_1', 0),
                    'star_0': stats.get('star_0', 0),
                    'no_bird': stats.get('no_bird', 0),
                    'time': round(dir_time, 1),
                })
                
            except Exception as e:
                dir_time = time.time() - dir_start
                self.log(t("cli.batch_dir_failed", dir_time=dir_time, e=e))
                result.failed_dirs += 1
                result.dir_results.append({
                    'dir': dir_name,
                    'status': 'failed',
                    'error': str(e),
                    'time': round(dir_time, 1),
                })
        
        result.total_time = time.time() - batch_start
        
        # 保存批量汇总报告
        self._save_batch_report(result)
        
        # 打印汇总
        self._print_summary(result)
        
        return result
    
    def _save_batch_report(self, result: BatchResult):
        """保存批量处理汇总到根目录"""
        report = {
            'version': '1.0',
            'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'root_dir': self.root_dir,
            'total_dirs': result.total_dirs,
            'processed': result.processed_dirs,
            'skipped': result.skipped_dirs,
            'failed': result.failed_dirs,
            'total_photos': result.total_photos,
            'total_time': round(result.total_time, 1),
            'dirs': result.dir_results,
        }
        
        report_path = os.path.join(self.root_dir, '.superpicky_batch.json')
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(t("cli.batch_report_save_failed", e=e))
    
    def _print_summary(self, result: BatchResult):
        """打印批量处理汇总"""
        self.log(f"\n{'═' * 60}")
        self.log(t("cli.batch_summary_title"))
        self.log(f"{'═' * 60}")
        self.log(t("cli.batch_total_dirs", total_dirs=result.total_dirs))
        self.log(t("cli.batch_processed_dirs", processed_dirs=result.processed_dirs))
        if result.skipped_dirs > 0:
            self.log(t("cli.batch_skipped_dirs", skipped_dirs=result.skipped_dirs))
        if result.failed_dirs > 0:
            self.log(t("cli.batch_failed_dirs", failed_dirs=result.failed_dirs))
        self.log(t("cli.batch_total_photos", total_photos=result.total_photos))
        self.log(t("cli.batch_total_time", total_time=result.total_time))
        
        # 汇总各星级
        totals = {'star_3': 0, 'star_2': 0, 'star_1': 0, 'star_0': 0, 'no_bird': 0}
        for d in result.dir_results:
            if d.get('status') == 'success':
                for key in totals:
                    totals[key] += d.get(key, 0)
        
        self.log(t("cli.batch_rating_summary"))
        self.log(t("cli.batch_star3", star_3=totals['star_3']))
        self.log(t("cli.batch_star2", star_2=totals['star_2']))
        self.log(t("cli.batch_star1", star_1=totals['star_1']))
        self.log(t("cli.batch_star0", star_0=totals['star_0']))
        self.log(t("cli.batch_no_bird", no_bird=totals['no_bird']))
