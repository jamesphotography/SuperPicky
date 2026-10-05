# -*- coding: utf-8 -*-
"""
从 OpenCC 词典生成「简体 → 台湾正体」精简转换表 locales/zh_tw_convert.json。

繁体 TW 界面下，代码里写死的中文（罕见度档名、报告模板、国家名等）与语言包之外的
文字需要在运行时转成台湾正体。为免引入额外依赖，把 OpenCC s2twp 链路用到的词典
压成一个 JSON 随 locales 打包，由 tools/zh_convert.py 读取。

STPhrases 必须整表保留：只留「整词 ≠ 逐字」的词条看似等价，实际会改变最长匹配的
分词（例：「恢复」被删后，「恢复文件」会被「复文→覆文」截走，转成「恢覆檔案」）。
生成后会用 OpenCC 对语言包与鸟名做逐条对照，不一致会打印出来。

用法 / Usage:
    python3 scripts_dev/build_zh_tw_dict.py <opencc 包目录（含 dictionary/）>
    依赖 opencc-python-reimplemented（pip install opencc-python-reimplemented），
    只在生成时需要，运行时不需要。

词典来源 / Dictionary source: OpenCC (https://github.com/BYVoid/OpenCC), Apache License 2.0.

Builds a compact Simplified → Taiwan Traditional table from the OpenCC
dictionaries so the app can convert hard-coded Chinese strings at runtime
without depending on OpenCC. Only STPhrases entries whose phrase conversion
The full STPhrases table is kept: dropping entries that convert the same as
per-character changes maximum-match segmentation.
"""

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "locales", "zh_tw_convert.json")

# 转换后的台湾用语修正（OpenCC 没覆盖或转错的词）
# Post-conversion fixes for Taiwan usage that OpenCC misses or gets wrong.
POST_FIXES = [
    ("後設資料", "中繼資料"),   # metadata 台湾惯用「中繼資料」
    ("於震", "于震"),           # 姓氏「于」不应转成「於」
    ("澳大利亞", "澳洲"),       # 台湾惯称「澳洲」
    # 以下为 OpenCC s2twp 转出、但不是台湾软件惯用的词（按顺序替换，长词在前）
    # Words OpenCC leaves in mainland phrasing (ordered; longer phrases first).
    ("許可權", "權限"),         # 权限：OpenCC 误转「許可權」
    ("回收站", "垃圾桶"),       # 回收站 → 垃圾桶
    ("選中", "選取"),           # 选中 → 選取
    ("當前", "目前"),           # 当前 → 目前
    ("資源管理器", "檔案總管"), # Windows 资源管理器 → 檔案總管
    ("質量", "品質"),           # 质量 → 品質
    ("識別", "辨識"),           # 识别（影像/鸟种）→ 辨識
    ("重啟應用", "重新啟動應用程式"),
    ("重啟", "重新啟動"),
    ("郵箱", "電子信箱"),
    ("檢測", "偵測"),           # 检测（到）→ 偵測
    ("響應", "回應"),
    ("後臺", "背景"),           # 后台 → 背景
    ("拖拽", "拖曳"),
    ("目錄", "資料夾"),         # 目录（文件夹）→ 資料夾
    ("根資料夾", "根目錄"),     # 但「根目錄」台湾也这么叫，改回
]


def _load(dict_dir: str, name: str) -> dict:
    """
    读取一个 OpenCC 文本词典（每行「键<TAB>值1 值2…」），取第一个候选值。

    参数:
    dict_dir (str): dictionary 目录
    name (str): 词典文件名

    返回:
    dict: 键 → 首选转换结果

    Reads one OpenCC text dictionary, keeping the first candidate per key.
    """
    table = {}
    with open(os.path.join(dict_dir, name), "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if "\t" not in line:
                continue
            key, values = line.split("\t", 1)
            table[key] = values.split(" ")[0]
    return table


def main() -> None:
    """生成转换表并与 OpenCC 逐条对照 / Build the table and cross-check it."""
    pkg_dir = sys.argv[1]
    dict_dir = os.path.join(pkg_dir, "dictionary")
    chars = _load(dict_dir, "STCharacters.txt")
    phrases = _load(dict_dir, "STPhrases.txt")

    data = {
        "_source": "OpenCC s2twp dictionaries (https://github.com/BYVoid/OpenCC), Apache License 2.0",
        "chars": chars,
        "phrases": phrases,
        "tw_phrases": _load(dict_dir, "TWPhrases.txt"),
        "tw_variants": _load(dict_dir, "TWVariants.txt"),
        "post_fixes": POST_FIXES,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(f"written {OUT}: {os.path.getsize(OUT)} bytes, phrases={len(data['phrases'])}")

    # 与 OpenCC 对照（语言包 + 全部鸟名）/ Cross-check against OpenCC
    sys.path.insert(0, os.path.dirname(pkg_dir))
    sys.path.insert(0, ROOT)
    from opencc import OpenCC
    from tools import zh_convert
    zh_convert._TABLE = None  # 强制重新加载刚生成的表 / reload the new table
    cc = OpenCC("s2twp")

    def reference(text: str) -> str:
        out = cc.convert(text)
        for a, b in POST_FIXES:
            out = out.replace(a, b)
        return out

    corpus = []
    with open(os.path.join(ROOT, "locales", "zh_CN.json"), "r", encoding="utf-8") as f:
        for section in json.load(f).values():
            if isinstance(section, dict):
                corpus.extend(v for v in section.values() if isinstance(v, str))
    conn = sqlite3.connect(os.path.join(ROOT, "birdid", "data", "bird_reference.sqlite"))
    corpus.extend(r[0] for r in conn.execute("SELECT chinese_simplified FROM BirdCountInfo"))
    conn.close()
    bad = [(t, reference(t), zh_convert.to_taiwan(t)) for t in corpus
           if reference(t) != zh_convert.to_taiwan(t)]
    print(f"cross-check: {len(corpus)} strings, {len(bad)} differ")
    for item in bad[:40]:
        print("  ", item)


if __name__ == "__main__":
    main()
