#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无鸟补救扫描全链路验证：对 39 张已确认有鸟的漏检 ARW 样本，
经真实 detect_and_draw_birds 验证救回率与开关行为。

预期（依据 2026-07-14 A/B 实测）:
- rescue_scan_enabled=True  → 检出(含救回) >= 30/39
- rescue_scan_enabled=False → 与旧版行为一致，检出 <= 5/39

End-to-end validation of the rescue scan on 39 confirmed missed-bird ARW
samples through the real detect_and_draw_birds; asserts rescue rate and
toggle behavior.
"""
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = "/Users/jameszhenyu/Desktop/零星"
UI_SETTINGS = [50, 300, 5.0, False]  # 置信度50/锐度/美学/不存裁切


def extract_jpeg(arw: str, out_dir: str) -> str:
    """exiftool 抽内嵌 JPEG（与生产 -JpgFromRaw 链路一致）。"""
    jpg = os.path.join(out_dir, os.path.splitext(os.path.basename(arw))[0] + ".jpg")
    for tag in ("-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"):
        with open(jpg, "wb") as f:
            subprocess.run(["exiftool", tag, "-b", arw], stdout=f,
                           stderr=subprocess.DEVNULL)
        if os.path.getsize(jpg) > 10000:
            return jpg
    raise RuntimeError(f"no embedded jpeg: {arw}")


def main() -> None:
    if not os.path.isdir(SAMPLE_DIR):
        sys.exit(f"样本目录不存在: {SAMPLE_DIR}")

    from advanced_config import get_advanced_config
    from ai_model import load_yolo_model, detect_and_draw_birds

    cfg = get_advanced_config()
    saved = cfg.rescue_scan_enabled
    model = load_yolo_model()
    arws = sorted(os.path.join(SAMPLE_DIR, f) for f in os.listdir(SAMPLE_DIR)
                  if f.lower().endswith(".arw"))
    print(f"samples: {len(arws)}")

    with tempfile.TemporaryDirectory() as tmp:
        jpgs = [extract_jpeg(a, tmp) for a in arws]
        for enabled, expect in ((False, "<=5"), (True, ">=30")):
            cfg.set_rescue_scan_enabled(enabled)
            hits = rescued_n = 0
            for jpg in jpgs:
                r = detect_and_draw_birds(jpg, model, None, tmp, UI_SETTINGS, None)
                # 与生产二次门槛同口径:救回豁免,否则须过 UI 阈值
                # Same gate as production: rescued exempt, else UI threshold.
                ok = r[0] and (r[9] or r[2] >= UI_SETTINGS[0] / 100.0)
                hits += int(ok)
                rescued_n += int(bool(r[9]))
            print(f"rescue_enabled={enabled}: 检出 {hits}/{len(jpgs)} "
                  f"(其中救回 {rescued_n}) 预期 {expect}")
            if enabled:
                assert hits >= 30, f"救回率不达标: {hits}/39"
            else:
                assert hits <= 5, f"关闭开关行为异常: {hits}/39"

    cfg.set_rescue_scan_enabled(saved)
    print("VALIDATION PASSED")


if __name__ == "__main__":
    main()
