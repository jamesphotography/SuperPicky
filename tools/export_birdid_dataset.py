#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出 BirdID 训练/评测数据集（内部工具）。

用**生产识别算法**把原图裁成「鸟主体方形裁剪图」，并产出标准标签 CSV，供外部
迁移训练库（superpicky_RL）直接消费——RL 侧不再需要复刻 YOLO 裁剪 / 标签解析，
从源头消除 train/serve skew。

裁剪链路与生产 `birdid.bird_identifier` 完全一致：
    load_image（RAW→rawpy+EXIF 定向 / JPEG→EXIF 定向 / HEIF）
    → max(W,H)<=640 走整图（is_yolo_cropped=0）
    → 否则 detect_and_crop_bird(BGR + imgsz=1024 + 对焦点选框) 方形裁剪（=1）
    → 无鸟则跳过（与生产「不出结果」一致）。

标签解析口径与生产/RL 一致：model_class_id > 中文名 > 英文名 > ebird(仅唯一码)；
解析不出（空码/占位码歧义/查无此种）跳过并记录，绝不静默错配。

Export a BirdID training/eval dataset using the *production* recognition pipeline:
crop each image to the bird subject (same code path as serve) and emit a standard
label CSV so the external transfer-training repo consumes pre-cropped data directly,
eliminating any train/serve skew.

用法 / Usage:
    .venv/bin/python tools/export_birdid_dataset.py \\
        --input  /path/to/源目录            # 含 labels.csv + 图片(JPG/RAW/HEIF)
        --output /path/to/导出目录
        [--label-csv labels.csv] [--prefer raw|jpeg]
        [--jpeg-quality 97] [--conf 0.25] [--padding 0.15]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 确保能 import 生产模块（本脚本在 tools/ 下，项目根是上一级）。
# Ensure the project root is importable (this script lives in tools/).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 与生产 predict_bird 对齐的「目标模型契约」常量，仅写入 export_meta.json 供 RL 侧校验。
# 这些是模型/训练侧属性，导出图不烘焙它们（不预 resize），此处仅作溯源记录。
# Target-model contract constants (recorded in meta only; not baked into crops).
_TARGET_NUM_CLASSES = 10964
_TARGET_IMAGE_SIZE = 224
_TARGET_TEMPERATURE = 0.9
_YOLO_IMGSZ = 1024  # detect_and_crop_bird 内部固定值（对齐生产 TARGET_IMAGE_SIZE）

# 输入图片按 stem 查找时的扩展名优先级。RAW 优先 → 可读相机对焦点做选框（更准）。
# Extension priority when locating an image by stem. RAW first so the focus point
# can drive subject selection (more accurate on cluttered scenes).
_RAW_EXTS = [
    ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".srf", ".dng", ".raf", ".orf",
    ".rw2", ".pef", ".srw", ".raw", ".rwl", ".3fr", ".fff", ".erf", ".mef",
    ".mos", ".mrw", ".x3f",
]
_HEIF_EXTS = [".heic", ".heif", ".hif"]
_PLAIN_EXTS = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"]
_EXT_PRIORITY_RAW = _RAW_EXTS + _HEIF_EXTS + _PLAIN_EXTS
_EXT_PRIORITY_JPEG = _PLAIN_EXTS + _HEIF_EXTS + _RAW_EXTS
_ALL_IMAGE_EXTS = set(_RAW_EXTS + _HEIF_EXTS + _PLAIN_EXTS)


