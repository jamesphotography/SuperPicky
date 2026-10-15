# -*- coding: utf-8 -*-
"""
处理文件格式筛选（#126）：只处理指定相机 / 格式拍摄的照片。

无 Qt 依赖，供设置中心、主处理流程与测试共用。

判断以「同一主文件名的一组文件」为单位：
- 与 RAW 同名的 JPG 是那张 RAW 的一部分（预览 + 随行文件），跟随 RAW 是否被处理；
- 只有「没有同名 RAW / HEIF 的 JPG」才算「单独的 JPEG」，由 jpeg 分组控制，
  默认不勾选（用户拍板：例如 darktable 导出的 JPG 不应再被处理）。

File-format filter for processing (#126): process only photos of the chosen
camera formats. Qt-free; shared by the settings center, the processor and
tests. Decisions are made per same-stem file group: a JPG paired with a RAW
follows that RAW; only a JPG without a same-stem RAW/HEIF counts as a
"standalone JPEG", which is unchecked by default.
"""

from typing import FrozenSet, List, Optional, Sequence, Tuple

# 筛选模式 / Filter modes
FORMAT_FILTER_MODES: Tuple[str, ...] = ("all", "selected")

# (分组 id, 扩展名)；顺序即设置界面的显示顺序。扩展名必须是 constants 里
# 实际支持的格式——这里列出的格式不等于新增支持。
# (group id, extensions) in display order. Only formats the app already
# supports appear here; listing one does not add support for it.
FORMAT_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("canon", (".cr2", ".cr3")),
    ("nikon", (".nef",)),
    ("sony", (".arw",)),
    ("fujifilm", (".raf",)),
    ("olympus", (".orf",)),
    ("panasonic", (".rw2",)),
    ("pentax", (".pef",)),
    ("hasselblad", (".3fr",)),
    ("phaseone", (".iiq",)),
    ("dng", (".dng",)),
    ("heif", (".hif", ".heif", ".heic")),
    ("jpeg", (".jpg", ".jpeg")),
)

FORMAT_GROUP_IDS: Tuple[str, ...] = tuple(gid for gid, _ in FORMAT_GROUPS)

# 切到「只处理勾选的格式」时的默认勾选：除「单独的 JPEG」外全部勾选
# Default checks when switching to "selected": everything except standalone JPEG
DEFAULT_FORMAT_GROUPS: Tuple[str, ...] = tuple(g for g in FORMAT_GROUP_IDS if g != "jpeg")

# 不随界面语言变化的分组名（品牌名 / 格式名）；heif 与 jpeg 走 i18n
# Group labels that are not translated (brands / format names); heif and jpeg use i18n
BRAND_LABELS = {
    "canon": "Canon", "nikon": "Nikon", "sony": "Sony", "fujifilm": "Fujifilm",
    "olympus": "OM System / Olympus", "panasonic": "Panasonic", "pentax": "Pentax",
    "hasselblad": "Hasselblad", "phaseone": "Phase One", "dng": "DNG",
}


def normalize_groups(groups: Optional[Sequence[str]]) -> List[str]:
    """
    清洗分组列表：去掉未知 id 与重复项，按 FORMAT_GROUPS 的顺序返回。

    Drop unknown/duplicate ids and return them in FORMAT_GROUPS order.

    参数 / Parameters:
        groups: 分组 id 列表（可为 None）/ group ids (may be None).

    返回 / Returns:
        List[str]: 合法的分组 id / valid group ids.
    """
    wanted = set(groups or ())
    return [gid for gid in FORMAT_GROUP_IDS if gid in wanted]


def allowed_extensions(mode: str, groups: Optional[Sequence[str]]) -> Optional[FrozenSet[str]]:
    """
    把设置换算成允许处理的扩展名集合（小写，含点）。

    Translate the setting into the set of allowed lowercase extensions.

    参数 / Parameters:
        mode (str): "all" 或 "selected" / "all" or "selected".
        groups: 勾选的分组 id / checked group ids.

    返回 / Returns:
        Optional[FrozenSet[str]]: None 表示不筛选（处理全部）；否则为允许的扩展名。
        None means no filtering; otherwise the allowed extensions.
    """
    if mode != "selected":
        return None
    chosen = set(normalize_groups(groups))
    return frozenset(ext for gid, exts in FORMAT_GROUPS if gid in chosen for ext in exts)


def extension_group(ext: str) -> Optional[str]:
    """
    扩展名所属的分组 id；不在任何分组里返回 None。

    The group id an extension belongs to, or None.
    """
    ext = ext.lower()
    for gid, exts in FORMAT_GROUPS:
        if ext in exts:
            return gid
    return None
