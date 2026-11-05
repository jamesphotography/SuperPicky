"""
元数据输出字段表（issue #153）。

SuperPicky 往照片里写 12 个字段，每个字段都可以在设置中心「输出」页单独关掉。
本模块是这 12 个字段的**唯一清单**：配置键（advanced_config）、写入器用的 item 键
（tools/exiftool_manager）、界面分组与文案键（ui/settings_center）都从这里取，
避免三处各维护一份列表而漂移。本模块不依赖 Qt，CLI 路径也能用。

Metadata output field table (issue #153). SuperPicky writes 12 fields into each
photo and every one can be switched off on the Output settings page. This module
is the single list of those fields: the config key (advanced_config), the item
key the writer uses (tools/exiftool_manager) and the UI group / text key
(ui/settings_center) all come from here. Qt-free so the CLI can use it too.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Tuple


@dataclass(frozen=True)
class MetadataField:
    """
    一个可开关的元数据输出字段。

    属性:
    field_id (str): 稳定 id，界面与测试用它指代字段
    config_key (str): advanced_config 里的布尔开关键
    item_key (str): 写入器 item 字典里携带该字段值的键
    group (str): 界面分组，"core"（核心内容）或 "species"（鸟种识别内容）

    One switchable metadata output field.

    Attributes:
    field_id (str): stable id used by the UI and tests
    config_key (str): boolean switch key in advanced_config
    item_key (str): key carrying the value in the writer's item dict
    group (str): UI group, "core" or "species"
    """

    field_id: str
    config_key: str
    item_key: str
    group: str


# 顺序即界面顺序。关键字复用 4.4 起就有的 birdid_write_keywords，
# 老用户关过的选择原样保留，不需要迁移。
# Order is the UI order. Keywords reuse the existing birdid_write_keywords key,
# so a user who had turned it off keeps that choice with no migration.
METADATA_FIELDS: Tuple[MetadataField, ...] = (
    MetadataField("rating", "metadata_write_rating", "rating", "core"),
    MetadataField("pick", "metadata_write_pick", "pick", "core"),
    MetadataField("label", "metadata_write_label", "label", "core"),
    MetadataField("title", "metadata_write_title", "title", "core"),
    MetadataField("caption", "metadata_write_caption", "caption", "core"),
    MetadataField("sharpness", "metadata_write_sharpness", "sharpness", "core"),
    MetadataField("aesthetic", "metadata_write_aesthetic", "nima_score", "core"),
    MetadataField("focus", "metadata_write_focus", "focus_status", "core"),
    MetadataField("keywords", "birdid_write_keywords", "keywords", "species"),
    MetadataField("iucn", "metadata_write_iucn", "iucn_category", "species"),
    MetadataField("rarity", "metadata_write_rarity", "gbif_rarity_100", "species"),
    MetadataField("beauty", "metadata_write_beauty", "aesthetic_index", "species"),
)

FIELD_IDS: Tuple[str, ...] = tuple(f.field_id for f in METADATA_FIELDS)
FIELDS_BY_ID: Dict[str, MetadataField] = {f.field_id: f for f in METADATA_FIELDS}

# 「一键精简」只保留这 5 项（维护者 2026-11-05 拍板），其余全部取消。
# The "Lean" preset keeps exactly these five fields and clears the rest.
LEAN_PRESET: FrozenSet[str] = frozenset(
    {"rating", "pick", "caption", "sharpness", "aesthetic"}
)

ALL_FIELDS: FrozenSet[str] = frozenset(FIELD_IDS)


def filter_item(item: Dict, enabled: Iterable[str]) -> Dict:
    """
    返回去掉了「已关闭字段」的 item 副本；不认识的键（如 'file'）原样保留。

    参数:
    item (Dict): 写入器 item，如 {'file': ..., 'rating': 3, 'sharpness': 95.3}
    enabled (Iterable[str]): 开着的字段 id 集合

    返回:
    Dict: 新字典，原 item 不被修改

    Return a copy of the item without the keys of switched-off fields; keys
    that are not field values (such as 'file') are kept.

    Parameters:
    item (Dict): writer item
    enabled (Iterable[str]): ids of the enabled fields

    Return:
    Dict: a new dict; the input is not modified
    """
    enabled_set = set(enabled)
    drop = {f.item_key for f in METADATA_FIELDS if f.field_id not in enabled_set}
    return {k: v for k, v in item.items() if k not in drop}


def has_payload(item: Dict) -> bool:
    """
    item 里是否还有至少一个要写的字段值。

    与写入器的判断口径一致：iucn_category 与 keywords 用真值判断（空串 / 空列表不写），
    其余字段只要不是 None 就写（0 星、0 分都是合法值）。

    参数:
    item (Dict): 写入器 item

    返回:
    bool: 有可写内容为 True

    Whether the item still carries at least one value to write. Mirrors the
    writer: iucn_category and keywords are written only when truthy, every
    other field whenever it is not None (0 stars / 0 scores are valid).

    Parameters:
    item (Dict): writer item

    Return:
    bool: True if something would be written
    """
    for f in METADATA_FIELDS:
        value = item.get(f.item_key)
        if f.item_key in ("iucn_category", "keywords"):
            if value:
                return True
        elif value is not None:
            return True
    return False
