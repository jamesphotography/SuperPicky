#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从本机安装的懂鸟 app 提取鸟种罕见指数，匹配到本项目模型的类别，产出 CSV。

产物交给 scripts_dev/build_custom_rarity.py 转成 custom_rarity.db，落在用户
配置目录。**数据本身绝不进仓库、绝不进安装包**——懂鸟是商业软件，这份数据是
它的核心资产；本项目的 core/custom_rarity.py 一开始就是按「应用不附带任何
数据、用户自备」设计的，本脚本不破这个例。

数据位置与格式（懂鸟 1.5.10 实测）:
    <Runner.app>/Frameworks/App.framework/flutter_assets/assets/labels/
        dn_labels.<日期>.picto.bin      protobuf，顶层为重复的 field 1
    每条记录 field 2 是内嵌消息，其中:
        3=目  4=科  5=中文名  6=英文名  7=学名  8=繁体名  12=罕见度(float64)
    罕见度 -1.0 是哨兵值（无评级/已灭绝），必须过滤，否则界面会显示 -1.0。

匹配策略（沿用上一版 CSV 的 matched_by 口径）:
    学名 → 英文名 → avilist 桥接；都不中的保留上一版的手工条目。
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sqlite3
import struct
import sys
from typing import Dict, Iterator, Optional, Tuple

#: 仓库根目录（本脚本在 scripts_dev/ 下）/ Repository root.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 罕见度的哨兵值：无评级/已灭绝。必须过滤，否则界面会显示「罕见度 -1.0」。
#: Sentinel meaning "not rated"; must be filtered or the UI shows -1.0.
SENTINEL = -1.0

#: 懂鸟 app 在 macOS 上的默认安装位置（iOS app 以 Wrapper 形式运行）。
#: 标签文件名带日期（如 dn_labels.260630.picto.bin），故用通配符定位。
#: Default install location of the 懂鸟 app on macOS; the labels file is dated.
DEFAULT_APP_GLOB = (
    "/Applications/Runner.app/Wrapper/Runner.app/Frameworks/App.framework/"
    "flutter_assets/assets/labels/dn_labels.*.picto.bin"
)


def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    """读一个 varint，返回 (值, 新位置) / Read one varint."""
    result = shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


def _fields(buf: bytes) -> Iterator[Tuple[int, object]]:
    """
    通用 protobuf 遍历：产出 (字段号, 值)。没有 .proto 定义，按 wire type 解。

    Generic protobuf walk; no schema available, so decode by wire type.
    """
    i = 0
    while i < len(buf):
        try:
            key, i = _varint(buf, i)
        except (IndexError, ValueError):
            return
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
            yield field, value
        elif wire == 2:
            length, i = _varint(buf, i)
            yield field, buf[i:i + length]
            i += length
        elif wire == 5:
            yield field, struct.unpack("<f", buf[i:i + 4])[0]
            i += 4
        elif wire == 1:
            yield field, struct.unpack("<d", buf[i:i + 8])[0]
            i += 8
        else:
            return


def find_labels_file(pattern: str = DEFAULT_APP_GLOB) -> Optional[str]:
    """
    定位懂鸟的标签文件（文件名带日期，故用通配符）。

    参数 / Parameters:
    pattern (str): 通配路径 / Glob pattern.

    返回 / Returns:
    Optional[str]: 匹配到的最新一个；没有则 None（未安装懂鸟是常态）。

    Locate the dated labels file; None when the app is not installed.
    """
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def parse_dongniao(bin_path: str) -> Dict[str, dict]:
    """
    解析懂鸟标签库，返回 {学名: {...}}。

    参数 / Parameters:
        bin_path (str): dn_labels.*.picto.bin 的路径。

    返回 / Returns:
        Dict[str, dict]: 学名 → {cn, en, rarity}，rarity 为哨兵值的条目已剔除。
    """
    data = open(bin_path, "rb").read()
    out: Dict[str, dict] = {}
    for field, payload in _fields(data):
        if field != 1 or not isinstance(payload, bytes):
            continue
        inner = dict(_fields(payload)).get(2)
        if not isinstance(inner, bytes):
            continue
        rec = dict(_fields(inner))

        def text(key: int) -> str:
            value = rec.get(key)
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else ""

        sci = text(7).strip()
        rarity = rec.get(12)
        if not sci or not isinstance(rarity, float) or rarity == SENTINEL:
            continue
        out.setdefault(sci, {"cn": text(5).strip(), "en": text(6).strip(),
                             "rarity": round(rarity, 2)})
    return out


