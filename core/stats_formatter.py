#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 统计摘要格式化模块
CLI 和 GUI 共享输出格式
"""

from typing import Dict, List, Callable, Optional


def format_processing_summary(stats: Dict, include_time: bool = True) -> List[str]:
    """
    格式化处理完成摘要
    
    Args:
        stats: 统计数据字典，包含 total, star_3, star_2, star_1, star_0, 
               no_bird, picked, flying, total_time, avg_time
        include_time: 是否包含时间统计
    
    Returns:
        格式化的行列表
    """
    lines = []
    lines.append("=" * 60)
    lines.append("📊 处理完成统计:")
    lines.append("")
    lines.append(f"  总文件数: {stats.get('total', 0)}")
    lines.append(f"  ├─ ⭐⭐⭐ 优选 (3星): {stats.get('star_3', 0)}")
    lines.append(f"  │     └─ 🏆 精选旗标: {stats.get('picked', 0)}")
    lines.append(f"  ├─ ⭐⭐   良好 (2星): {stats.get('star_2', 0)}")
    lines.append(f"  ├─ ⭐     普通 (1星): {stats.get('star_1', 0)}")
    lines.append(f"  ├─ 普通 (0星)  : {stats.get('star_0', 0)}")
    lines.append(f"  └─ ❌    无鸟       : {stats.get('no_bird', 0)}")
    
    # 飞鸟统计
    flying = stats.get('flying', 0)
    if flying > 0:
        lines.append("")
        lines.append(f"  🦅 飞鸟标记: {flying} 张")
    
    # 时间统计
    if include_time:
        lines.append("")
        total_time = stats.get('total_time', 0)
        avg_time = stats.get('avg_time', 0)
        lines.append(f"  总耗时: {total_time:.1f}秒")
        lines.append(f"  平均速度: {avg_time:.1f}秒/张")
    
    lines.append("=" * 60)
    lines.append("")
    lines.append("✅ 所有照片已写入EXIF元数据，可在Lightroom中查看")
    
    return lines


def format_reset_summary(restored: int, failed: int = 0, exif_reset: int = 0) -> List[str]:
    """
    格式化重置完成摘要
    
    Args:
        restored: 已恢复文件数
        failed: 恢复失败数
        exif_reset: EXIF 重置数
    
    Returns:
        格式化的行列表
    """
    lines = []
    lines.append("")
    lines.append("📂 重置完成统计:")
    lines.append("")
    
    if restored > 0:
        lines.append(f"  ✅ 文件恢复: {restored} 张")
    if failed > 0:
        lines.append(f"  ⚠️ 恢复失败: {failed} 张")
    if exif_reset > 0:
        lines.append(f"  ✅ EXIF重置: {exif_reset} 张")
    
    lines.append("")
    lines.append("✅ 目录重置完成！")
    
    return lines


def format_info_summary(
    has_report: bool,
    total_records: int = 0,
    rating_counts: Optional[Dict[int, int]] = None,
    flying_count: int = 0,
    has_manifest: bool = False,
    folder_counts: Optional[Dict[str, int]] = None
) -> List[str]:
    """
    格式化目录信息摘要
    
    Args:
        has_report: 是否存在 report.db
        total_records: 总记录数
        rating_counts: 各星级数量 {3: 100, 2: 200, ...}
        flying_count: 飞鸟数量
        has_manifest: 是否存在 manifest
        folder_counts: 各文件夹文件数 {"3星_优选": 100, ...}
    
    Returns:
        格式化的行列表
    """
    lines = []
    lines.append("")
    lines.append("📋 文件状态:")
    
    if has_report:
        lines.append(f"  ✅ report.db 存在 (共 {total_records} 条记录)")
        
        if rating_counts:
            lines.append("")
            lines.append("📊 评分分布:")
            for rating in sorted(rating_counts.keys(), reverse=True):
                count = rating_counts[rating]
                if rating >= 0:
                    stars = "⭐" * int(rating) if rating > 0 else "0星"
                else:
                    stars = "❌"
                lines.append(f"     {stars}: {count} 张")
        
        if flying_count > 0:
            lines.append("")
            lines.append(f"🦅 飞鸟照片: {flying_count} 张")
    else:
        lines.append("  ❌ report.db 不存在")
    
    if has_manifest:
        lines.append("  ✅ manifest 文件存在 (可重置)")
    else:
        lines.append("  ℹ️  manifest 文件不存在")
    
    if folder_counts:
        lines.append("")
        lines.append("📂 分类文件夹:")
        for folder, count in folder_counts.items():
            lines.append(f"     {folder}/: {count} 张")
    
    return lines


def print_summary(lines: List[str], log_func: Optional[Callable[[str], None]] = None):
    """
    输出摘要（可用于 CLI 和 GUI）
    
    Args:
        lines: 格式化的行列表
        log_func: 日志输出函数，如果为 None 则使用 print
    """
    output = log_func if log_func else print
    for line in lines:
        output(line)
