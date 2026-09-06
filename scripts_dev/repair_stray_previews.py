#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复被「缓存预览图误搬 + JPG 顶替 RAW」污染的库数据。

对应缺陷（2026-09-05 定位，代码侧已在同一提交修复）：
  1) core/rating_mover 把 temp_jpeg_path 指向的 .superpicky 内部缓存预览当
     「配套 JPEG」搬进鸟种目录（改鸟种与改星级两条路径都有）；
  2) 再处理该目录时，那张 jpg 与 RAW 共用同一个 prefix（DB 主键），
     core/photo_processor 的 organize 阶段用它覆盖了 RAW 的 current_path，
     RAW 从此被永久落在旧目录、从库的视角消失。

本脚本把已经产生的脏数据修回来，四类操作全部幂等：
  A. 删除被误搬到照片目录的临时预览图；
  B. 把落单的 RAW 搬到它本该在的目录（即那张 jpg 所在、用户改鸟种/星级后
     期望的目录），并让 current_path 指向它；
  C. 清空指向已不存在文件的陈旧 temp_jpeg_path；
  D. 把指向不存在文件的 current_path 重新指到该文件的真实位置。

安全边界（宁可少修，不可误删）：
  - 只删「EXIF Software 含 dcraw」的 jpg，即 SuperPicky 自己从 RAW 解出来的
    预览图（可再生）；相机直出的 JPEG 永远没有这个标记。
  - 且只在同目录有同名 RAW 陪着时才删——没有 RAW 陪着的 jpg 可能是该照片
    仅存的图像文件（例如用户在外部工具里改过名）。
  - 同目录并存 RAW+JPEG 的双格式拍摄一律不动。
  - 找不到目标文件时只报告、不猜测。

Repair libraries polluted by "cache preview relocated + JPEG shadowing the
RAW". All four operations are idempotent. Deletion is limited to previews
SuperPicky itself generated via dcraw (identified by the EXIF Software tag)
and only when the matching RAW sits beside them, so a camera JPEG or a
photo's only remaining image file is never touched.

用法 / Usage:
    python scripts_dev/repair_stray_previews.py <目录…>           # 预演
    python scripts_dev/repair_stray_previews.py --apply <目录…>   # 执行
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constants import RAW_EXTENSIONS

# 大小写两种写法都要认：ExFAT/APFS 大小写不敏感，但 DB 里存的是原样文件名
# Match both cases; the DB stores names verbatim.
RAW_EXT: Tuple[str, ...] = tuple(RAW_EXTENSIONS) + tuple(
    e.upper() for e in RAW_EXTENSIONS
)

# plan() 的返回类型：(待删预览图, RAW 归位, temp_jpeg 清空, current_path 找回)
Plan = Tuple[
    List[str],
    List[Tuple[str, str, str]],
    List[Tuple[str, str]],
    List[Tuple[str, str, str]],
]


def _manifest_temp_jpegs(root: str) -> set:
    """
    读 manifest 里由 SuperPicky 生成的临时 JPEG 文件名集合。

    只是辅助判据：manifest 仅记录**当次**处理生成的文件，更早几轮生成的会漏，
    所以真正的把关靠 _is_generated_preview 的 EXIF 判据。

    Read the manifest's generated-JPEG names; only a hint, since it records
    just the latest run — the EXIF check below is the authoritative test.

    参数 / Args:
        root: 照片批次根目录（绝对路径）

    返回 / Returns:
        set: 文件名（不含目录）集合；读不到时为空集
    """
    path = os.path.join(root, ".superpicky_manifest.json")
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    return {os.path.basename(x) for x in data.get("temp_jpegs", []) or []}


