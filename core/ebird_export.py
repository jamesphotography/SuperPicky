# -*- coding: utf-8 -*-
"""
eBird 观测记录导出 / Export observations in the eBird Record Format.

把一批处理结果整理成可直接在 eBird 网站导入的 CSV：一天一份清单，同一天
同一鸟种一行，数量固定 1（拍到即记录，不做个体计数）。

格式要点（依据 eBird 官方文档与模板）：
  - 19 列，顺序固定，**不带表头**——列错位会直接导致导入失败；
  - 日期 M/D/YYYY，时间 H:MM；
  - 字段内不得出现引号，导入器无法处理。

**鸟种只给学名，通用名一律留空**（2026-09-07 真实导入验证得出）：
eBird 按**用户账号的显示语言**匹配通用名，同一个种在不同 locale 下名字不同：

    Myzomela obscura         en_US: Dusky Myzomela       en_AU: Dusky Honeyeater
    Oriolus flavocinctus     en_US: Green Oriole         en_AU: Yellow Oriole
    Myzomela erythrocephala  en_US: Red-headed Myzomela  en_AU: Red-headed Honeyeater

首版填的是全球(Clements)通用名，结果在澳洲账号下这三个种被判 "unknown
species"，而两地同名的十六个种全部成功——一一吻合。改为只填学名后同一批
数据导入成功。学名是分类学的唯一标识、不随 locale 变化，对任何国家的用户
都成立；eBird 导入后会按各自语言显示对应的通用名。

只有查不到学名时才退而填模型的英文名——那是仅存的线索，eBird 多半认不出，
会让用户在导入界面手工选种，备注里写明来源便于核对。

Species are identified by scientific name only: eBird matches common names in
the account's display language, so a globally-correct common name gets
rejected on e.g. an Australian account. Verified against a real import.
"""
from __future__ import annotations

import csv
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# eBird Record Format 的 19 列，顺序即协议，不可调整
# The 19 columns of the eBird Record Format; order is the contract.
EBIRD_COLUMNS: List[str] = [
    "Common Name", "Genus", "Species", "Number", "Species Comments",
    "Location Name", "Latitude", "Longitude", "Date", "Start Time",
    "State/Province", "Country Code", "Protocol", "Number of Observers",
    "Duration", "All observations reported?", "Effort Distance Miles",
    "Effort area acres", "Submission Comments",
]

# 只有这两类匹配可信到能直接用它的通用名；其余只给学名
# Only these match types are trusted enough to supply a common name.
_TRUSTED_MATCH_TYPES = frozenset({"exact", "avilist_only"})

# 观测记录的固定字段：拍照顺带记录属于 Incidental，且只记了拍到的鸟，
# 因此「是否报告了全部所见」为 N。
# Photography yields incidental records, and only photographed birds are
# listed, hence "all observations reported" = N.
PROTOCOL = "Incidental"
ALL_OBSERVATIONS_REPORTED = "N"
NUMBER_OF_OBSERVERS = "1"
DEFAULT_COUNT = "1"

# 低于此星级的照片不计入观测记录（低星多为虚焦/误识）
# Below this rating photos are excluded; they are mostly blurred or misIDed.
MIN_RATING = 2

_reference_cache: Optional[Dict[str, Tuple[str, str, str]]] = None


@dataclass
class SpeciesId:
    """
    一个鸟种在 eBird 记录里的身份。

    属性 / Attributes:
        common_name: eBird 通用名；低可信或查不到时为空串（绝不编造）
        genus:       属名
        species:     种加词
        confident:   映射是否可信，供 UI 汇总提示
        comment:     写进 Species Comments 的核对提示；可信时为空
    """
    common_name: str = ""
    genus: str = ""
    species: str = ""
    confident: bool = False
    comment: str = ""


@dataclass
class Entry:
    """清单里的一行观测：一个鸟种 + 数量。"""
    species: SpeciesId
    count: str = DEFAULT_COUNT


@dataclass
class Checklist:
    """
    一份 eBird 清单：同一天、同一地点的全部观测。

    属性 / Attributes:
        date:       M/D/YYYY
        start_time: H:MM，当天最早一张照片的时间
        location:   用户填写的地点名
        latitude / longitude: 当天照片 GPS 的中位数；无 GPS 时为空串
        entries:    该日的观测行，按鸟种去重
    """
    date: str
    start_time: str
    location: str
    latitude: str = ""
    longitude: str = ""
    entries: List[Entry] = field(default_factory=list)