def load_manual(old_csv: str) -> Dict[str, dict]:
    """
    取出上一版 CSV 里的手工条目（灭绝种等），它们不在懂鸟数据里但值得保留。

    Carry over hand-curated rows (extinct species etc.) absent from the source.
    """
    manual: Dict[str, dict] = {}
    if not os.path.exists(old_csv):
        return manual
    with open(old_csv, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if (row.get("matched_by") or "").startswith("手工"):
                key = (row.get("model_class_id") or "").strip()
                if key:
                    manual[key] = row
    return manual


def build_rows(dongniao: Dict[str, dict], manual: Dict[str, dict]) -> list:
    """
    产出 CSV 行：**模型类别 ∪ 懂鸟全集**。

    不能只产出模型的类别。运行时查罕见度只用中/英文名（见
    `core/custom_rarity.py` 的 `_load`），`model_class_id` 从不参与查询，所以
    模型没有的鸟种照样查得到——把行限制在模型类别内是纯损失。而恰恰是这些
    鸟种最需要：模型不认识它们，用户只能手工改鸟名，改完却看不到罕见度。
    例：模型只有拆分前的 `Falcunculus frontatus`，用户改成拆分后的「北鵙雀鹟」
    (`F. whitei`)，懂鸟给了 9.8，详情页却空着（2026-09-23 用户反馈）。

    参数 / Parameters:
    dongniao (Dict[str, dict]): 学名 → {cn, en, rarity}。
    manual (Dict[str, dict]): 上一版 CSV 里的手工条目，按 model_class_id 索引。

    返回 / Returns:
    tuple: (行列表, 匹配方式统计)。

    Emit the union of our model's classes and the full source dataset: the
    runtime looks rarity up by name, so species our model lacks are still
    useful — and they are precisely the ones a user had to rename by hand.
    """
    ref = sqlite3.connect(os.path.join(REPO, "birdid", "data", "bird_reference.sqlite"))
    ref.row_factory = sqlite3.Row

    by_en = {}
    for sci, rec in dongniao.items():
        if rec["en"]:
            by_en.setdefault(rec["en"], (sci, rec))

    bridge = {r["scientific_name_model"]: r["scientific_name_avilist"]
              for r in ref.execute(
                  "SELECT scientific_name_model, scientific_name_avilist "
                  "FROM avilist_map WHERE scientific_name_avilist IS NOT NULL")}

    rows, stats = [], {}
    used_sci = set()          # 已被模型类别用掉的懂鸟学名 / source names already consumed
    for r in ref.execute(
            "SELECT model_class_id, scientific_name, english_name, chinese_simplified "
            "FROM BirdCountInfo ORDER BY model_class_id"):
        sci = (r["scientific_name"] or "").strip()
        en = (r["english_name"] or "").strip()
        hit, how = None, "未匹配"

        if sci and sci in dongniao:
            hit, how = dongniao[sci], "学名"
        elif en and en in by_en:
            hit, how = by_en[en][1], "英文名"
        elif sci and bridge.get(sci) in dongniao:
            hit, how = dongniao[bridge[sci]], "avilist桥"
        elif str(r["model_class_id"]) in manual:
            old = manual[str(r["model_class_id"])]
            rows.append(old)
            stats[old.get("matched_by") or "手工"] = stats.get(
                old.get("matched_by") or "手工", 0) + 1
            continue

        stats[how] = stats.get(how, 0) + 1
        if hit is not None:
            used_sci.add(sci if sci in dongniao else
                         (by_en[en][0] if how == "英文名" else bridge.get(sci, "")))
        rows.append({
            "model_class_id": r["model_class_id"],
            "scientific_name": sci,
            "english_name": en,
            # 中文名以懂鸟为准：它跟的是现行中文名录，正是我们想要的参照
            "chinese_simplified": (hit["cn"] if hit else (r["chinese_simplified"] or "")),
            "rarity_index": (hit["rarity"] if hit else ""),
            "matched_by": how,
        })

    # 补上懂鸟有、模型没有的鸟种（拆分出的新种多在此列），model_class_id 留空。
    # Add source species our model lacks; they still resolve by name at runtime.
    for sci, rec in dongniao.items():
        if sci in used_sci:
            continue
        stats["模型无此类别"] = stats.get("模型无此类别", 0) + 1
        rows.append({
            "model_class_id": "",
            "scientific_name": sci,
            "english_name": rec["en"],
            "chinese_simplified": rec["cn"],
            "rarity_index": rec["rarity"],
            "matched_by": "模型无此类别",
        })
    return rows, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从本机安装的懂鸟 app 提取罕见指数，产出待转换的 CSV")
    parser.add_argument("--bin", default="", help="dn_labels.*.picto.bin；留空则自动定位")
    parser.add_argument("--out", required=True, help="输出 CSV")
    parser.add_argument("--old-csv", default="", help="上一版 CSV（保留手工条目）")
    args = parser.parse_args()

    bin_path = args.bin or find_labels_file()
    if not bin_path:
        print("[ERROR] 找不到懂鸟标签文件；请确认已安装懂鸟，或用 --bin 指定路径")
        return 1

    print(f"[来源] {bin_path}")
    dongniao = parse_dongniao(bin_path)
    print(f"[懂鸟] 有效条目 {len(dongniao)} 条（已剔除 -1.0 哨兵）")
    manual = load_manual(args.old_csv)
    print(f"[保留] 上一版手工条目 {len(manual)} 条")

    rows, stats = build_rows(dongniao, manual)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "model_class_id", "scientific_name", "english_name",
            "chinese_simplified", "rarity_index", "matched_by"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[输出] {len(rows)} 行 -> {args.out}")
    for key in sorted(stats, key=lambda k: -stats[k]):
        print(f"    {key}: {stats[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