def _is_generated_preview(path: str) -> bool:
    """
    判断一个 jpg 是不是 SuperPicky 自己从 RAW 解出来的预览图（可安全删除）。

    判据是 EXIF 的 Software 标记：预览图经 dcraw 解码生成，会写入
    "dcraw v9.26"；相机直出的 JPEG 永远不会有这个标记。读取失败一律返回
    False——判不准就不删。

    True when the JPEG was produced by SuperPicky's dcraw decode (safe to
    delete). Any failure returns False: never delete what we cannot verify.

    参数 / Args:
        path: 待判断的 jpg 绝对路径

    返回 / Returns:
        bool: True 表示可安全删除的生成物
    """
    if not os.path.exists(path):
        return False
    try:
        out = subprocess.run(
            ["exiftool", "-s", "-s", "-s", "-Software", path],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return "dcraw" in out


def _find_file(root: str, name: str) -> str:
    """
    在库内（跳过 .superpicky）按文件名查找，返回相对 root 的路径。

    Locate a file by name inside the library, skipping .superpicky.

    参数 / Args:
        root: 批次根目录
        name: 文件名（含扩展名）

    返回 / Returns:
        str: 相对路径；找不到返回 ""
    """
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".superpicky"]
        if name in files:
            return os.path.relpath(os.path.join(cur, name), root)
    return ""


def plan(root: str) -> Plan:
    """
    算出该目录需要做的修复，不改动任何文件或数据库。

    参数 / Args:
        root: 照片批次根目录（含 .superpicky/report.db）

    返回 / Returns:
        Plan: (待删预览图相对路径,
               [(prefix, RAW 现位置, RAW 应移到)],
               [(prefix, 失效的 temp_jpeg_path)],
               [(prefix, 失效的 current_path, 找回的位置)])
        目录里没有 report.db 时返回四个空列表。
    """
    db_path = os.path.join(root, ".superpicky", "report.db")
    if not os.path.exists(db_path):
        return [], [], [], []

    temp_jpegs = _manifest_temp_jpegs(root)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT filename, current_path, original_path, temp_jpeg_path FROM photos"
        ).fetchall()
    finally:
        con.close()

    to_delete: List[str] = []
    fix_current: List[Tuple[str, str, str]] = []
    clear_jpeg: List[Tuple[str, str]] = []
    relink: List[Tuple[str, str, str]] = []

    for r in rows:
        cur = r["current_path"] or ""
        orig = r["original_path"] or ""
        tj = r["temp_jpeg_path"] or ""

        if cur.lower().endswith((".jpg", ".jpeg")) and orig.lower().endswith(RAW_EXT):
            # B. current_path 指向 jpg 而原图是 RAW —— 同目录也没有那张 RAW，
            #    说明 RAW 被落在了别处，这才是真损坏。
            raw_name = os.path.basename(orig)
            same_dir_raw = os.path.join(
                os.path.dirname(os.path.join(root, cur)), raw_name
            )
            if not os.path.exists(same_dir_raw):
                found = _find_file(root, raw_name)
                if found:
                    # jpg 所在目录 = 用户改鸟种/星级后期望的位置（元数据侧车
                    # 通常也已经在那里），所以把落单的 RAW 搬过去，而不是把
                    # current_path 退回旧目录——退回会让鸟名与目录继续打架。
                    # Move the stranded RAW to where the edit intended it.
                    fix_current.append(
                        (r["filename"], found,
                         os.path.join(os.path.dirname(cur), raw_name))
                    )
                    # RAW 归位后，顶替过它的那张预览图就是多余的
                    # Once the RAW is back, the stand-in preview is redundant.
                    if _is_generated_preview(os.path.join(root, cur)):
                        to_delete.append(cur)
        elif cur and not os.path.exists(os.path.join(root, cur)):
            # D. current_path 指向的文件根本不存在（连拍组改鸟种时 DB 与文件
            #    移动不同步的历史残留）→ 按文件名找回，只改 DB 不动文件。
            # Relink a dead current_path; DB only, no file is touched.
            found = _find_file(root, os.path.basename(cur))
            if found and found != cur:
                relink.append((r["filename"], cur, found))

        if tj and not tj.startswith(".superpicky"):
            abs_tj = os.path.join(root, tj)
            if os.path.exists(abs_tj) and (
                os.path.basename(tj) in temp_jpegs or _is_generated_preview(abs_tj)
            ):
                to_delete.append(tj)          # A. 误搬出来的预览图
            if not os.path.exists(abs_tj):
                clear_jpeg.append((r["filename"], tj))   # C. 陈旧路径

    # A（兜底）：DB 里的 temp_jpeg_path 可能早被清空或改写，光靠 DB 追不全，
    # 再按文件特征全盘扫一遍。只在同名 RAW 就在旁边时才删。
    # Sweep by file signature; DB references alone miss earlier strays.
    for cur_dir, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".superpicky"]
        for fn in files:
            if not fn.lower().endswith((".jpg", ".jpeg")) or fn.startswith("._"):
                continue
            abs_p = os.path.join(cur_dir, fn)
            rel_p = os.path.relpath(abs_p, root)
            if rel_p in to_delete:
                continue
            stem = os.path.splitext(fn)[0]
            has_raw = any(
                os.path.exists(os.path.join(cur_dir, stem + e)) for e in RAW_EXT
            )
            if has_raw and _is_generated_preview(abs_p):
                to_delete.append(rel_p)

    return to_delete, fix_current, clear_jpeg, relink