def _reference_db_path() -> str:
    """鸟类参考库路径（含 eBird/Clements 命名映射）。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "birdid", "data", "bird_reference.sqlite")


def _load_reference() -> Dict[str, Tuple[str, str, str]]:
    """
    载入并缓存「鸟名 → (学名, eBird通用名, 匹配类型)」查表。

    中文名与英文名都作为键，因为照片记录里可能只有其中一个。整表一万余行，
    一次读进内存即可；导出时每个鸟种查一次。

    Build a name → (scientific, eBird common name, match type) lookup, keyed by
    both Chinese and English names since a photo may carry only one.

    返回 / Returns:
        Dict[str, Tuple[str, str, str]]: (学名, eBird英文名, 匹配类型)；
        查不到参考库时为空字典
    """
    global _reference_cache
    if _reference_cache is not None:
        return _reference_cache

    table: Dict[str, Tuple[str, str, str]] = {}
    path = _reference_db_path()
    if os.path.exists(path):
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT b.chinese_simplified, b.english_name, b.scientific_name,"
                    "       a.en_name_clements, a.en_name_avilist, a.match_type "
                    "FROM BirdCountInfo b "
                    "LEFT JOIN avilist_map a ON a.scientific_name_model = b.scientific_name"
                ).fetchall()
            finally:
                con.close()
            for cn, en, sci, clements, avilist, match in rows:
                # Clements 列偶有缺失（105 种），此时用 AviList 名兜底——两者
                # 都是权威分类，AviList 名同样是标准 eBird 命名，白白降级
                # 只会让用户去核对本来没问题的鸟种。
                # Fall back to the AviList name when Clements is missing.
                value = (sci or "", (clements or avilist or ""), match or "")
                for key in (cn, en):
                    if key:
                        table.setdefault(str(key).strip(), value)
        except Exception:
            # 参考库缺失或损坏时退化为「只用照片自带的鸟名」，不阻断导出
            # Degrade to photo-supplied names rather than blocking the export.
            table = {}

    _reference_cache = table
    return table


def reset_reference_cache() -> None:
    """清空查表缓存（供测试切换参考库）。"""
    global _reference_cache
    _reference_cache = None


def resolve_species(cn_name: Optional[str], en_name: Optional[str]) -> SpeciesId:
    """
    把照片里的鸟名解析成 eBird 记录所需的身份：**只给学名**。

    通用名一律留空，因为 eBird 按账号显示语言匹配它——见模块开头对实测
    失败的说明。只有查不到学名时才退而填模型英文名作为仅存线索。

    Resolve into eBird identity fields using the scientific name only; the
    common name is locale-dependent and would break non-en_US accounts.

    参数 / Args:
        cn_name: 中文鸟名，可为 None
        en_name: 英文鸟名，可为 None

    返回 / Returns:
        SpeciesId: 属名/种加词为主；查不到学名时用英文名兜底并标记待核对
    """
    table = _load_reference()
    raw_en = (en_name or "").strip()
    raw_cn = (cn_name or "").strip()

    # 中英文名分别查再比对：两个名字本应来自同一次识别，指向不同鸟种说明
    # 记录有问题（手工改名只改了一半等），此时不能默默挑一个。
    # Cross-check both names; a disagreement means the record is inconsistent.
    hit_cn = table.get(raw_cn) if raw_cn else None
    hit_en = table.get(raw_en) if raw_en else None

    conflicting = bool(hit_cn and hit_en and hit_cn[0] != hit_en[0])
    entry = hit_cn or hit_en

    if entry is None or not entry[0]:
        # 查不到学名：填模型英文名作为仅存线索，eBird 多半会让用户手工选种
        # No scientific name known: fall back to the model's English name.
        return SpeciesId(
            common_name=_strip_quotes(raw_en or raw_cn),
            genus="", species="", confident=False,
            comment=_review_comment(raw_en or raw_cn),
        )

    genus, _, species = (entry[0] or "").partition(" ")
    return SpeciesId(
        common_name="", genus=genus, species=species,
        confident=not conflicting,
        comment=_review_comment(f"{raw_cn} / {raw_en}") if conflicting else "",
    )


def _review_comment(model_name: str) -> str:
    """
    生成供人工核对的备注，保留模型的原始识别。

    Build the review note that preserves what the model actually identified.
    """
    name = _strip_quotes(model_name) or "unknown"
    return f"SuperPicky ID: {name} - please verify"


def _strip_quotes(text: object) -> str:
    """
    去掉字段里的引号 —— eBird 导入器无法处理字段内的引号（官方明确要求）。

    Remove quotes; the eBird importer cannot handle them inside fields.
    """
    return str(text or "").replace('"', "").replace("“", "").replace("”", "").strip()


def _parse_shot_time(value: object) -> Optional[datetime]:
    """
    解析照片的拍摄时间，兼容 EXIF 的冒号日期写法。

    Parse the capture time, tolerating EXIF's colon-separated date form.

    参数 / Args:
        value: date_time_original 字段值

    返回 / Returns:
        Optional[datetime]: 解析不出时为 None（该照片无法构成观测记录）
    """
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-")
    # EXIF 常见形如 "2026:09:04 07:30:00"：只有日期部分用冒号分隔
    if len(text) >= 10 and text[4] == ":" and text[7] == ":":
        text = text[:4] + "-" + text[5:7] + "-" + text[8:]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _median_coord(values: List[float]) -> str:
    """
    取坐标中位数并格式化；无值时返回空串。

    用中位数而非均值：个别 GPS 漂移点不会把整份清单的位置带偏。

    Median (not mean) so a stray GPS fix cannot drag the location off.
    """
    if not values:
        return ""
    med = statistics.median(values)
    return f"{med:g}"


def build_checklists(photos: List[dict], location_name: str) -> List[Checklist]:
    """
    把照片整理成按日期分组的 eBird 清单。

    规则：只收 2★ 以上且识别出鸟种、且有拍摄时间的照片；同一天同一鸟种只出
    一行、数量 1；开始时间取当天最早一张；坐标取当天 GPS 中位数。

    Group photos into one checklist per date; 2★+ identified photos only, one
    row per species with count 1.

    参数 / Args:
        photos:        照片字典列表（来自 report.db）
        location_name: 用户填写的地点名，写入每一行

    返回 / Returns:
        List[Checklist]: 按日期升序；没有可用照片时为空列表
    """
    location = _strip_quotes(location_name)

    by_date: Dict[str, List[Tuple[datetime, dict]]] = {}
    for photo in photos:
        if (photo.get("rating") or 0) < MIN_RATING:
            continue
        if not (photo.get("bird_species_cn") or photo.get("bird_species_en")):
            continue
        shot = _parse_shot_time(photo.get("date_time_original"))
        if shot is None:
            continue
        by_date.setdefault(shot.strftime("%Y-%m-%d"), []).append((shot, photo))

    checklists: List[Checklist] = []
    for day in sorted(by_date):
        items = sorted(by_date[day], key=lambda pair: pair[0])
        earliest = items[0][0]

        lats = [float(p["gps_latitude"]) for _, p in items
                if p.get("gps_latitude") is not None]
        lons = [float(p["gps_longitude"]) for _, p in items
                if p.get("gps_longitude") is not None]

        checklist = Checklist(
            date=f"{earliest.month}/{earliest.day}/{earliest.year}",
            start_time=f"{earliest.hour}:{earliest.minute:02d}",
            location=location,
            latitude=_median_coord(lats),
            longitude=_median_coord(lons),
        )

        seen: set = set()
        for _, photo in items:
            cn = (photo.get("bird_species_cn") or "").strip()
            en = (photo.get("bird_species_en") or "").strip()
            key = cn or en
            if key in seen:
                continue                    # 同一天同一鸟种只记一次
            seen.add(key)
            checklist.entries.append(Entry(species=resolve_species(cn, en)))

        if checklist.entries:
            checklists.append(checklist)

    return checklists


def to_rows(checklists: List[Checklist]) -> List[List[str]]:
    """
    把清单摊平成 eBird Record Format 的行（每行 19 个字段，无表头）。

    Flatten checklists into eBird Record Format rows (19 fields, no header).

    参数 / Args:
        checklists: build_checklists 的结果

    返回 / Returns:
        List[List[str]]: 可直接写入 CSV 的行
    """
    rows: List[List[str]] = []
    for cl in checklists:
        for entry in cl.entries:
            s = entry.species
            rows.append([
                _strip_quotes(s.common_name),        # Common Name
                _strip_quotes(s.genus),              # Genus
                _strip_quotes(s.species),            # Species
                entry.count,                         # Number
                _strip_quotes(s.comment),            # Species Comments
                cl.location,                         # Location Name
                cl.latitude,                         # Latitude
                cl.longitude,                        # Longitude
                cl.date,                             # Date
                cl.start_time,                       # Start Time
                "",                                  # State/Province
                "",                                  # Country Code
                PROTOCOL,                            # Protocol
                NUMBER_OF_OBSERVERS,                 # Number of Observers
                "",                                  # Duration
                ALL_OBSERVATIONS_REPORTED,           # All observations reported?
                "",                                  # Effort Distance Miles
                "",                                  # Effort area acres
                "",                                  # Submission Comments
            ])
    return rows


def write_csv(path: str, rows: List[List[str]]) -> None:
    """
    写出 eBird 导入用的 CSV：**不写表头**，UTF-8。

    Write the import CSV with no header row.

    参数 / Args:
        path: 目标文件路径
        rows: to_rows 的结果

    异常 / Raises:
        OSError: 路径不可写时由调用方处理
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)


def summarize(checklists: List[Checklist]) -> Dict[str, object]:
    """
    汇总导出结果，供界面回报：几份清单、几个鸟种、哪些需要人工核对。

    Summarize the export for the UI: checklist and species counts, plus the
    species that need manual verification on the eBird site.

    参数 / Args:
        checklists: build_checklists 的结果

    返回 / Returns:
        Dict[str, object]: {"checklists": int, "rows": int,
                            "needs_review": List[str]}
    """
    rows = sum(len(c.entries) for c in checklists)
    review: List[str] = []
    for cl in checklists:
        for entry in cl.entries:
            if not entry.species.confident:
                label = entry.species.comment or entry.species.genus or "?"
                if label not in review:
                    review.append(label)
    return {"checklists": len(checklists), "rows": rows, "needs_review": review}
