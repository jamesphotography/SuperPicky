#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TOPIQ-IAA 美学模型诊断脚本 / TOPIQ-IAA aesthetic model diagnostic.

用途 / Purpose:
    在一个已处理过的照片目录上,对同一批鸟裁剪区,对比 App 现用的
    TOPIQ-IAA res50 (cfanet_iaa_ava_res50) 与 pyiqa 的 Swin 变体
    (cfanet_iaa_ava_swin) 的美学打分——分布 + 秩相关(Spearman)。

    On a processed photo directory, compare the app's current TOPIQ-IAA
    res50 against pyiqa's Swin variant on the same bird crops: score
    distributions + Spearman rank correlation.

背景 / Background:
    2026-07-18 评估「是否把 TOPIQ-IAA 迁移训练到 TAD66K」时写的诊断工具。
    结论见 docs/specs/2026-07-18-topiq-tad66k-transfer-training-analysis.md
    (放弃 TAD66K)。保留此脚本供未来重新评估时一键复现基线。
    Written while evaluating whether to fine-tune TOPIQ-IAA on TAD66K
    (2026-07-18, decided against — see the analysis doc). Kept so a future
    re-evaluation can reproduce the baseline in one command.

用法 / Usage:
    .venv/bin/python scripts_dev/aesthetic_topiq_diagnostic.py <已处理目录>

    目录须已被 SuperPicky 处理过(存在 .superpicky/report.db)。
    Directory must have been processed by SuperPicky (.superpicky/report.db).

依赖 / Deps: pyiqa (Swin 变体权重首次运行会自动下载)。
"""
import os
import sys
import sqlite3
import csv
import statistics

os.environ.setdefault('YOLO_VERBOSE', 'False')
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import cv2  # noqa: E402
import torch  # noqa: E402
from ai_model import load_yolo_model, detect_and_draw_birds, read_image_bgr  # noqa: E402
from iqa_scorer import get_iqa_scorer  # noqa: E402
import pyiqa  # noqa: E402

UI_SETTINGS = [50, 300, 4.5, False, 'none']  # [conf, sharp, nima, save_crop, norm]


def crop_bird(preview_path: str, yolo, work_dir: str):
    """复刻 App 的鸟裁剪:YOLO 检测→bbox 缩放回原图→15% pad→裁 BGR。"""
    orig = read_image_bgr(preview_path)
    if orig is None:
        return None
    res = detect_and_draw_birds(preview_path, yolo, None, work_dir,
                                UI_SETTINGS, None, skip_nima=True)
    if not res or not res[0] or res[5] is None or res[6] is None:
        return None
    bbox, dims = res[5], res[6]
    ho, wo = orig.shape[:2]
    wr, hr = dims
    sx, sy = wo / wr, ho / hr
    x, y, w, h = int(bbox[0] * sx), int(bbox[1] * sy), int(bbox[2] * sx), int(bbox[3] * sy)
    pad = int(max(w, h) * 0.15)
    crop = orig[max(0, y - pad):min(ho, y + h + pad),
                max(0, x - pad):min(wo, x + w + pad)]
    return crop if crop.size > 0 else None


def spearman(a, b) -> float:
    """手算 Spearman 秩相关,避免引入 scipy。"""
    def rank(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        rk = [0] * len(x)
        for pos, idx in enumerate(order):
            rk[idx] = pos
        return rk
    ra, rb = rank(a), rank(b)
    n = len(a)
    d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


def describe(vals, name: str) -> None:
    s = sorted(vals)
    pct = lambda q: s[int(q * (len(s) - 1))]
    print(f"{name}: min={min(vals):.2f} max={max(vals):.2f} "
          f"mean={statistics.mean(vals):.2f} std={statistics.stdev(vals):.2f} "
          f"range={max(vals) - min(vals):.2f} "
          f"p10={pct(.1):.2f} p50={pct(.5):.2f} p90={pct(.9):.2f}")


def main(work_dir: str) -> None:
    db_path = os.path.join(work_dir, '.superpicky', 'report.db')
    if not os.path.exists(db_path):
        raise SystemExit(f"未找到 report.db,目录须先被 SuperPicky 处理过: {db_path}")

    dev = 'mps' if torch.backends.mps.is_available() else (
        'cuda' if torch.cuda.is_available() else 'cpu')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    birds = [dict(r) for r in conn.execute(
        "SELECT filename, has_bird, temp_jpeg_path, rating FROM photos")
        if str(r['has_bird']).lower() in ('yes', '1') and r['temp_jpeg_path']]
    print(f"有鸟待测: {len(birds)}  设备: {dev}")

    yolo = load_yolo_model()
    res50 = get_iqa_scorer(device=dev)                    # App 现用 res50
    swin = pyiqa.create_metric('topiq_iaa', device=dev)   # Swin AVA 变体

    def swin_score(crop_bgr) -> float:
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        t = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        with torch.inference_mode():
            return float(swin(t.to(dev)).item())

    recs = []
    for i, r in enumerate(birds, 1):
        p = os.path.join(work_dir, r['temp_jpeg_path'])
        if not os.path.exists(p):
            p = os.path.join(work_dir, r['filename'] + '.jpg')
        if not os.path.exists(p):
            continue
        crop = crop_bird(p, yolo, work_dir)
        if crop is None:
            continue
        try:
            s_res = res50.calculate_from_array(crop)
            s_swin = swin_score(crop)
        except Exception:
            continue
        if s_res is None:
            continue
        recs.append({'file': r['filename'], 'rating': r['rating'],
                     'res50': round(s_res, 3), 'swin': round(s_swin, 3)})
        if i % 50 == 0:
            print(f"  ...{i}/{len(birds)}")

    if not recs:
        raise SystemExit("无成功打分样本")

    print(f"\n成功打分: {len(recs)} 张")
    res_v = [x['res50'] for x in recs]
    swin_v = [x['swin'] for x in recs]
    describe(res_v, "res50(App现用)")
    describe(swin_v, "Swin变体   ")
    print(f"\nres50 vs Swin 秩相关(Spearman rho) = {spearman(res_v, swin_v):.3f}")

    out_csv = os.path.join(work_dir, '.superpicky', 'aesthetic_diag.csv')
    with open(out_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['file', 'rating', 'res50', 'swin'])
        w.writeheader()
        w.writerows(recs)
    print(f"明细CSV: {out_csv}")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit("用法: python scripts_dev/aesthetic_topiq_diagnostic.py <已处理目录>")
    main(sys.argv[1])