class LabelResolver:
    """
    名字 → model_class_id 反查器（生产 DB 同表 BirdCountInfo）。

    口径与 RL `load_class_mapping` / `resolve_class_index` 一致：名字→索引的反查
    **只保留唯一对应一个 model_class_id 的键**，占位/重复 ebird 码（如 ostric2）
    与空码被剔除，避免 lossy 反转把多个物种塌缩到一个索引（即「部分鸟种 0%」根因）。

    Name → model_class_id resolver over the production DB (BirdCountInfo), matching
    the RL repo's unique-only reverse maps (placeholder/duplicate ebird codes dropped).
    """

    def __init__(self, db_path: str, table: str = "BirdCountInfo") -> None:
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库文件不存在 / DB not found: {db_path}")
        self.db_path = db_path
        self.idx_to_names: Dict[int, Tuple[str, str, str]] = {}
        zh_rows: Dict[str, List[int]] = {}
        en_rows: Dict[str, List[int]] = {}
        eb_rows: Dict[str, List[int]] = {}

        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT model_class_id, ebird_code, chinese_simplified, english_name "
                f"FROM {table} WHERE model_class_id IS NOT NULL"
            )
            for mcid, eb, zh, en in cur.fetchall():
                mcid = int(mcid)
                eb = (eb or "").strip()
                zh = (zh or "").strip()
                en = (en or "").strip()
                self.idx_to_names[mcid] = (zh, en, eb)
                if zh:
                    zh_rows.setdefault(zh, []).append(mcid)
                if en:
                    en_rows.setdefault(en, []).append(mcid)
                if eb:
                    eb_rows.setdefault(eb, []).append(mcid)

        uniq = lambda d: {k: v[0] for k, v in d.items() if len(v) == 1}
        self.zh_to_idx = uniq(zh_rows)
        self.en_to_idx = uniq(en_rows)
        self.ebird_to_idx = uniq(eb_rows)
        self.ambiguous_ebird = {k: v for k, v in eb_rows.items() if len(v) > 1}

    def resolve(
        self,
        *,
        model_class_id: Optional[str] = None,
        chinese: Optional[str] = None,
        english: Optional[str] = None,
        ebird: Optional[str] = None,
    ) -> Tuple[Optional[int], str]:
        """
        解析一条标注 → (model_class_id 或 None, 失败原因)。优先级与生产一致。

        Resolve one label → (model_class_id or None, reason-if-failed).
        """
        if model_class_id is not None and str(model_class_id).strip() != "":
            try:
                return int(str(model_class_id).strip()), ""
            except (ValueError, TypeError):
                pass
        if chinese:
            i = self.zh_to_idx.get(chinese.strip())
            if i is not None:
                return i, ""
        if english:
            i = self.en_to_idx.get(english.strip())
            if i is not None:
                return i, ""
        if ebird:
            eb = ebird.strip()
            i = self.ebird_to_idx.get(eb)
            if i is not None:
                return i, ""
            if eb in self.ambiguous_ebird:
                n = len(self.ambiguous_ebird[eb])
                return None, (
                    f"ebird '{eb}' 对应 {n} 个物种(占位/重复码)，无法唯一确定；"
                    f"请在 CSV 用 model_class_id 或中文名打标"
                )
        return None, "标签无法解析（空码/查无此种）"

    def names(self, idx: int) -> Tuple[str, str, str]:
        """返回 (中文, 英文, ebird_code)。Return (zh, en, ebird) for an index."""
        return self.idx_to_names.get(idx, ("", "", ""))


def pick_label_columns(headers: List[str]) -> Dict[str, Optional[str]]:
    """
    按表头(大小写/空格无关)识别标注列，复刻 RL `_pick_label_columns`。
    filename 缺省退回第一列。

    Identify label columns by normalized header (mirrors the RL repo). filename
    falls back to the first column.
    """
    norm = {str(c).strip().lower(): c for c in headers}

    def find(*cands: str) -> Optional[str]:
        for cand in cands:
            if cand in norm:
                return norm[cand]
        return None

    return {
        "filename": find("file name", "filename", "file", "name") or headers[0],
        "model_class_id": find("model_class_id", "class_id", "mcid"),
        "chinese": find("chinese_simplified", "chinese", "cn_name", "中文名", "中文"),
        "english": find("bird species", "species", "english_name", "english name", "english"),
        "ebird": find("ebird code", "ebird_code", "ebird_id", "ebird"),
    }


def build_stem_index(input_dir: str) -> Dict[str, List[str]]:
    """
    递归扫描 input_dir，建立 {stem(小写): [图片路径,...]}。
    支持现有「子目录 + CSV」布局（按 stem 找图，无需子目录标签模式）。

    Recursively index images by lowercased stem so CSV-mode also works with the
    existing "species subdir + CSV" layout.
    """
    index: Dict[str, List[str]] = {}
    for root, _dirs, files in os.walk(input_dir):
        for fn in files:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in _ALL_IMAGE_EXTS:
                stem = os.path.splitext(fn)[0].lower()
                index.setdefault(stem, []).append(os.path.join(root, fn))
    return index


def choose_source(paths: List[str], prefer: str) -> Optional[str]:
    """按扩展名优先级从同 stem 的候选里挑一个源文件。"""
    priority = _EXT_PRIORITY_RAW if prefer == "raw" else _EXT_PRIORITY_JPEG
    rank = {ext: i for i, ext in enumerate(priority)}
    return min(
        paths,
        key=lambda p: rank.get(os.path.splitext(p)[1].lower(), len(priority)),
        default=None,
    )