def apply_plan(root: str) -> Dict[str, int]:
    """
    执行修复：先搬文件再写库，任何一步失败都不让 DB 指向不存在的位置。

    Apply the repair: move files first, then write the DB, so a failed move
    never leaves the database pointing at a path that does not exist.

    参数 / Args:
        root: 照片批次根目录

    返回 / Returns:
        Dict[str, int]: 各项实际完成数（deleted / current_path_fixed /
                        temp_jpeg_cleared / relinked）
    """
    to_delete, fix_current, clear_jpeg, relink = plan(root)
    db_path = os.path.join(root, ".superpicky", "report.db")

    for rel in to_delete:
        target = os.path.join(root, rel)
        if os.path.exists(target):
            os.remove(target)
        # macOS 在 ExFAT 上留的 ._ 伴随文件一并清掉
        # Drop the macOS ._ companion file left on ExFAT volumes.
        companion = os.path.join(
            os.path.dirname(target), "._" + os.path.basename(target)
        )
        if os.path.exists(companion):
            os.remove(companion)

    moved: List[Tuple[str, str]] = []
    for prefix, src_rel, dst_rel in fix_current:
        src = os.path.join(root, src_rel)
        dst = os.path.join(root, dst_rel)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            moved.append((prefix, dst_rel))     # 已经在位，只需写库
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, dst)
            moved.append((prefix, dst_rel))
        except OSError as e:
            print(f"    ⚠️ RAW 移动失败 {src_rel}: {e}")

    con = sqlite3.connect(db_path)
    try:
        for prefix, new_rel in moved:
            con.execute("UPDATE photos SET current_path = ? WHERE filename = ?",
                        (new_rel, prefix))
        for prefix, _stale in clear_jpeg:
            con.execute("UPDATE photos SET temp_jpeg_path = NULL WHERE filename = ?",
                        (prefix,))
        for prefix, _dead, found in relink:
            con.execute("UPDATE photos SET current_path = ? WHERE filename = ?",
                        (found, prefix))
        con.commit()
    finally:
        con.close()

    return {
        "deleted": len(to_delete),
        "current_path_fixed": len(moved),
        "temp_jpeg_cleared": len(clear_jpeg),
        "relinked": len(relink),
    }


def main() -> int:
    """命令行入口：默认预演，加 --apply 才真正改动。"""
    ap = argparse.ArgumentParser(
        description="修复被缓存预览图污染的 SuperPicky 库数据（默认只预演）"
    )
    ap.add_argument("dirs", nargs="+", help="照片批次目录（含 .superpicky/report.db）")
    ap.add_argument("--apply", action="store_true",
                    help="真正执行；不加则只报告将要做什么")
    args = ap.parse_args()

    for root in args.dirs:
        name = os.path.basename(root.rstrip(os.sep)) or root
        if args.apply:
            stats = apply_plan(root)
            print(f"[已执行] {name}: 删预览图 {stats['deleted']} | "
                  f"RAW 归位 {stats['current_path_fixed']} | "
                  f"temp_jpeg 清空 {stats['temp_jpeg_cleared']} | "
                  f"失效路径找回 {stats['relinked']}")
        else:
            to_delete, fix_current, clear_jpeg, relink = plan(root)
            print(f"[预演] {name}: 删预览图 {len(to_delete)} | "
                  f"RAW 归位 {len(fix_current)} | temp_jpeg 清空 {len(clear_jpeg)} | "
                  f"失效路径找回 {len(relink)}")
            for rel in to_delete:
                print(f"    删除: {rel}")
            for prefix, src, dst in fix_current:
                print(f"    搬回 RAW: {prefix}\n        {src}\n     →  {dst}")
            for prefix, dead, found in relink:
                print(f"    找回: {prefix}\n        {dead}\n     →  {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
