#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TOPIQ vs CLIPIQA 美学评分对比脚本

用法:
    python compare_iqa.py /path/to/photo/directory
    python compare_iqa.py /path/to/photo/directory --limit 20
    python compare_iqa.py /path/to/photo/directory --csv result.csv

功能:
    - 遍历目录中的所有 RAW / HEIF / JPG 文件
    - 分别用 TOPIQ（本地独立模型）和 CLIPIQA（pyiqa 库）打分
    - 输出并排对比表格 + 统计摘要
"""

import os
import sys
import time
import argparse
import io
from typing import Optional

import torch
import numpy as np
from PIL import Image
import torchvision.transforms as T

# ─── 支持的文件格式 ───────────────────────────────────────────
RAW_EXTENSIONS = {
    '.nef', '.cr2', '.cr3', '.arw', '.orf', '.raf',
    '.rw2', '.dng', '.pef', '.srw', '.x3f',
}
HEIF_EXTENSIONS = {'.hif', '.heif', '.heic'}
JPG_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}
ALL_EXTENSIONS = RAW_EXTENSIONS | HEIF_EXTENSIONS | JPG_EXTENSIONS

# ─── RAW 图片加载（多级回退） ─────────────────────────────────
def _load_raw_image(filepath: str) -> Optional[Image.Image]:
    """
    加载 RAW 文件，三级回退策略:
    1. rawpy 提取嵌入式 JPEG 缩略图（最快）
    2. ExifTool 提取 JpgFromRaw（兼容性最好）
    3. rawpy 完整解码（最慢，作为最后手段）
    """
    basename = os.path.basename(filepath)

    # 方法 1: rawpy 提取缩略图
    try:
        import rawpy
        with rawpy.imread(filepath) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return Image.open(io.BytesIO(thumb.data)).convert('RGB')
            elif thumb.format == rawpy.ThumbFormat.BITMAP:
                return Image.fromarray(thumb.data).convert('RGB')
    except Exception:
        pass  # 静默失败，尝试下一个方法

    # 方法 2: ExifTool 提取 JpgFromRaw
    try:
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_path = tmp.name
        result = subprocess.run(
            ['exiftool', '-b', '-JpgFromRaw', '-w!', tmp_path, filepath],
            capture_output=True, timeout=10,
        )
        # exiftool -b -JpgFromRaw 直接输出到 stdout 更可靠
        result2 = subprocess.run(
            ['exiftool', '-b', '-JpgFromRaw', filepath],
            capture_output=True, timeout=10,
        )
        if result2.stdout and len(result2.stdout) > 1000:
            img = Image.open(io.BytesIO(result2.stdout)).convert('RGB')
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return img
        # 也试试 PreviewImage
        result3 = subprocess.run(
            ['exiftool', '-b', '-PreviewImage', filepath],
            capture_output=True, timeout=10,
        )
        if result3.stdout and len(result3.stdout) > 1000:
            img = Image.open(io.BytesIO(result3.stdout)).convert('RGB')
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return img
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    except Exception:
        pass

    # 方法 3: rawpy 完整解码（慢）
    try:
        import rawpy
        with rawpy.imread(filepath) as raw:
            rgb = raw.postprocess()
            return Image.fromarray(rgb).convert('RGB')
    except Exception:
        pass

    return None


# ─── 图片加载工具 ─────────────────────────────────────────────
def load_image_as_pil(filepath: str) -> Optional[Image.Image]:
    """
    将 RAW / HEIF / JPG 统一加载为 PIL RGB Image。
    RAW: 通过 rawpy 提取嵌入式 JPEG 缩略图（速度极快）
    HEIF: 通过 pillow-heif
    JPG/PNG: 直接 PIL.Image.open
    """
    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext in RAW_EXTENSIONS:
            img = _load_raw_image(filepath)
            if img is not None:
                return img
            # 所有方法都失败了
            print(f"  ⚠️  所有 RAW 解码方法均失败 [{os.path.basename(filepath)}]")
            return None

        elif ext in HEIF_EXTENSIONS:
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
            return Image.open(filepath).convert('RGB')

        else:
            return Image.open(filepath).convert('RGB')

    except Exception as e:
        print(f"  ⚠️  加载失败 [{os.path.basename(filepath)}]: {e}")
        return None


# ─── TOPIQ 评分器 ─────────────────────────────────────────────
class TopiqScorer:
    """复用项目已有的 TOPIQ 独立模型"""

    def __init__(self, device: torch.device):
        self.device = device
        self._model = None
        self._transform = T.ToTensor()

    def _load(self):
        if self._model is not None:
            return
        from topiq_model import CFANet, load_topiq_weights, get_topiq_weight_path

        weight_path = get_topiq_weight_path()
        self._model = CFANet()
        load_topiq_weights(self._model, weight_path, self.device)
        self._model.to(self.device)
        if self.device.type in ('mps', 'cuda'):
            self._model = self._model.half()
            self._use_fp16 = True
        else:
            self._use_fp16 = False
        self._model.eval()

    def score(self, img: Image.Image) -> float:
        self._load()
        img_resized = img.resize((384, 384), Image.LANCZOS)
        tensor = self._transform(img_resized).unsqueeze(0).to(self.device)
        if self._use_fp16:
            tensor = tensor.half()
        with torch.inference_mode():
            s = self._model(tensor, return_mos=True)
        s = s.item() if isinstance(s, torch.Tensor) else float(s)
        return max(1.0, min(10.0, s))


# ─── CLIPIQA 评分器 ───────────────────────────────────────────
class ClipiqaScorer:
    """通过 pyiqa 库调用 CLIPIQA 模型"""

    def __init__(self, device: torch.device):
        self.device = device
        self._metric = None

    def _load(self):
        if self._metric is not None:
            return
        import pyiqa
        # clipiqa+ 使用 RN50 backbone，权重小、速度快
        self._metric = pyiqa.create_metric('clipiqa+', device=self.device)

    def score(self, img: Image.Image) -> float:
        self._load()
        # pyiqa 需要 tensor [0, 1] 范围，(B, C, H, W)
        img_resized = img.resize((384, 384), Image.LANCZOS)
        tensor = T.ToTensor()(img_resized).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            s = self._metric(tensor)
        s = s.item() if isinstance(s, torch.Tensor) else float(s)
        # CLIPIQA 输出范围 [0, 1]，映射到 [1, 10] 以便对比
        return round(1.0 + s * 9.0, 4)


# ─── 主流程 ──────────────────────────────────────────────────
def collect_files(directory: str, limit: int = 0) -> list:
    """递归收集所有支持的图片文件"""
    files = []
    for root, _dirs, filenames in os.walk(directory):
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in ALL_EXTENSIONS:
                files.append(os.path.join(root, fn))
    files.sort()
    if limit > 0:
        files = files[:limit]
    return files


def run_comparison(directory: str, limit: int = 0, csv_path: str = None):
    from config import get_best_device
    device = get_best_device()
    print(f"🖥️  设备: {device}")

    # ── 收集文件 ──
    files = collect_files(directory, limit)
    if not files:
        print("❌ 未找到支持的图片文件")
        return

    print(f"📂 目录: {directory}")
    print(f"📸 共 {len(files)} 张图片\n")

    # ── 初始化两个评分器 ──
    print("🔧 加载 TOPIQ 模型...")
    topiq = TopiqScorer(device)
    topiq._load()
    print("✅ TOPIQ 就绪\n")

    print("🔧 加载 CLIPIQA+ 模型 (首次需下载权重)...")
    clipiqa = ClipiqaScorer(device)
    clipiqa._load()
    print("✅ CLIPIQA+ 就绪\n")

    # ── 逐张评分 ──
    header = f"{'#':>3}  {'文件名':<35}  {'TOPIQ':>7}  {'CLIPIQA+':>9}  {'差值':>6}  {'耗时':>8}"
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)

    results = []
    topiq_times = []
    clipiqa_times = []

    for idx, filepath in enumerate(files, 1):
        basename = os.path.basename(filepath)
        display_name = basename[:33] + ".." if len(basename) > 35 else basename

        # 加载图片（一次加载，两个模型共用）
        img = load_image_as_pil(filepath)
        if img is None:
            print(f"{idx:3d}  {display_name:<35}  {'FAIL':>7}  {'FAIL':>9}  {'---':>6}  {'---':>8}")
            continue

        # TOPIQ 评分
        t0 = time.perf_counter()
        score_topiq = topiq.score(img)
        t_topiq = time.perf_counter() - t0
        topiq_times.append(t_topiq)

        # CLIPIQA 评分
        t0 = time.perf_counter()
        score_clipiqa = clipiqa.score(img)
        t_clipiqa = time.perf_counter() - t0
        clipiqa_times.append(t_clipiqa)

        diff = score_clipiqa - score_topiq
        total_ms = (t_topiq + t_clipiqa) * 1000

        print(f"{idx:3d}  {display_name:<35}  {score_topiq:7.2f}  {score_clipiqa:9.2f}  {diff:+6.2f}  {total_ms:6.0f}ms")

        results.append({
            'filename': basename,
            'topiq': score_topiq,
            'clipiqa': score_clipiqa,
            'diff': diff,
        })

    # ── 统计摘要 ──
    if not results:
        print("\n❌ 没有成功评分的图片")
        return

    print(sep)
    topiq_scores = [r['topiq'] for r in results]
    clipiqa_scores = [r['clipiqa'] for r in results]
    diffs = [r['diff'] for r in results]

    print(f"\n{'═' * 60}")
    print("  📊 统计摘要")
    print(f"{'═' * 60}")
    print(f"  共评分: {len(results)} 张图片\n")

    print(f"  {'指标':<16}  {'TOPIQ':>9}  {'CLIPIQA+':>9}")
    print(f"  {'─' * 40}")
    print(f"  {'平均分':<16}  {np.mean(topiq_scores):9.2f}  {np.mean(clipiqa_scores):9.2f}")
    print(f"  {'中位数':<16}  {np.median(topiq_scores):9.2f}  {np.median(clipiqa_scores):9.2f}")
    print(f"  {'标准差':<16}  {np.std(topiq_scores):9.2f}  {np.std(clipiqa_scores):9.2f}")
    print(f"  {'最高分':<16}  {max(topiq_scores):9.2f}  {max(clipiqa_scores):9.2f}")
    print(f"  {'最低分':<16}  {min(topiq_scores):9.2f}  {min(clipiqa_scores):9.2f}")
    print(f"  {'─' * 40}")
    print(f"  {'平均差值':<16}  {np.mean(diffs):+9.2f}")
    print(f"  {'平均耗时/张':<14}  {np.mean(topiq_times)*1000:7.0f}ms  {np.mean(clipiqa_times)*1000:7.0f}ms")

    # Pearson 相关系数
    if len(results) >= 3:
        corr = np.corrcoef(topiq_scores, clipiqa_scores)[0, 1]
        print(f"\n  📈 Pearson 相关系数: {corr:.4f}")
        if corr > 0.8:
            print("     → 两者高度一致")
        elif corr > 0.5:
            print("     → 两者中等相关，存在明显偏好差异")
        else:
            print("     → 两者评价逻辑差异显著")

    # CLIPIQA 比 TOPIQ 排序差异最大的 Top-5
    if len(results) >= 5:
        sorted_topiq = sorted(results, key=lambda r: r['topiq'], reverse=True)
        sorted_clipiqa = sorted(results, key=lambda r: r['clipiqa'], reverse=True)

        topiq_rank = {r['filename']: i for i, r in enumerate(sorted_topiq, 1)}
        clipiqa_rank = {r['filename']: i for i, r in enumerate(sorted_clipiqa, 1)}

        rank_changes = []
        for r in results:
            fn = r['filename']
            change = topiq_rank[fn] - clipiqa_rank[fn]
            rank_changes.append((fn, topiq_rank[fn], clipiqa_rank[fn], change, r['topiq'], r['clipiqa']))

        # 排名提升最大（CLIPIQA 更喜欢）
        rank_changes.sort(key=lambda x: x[3], reverse=True)
        print(f"\n  🔺 CLIPIQA 比 TOPIQ 高估最多 (排名提升 Top-5):")
        for fn, tr, cr, ch, ts, cs in rank_changes[:5]:
            fn_short = fn[:28] + ".." if len(fn) > 30 else fn
            print(f"     {fn_short:<30}  TOPIQ #{tr:2d}({ts:.1f})  →  CLIP #{cr:2d}({cs:.1f})  ↑{ch}")

        # 排名下降最大（TOPIQ 更喜欢）
        rank_changes.sort(key=lambda x: x[3])
        print(f"\n  🔻 TOPIQ 比 CLIPIQA 高估最多 (排名下降 Top-5):")
        for fn, tr, cr, ch, ts, cs in rank_changes[:5]:
            fn_short = fn[:28] + ".." if len(fn) > 30 else fn
            print(f"     {fn_short:<30}  TOPIQ #{tr:2d}({ts:.1f})  →  CLIP #{cr:2d}({cs:.1f})  ↓{abs(ch)}")

    print(f"\n{'═' * 60}")

    # ── CSV 输出 ──
    if csv_path:
        import csv
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['filename', 'topiq', 'clipiqa', 'diff'])
            writer.writeheader()
            writer.writerows(results)
        print(f"\n💾 结果已保存到: {csv_path}")


# ─── CLI 入口 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="TOPIQ vs CLIPIQA+ 美学评分对比工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python compare_iqa.py ~/Desktop/Test-慧眼选鸟
  python compare_iqa.py ~/Desktop/Test-慧眼选鸟 --limit 10
  python compare_iqa.py ~/Desktop/Test-慧眼选鸟 --csv result.csv
        """,
    )
    parser.add_argument('directory', help='图片目录路径')
    parser.add_argument('--limit', type=int, default=0,
                        help='限制处理图片数量 (0=全部)')
    parser.add_argument('--csv', type=str, default=None,
                        help='输出 CSV 文件路径')

    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"❌ 目录不存在: {args.directory}")
        return 1

    print("=" * 60)
    print("  🔬 TOPIQ vs CLIPIQA+  美学评分对比")
    print("=" * 60)
    print()

    run_comparison(args.directory, limit=args.limit, csv_path=args.csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