def git_commit(repo_root: str) -> str:
    """读取当前 git commit（溯源用），失败返回 'unknown'。"""
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="export_birdid_dataset",
        description="用生产算法导出 BirdID 训练/评测裁剪数据集 + 标准标签 CSV（内部工具）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", "-i", required=True, help="源目录（含 labels.csv + 图片）")
    parser.add_argument("--output", "-o", required=True, help="导出目录")
    parser.add_argument("--label-csv", default="labels.csv",
                        help="源目录内的标签 CSV 文件名（默认 labels.csv）")
    parser.add_argument("--prefer", choices=["raw", "jpeg"], default="raw",
                        help="同名多格式时优先用哪种源：raw(默认,可用对焦点) / jpeg")
    parser.add_argument("--jpeg-quality", type=int, default=97,
                        help="导出 JPEG 质量（默认 97）")
    parser.add_argument("--conf", type=float, default=0.25,
                        help="YOLO 置信度阈值（默认 0.25，对齐生产）")
    parser.add_argument("--padding", type=float, default=0.15,
                        help="裁剪框每边 padding 比例（默认 0.15，对齐生产）")
    parser.add_argument("--db", default=None,
                        help="bird_reference.sqlite 路径（默认用 SuperPicky 内置 DB）")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    if not os.path.isdir(input_dir):
        print(f"❌ 源目录不存在: {input_dir}")
        return 1

    csv_path = os.path.join(input_dir, args.label_csv)
    if not os.path.isfile(csv_path):
        print(f"❌ 标签 CSV 不存在: {csv_path}")
        return 1

    # —— 延迟导入生产模块（YOLO/torch 较重）——
    from birdid.bird_identifier import (
        load_image, get_yolo_detector, _read_focus_point_for_path,
        YOLO_AVAILABLE, YOLO_MODEL_PATH,
    )

    if not YOLO_AVAILABLE:
        print("❌ YOLO 不可用，无法裁剪。请检查 ultralytics 安装与模型文件。")
        return 1

    db_path = args.db or os.path.join(
        _PROJECT_ROOT, "birdid", "data", "bird_reference.sqlite"
    )
    resolver = LabelResolver(db_path)
    detector = get_yolo_detector()
    if detector is None:
        print("❌ YOLO 检测器加载失败。")
        return 1

    images_out = os.path.join(output_dir, "images")
    os.makedirs(images_out, exist_ok=True)

    # 读 CSV
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        print("❌ 标签 CSV 为空。")
        return 1
    headers, data_rows = rows[0], rows[1:]
    col = pick_label_columns(headers)
    col_idx = {k: (headers.index(v) if v in headers else None) for k, v in col.items()}

    def cell(row: List[str], key: str) -> Optional[str]:
        i = col_idx.get(key)
        if i is None or i >= len(row):
            return None
        v = row[i].strip()
        return v or None

    stem_index = build_stem_index(input_dir)

    label_records: List[Dict[str, object]] = []
    skipped: List[Tuple[str, str]] = []
    seen_out_stems: Dict[str, int] = {}
    counts = {"total": len(data_rows), "exported": 0, "cropped": 0,
              "whole": 0, "no_bird": 0, "no_image": 0, "no_label": 0}

    t0 = time.time()
    for n, row in enumerate(data_rows, 1):
        if not row or all(not c.strip() for c in row):
            continue
        stem_raw = cell(row, "filename")
        if not stem_raw:
            skipped.append((f"(row {n})", "filename 为空"))
            continue
        stem = os.path.splitext(stem_raw)[0]  # 容忍 CSV 里带扩展名

        # 定位源图（按 stem 递归查找 + 扩展名优先级）
        candidates = stem_index.get(stem.lower())
        if not candidates:
            skipped.append((stem, "未找到对应图片文件"))
            counts["no_image"] += 1
            continue
        src = choose_source(candidates, args.prefer)

        # 解析标签
        label, reason = resolver.resolve(
            model_class_id=cell(row, "model_class_id"),
            chinese=cell(row, "chinese"),
            english=cell(row, "english"),
            ebird=cell(row, "ebird"),
        )
        if label is None:
            skipped.append((src, reason))
            counts["no_label"] += 1
            continue

        # —— 生产裁剪链路 ——
        try:
            image = load_image(src)
        except Exception as e:
            skipped.append((src, f"load_image 失败: {e}"))
            continue

        w, h = image.size
        if max(w, h) <= 640:
            crop_img = image
            is_cropped = 0
            counts["whole"] += 1
        else:
            focus = _read_focus_point_for_path(src)
            cropped, _info = detector.detect_and_crop_bird(
                image,
                confidence_threshold=args.conf,
                padding_ratio=args.padding,
                focus_point=focus,
            )
            if cropped is None:
                skipped.append((src, "未检测到鸟（生产此时不出结果）"))
                counts["no_bird"] += 1
                continue
            crop_img = cropped
            is_cropped = 1
            counts["cropped"] += 1

        # 输出文件名：保留原 stem；跨子目录撞名时追加后缀。
        out_stem = stem
        if out_stem.lower() in seen_out_stems:
            seen_out_stems[out_stem.lower()] += 1
            out_stem = f"{stem}__{seen_out_stems[out_stem.lower()]}"
        else:
            seen_out_stems[out_stem.lower()] = 0
        out_name = f"{out_stem}.jpg"
        out_path = os.path.join(images_out, out_name)
        try:
            crop_img.convert("RGB").save(
                out_path, "JPEG", quality=args.jpeg_quality, subsampling=0
            )
        except Exception as e:
            skipped.append((src, f"保存失败: {e}"))
            continue

        zh, en, eb = resolver.names(label)
        label_records.append({
            "filename": out_stem,
            "model_class_id": label,
            "is_yolo_cropped": is_cropped,
            "chinese": zh,
            "english": en,
            "ebird_code": eb,
            "source": os.path.relpath(src, input_dir),
        })
        counts["exported"] += 1
        if n % 50 == 0 or n == len(data_rows):
            print(f"  [{n}/{len(data_rows)}] 已导出 {counts['exported']}，"
                  f"跳过 {len(skipped)} ...")

    # —— 写出 labels.csv ——
    labels_csv = os.path.join(output_dir, "labels.csv")
    fields = ["filename", "model_class_id", "is_yolo_cropped",
              "chinese", "english", "ebird_code", "source"]
    with open(labels_csv, "w", encoding="utf-8", newline="") as f:
        w_ = csv.DictWriter(f, fieldnames=fields)
        w_.writeheader()
        w_.writerows(label_records)

    # —— 写出 skipped.csv ——
    skipped_csv = os.path.join(output_dir, "skipped.csv")
    with open(skipped_csv, "w", encoding="utf-8", newline="") as f:
        w_ = csv.writer(f)
        w_.writerow(["source", "reason"])
        w_.writerows(skipped)

    # —— 写出 export_meta.json（溯源 + 目标模型契约）——
    meta = {
        "tool": "tools/export_birdid_dataset.py",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "superpicky_git_commit": git_commit(_PROJECT_ROOT),
        "input_dir": input_dir,
        "output_dir": output_dir,
        "label_csv": args.label_csv,
        "prefer_source": args.prefer,
        "crop_params": {
            "yolo_conf": args.conf,
            "padding_ratio": args.padding,
            "yolo_imgsz": _YOLO_IMGSZ,
            "bird_class_id": 14,
            "yolo_model": os.path.relpath(YOLO_MODEL_PATH, _PROJECT_ROOT),
            "jpeg_quality": args.jpeg_quality,
            "jpeg_subsampling": 0,
        },
        # 目标模型契约：RL 侧据此校验「这批裁剪是为该模型生成的」。
        "target_model_contract": {
            "model": "model20240824",
            "num_classes": _TARGET_NUM_CLASSES,
            "image_size": _TARGET_IMAGE_SIZE,
            "cls_temperature": _TARGET_TEMPERATURE,
            "transform_cropped": "OSEA_TRANSFORM_DIRECT (LANCZOS resize → 224)",
            "transform_whole": "EVAL_TRANSFORM (Resize 256 + CenterCrop 224)",
        },
        "note": (
            "JPEG q97 比生产内存内裁剪多一次 JPEG round-trip，eval logit 会有极小"
            "非零差异；裁剪算法本身仍逐位一致。如需 bit-exact 评测请改存 PNG。"
        ),
        "counts": counts,
        "skipped_count": len(skipped),
    }
    meta_path = os.path.join(output_dir, "export_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # —— 汇总 ——
    dt = time.time() - t0
    print("\n" + "=" * 56)
    print("  导出完成 / Export complete")
    print("=" * 56)
    print(f"  源 CSV 行数      : {counts['total']}")
    print(f"  成功导出         : {counts['exported']}  "
          f"(方形裁剪 {counts['cropped']} / 整图 {counts['whole']})")
    print(f"  跳过             : {len(skipped)}  "
          f"(无鸟 {counts['no_bird']} / 无图 {counts['no_image']} / 无标签 {counts['no_label']})")
    print(f"  物种数           : {len({r['model_class_id'] for r in label_records})}")
    print(f"  耗时             : {dt:.1f}s")
    print(f"\n  📂 {labels_csv}")
    print(f"  📂 {images_out}/")
    print(f"  📄 {skipped_csv}")
    print(f"  📄 {meta_path}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
