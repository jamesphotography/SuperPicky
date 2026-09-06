# -*- coding: utf-8 -*-
"""跨目录连拍合并必须忽略低置信度鸟种。

Burst consolidation must ignore low-confidence species.

背景 / Background:
识鸟置信度低于阈值时，候选鸟名只进内存 file_bird_species(标记 low_confidence)
并写 caption，**从不写入 DB 的 bird_species_cn/en**。主整理 _move_files_to_
rating_folders 明确过滤了该标记，但跨目录连拍合并 _consolidate_burst_groups
漏了，于是用 1%~50% 置信度的鸟名建出真实目录并把整组搬进去。结果磁盘上出现
数据库里根本不存在的「幽灵鸟种目录」——结果浏览器只读 DB，自然显示不出来，
用户看到的就是「浏览器里的鸟种和实际分的目录对不上」。

Low-confidence species names never reach the DB, yet burst consolidation used
them to create real folders, producing species directories the results browser
(DB-driven) cannot show.
"""
from typing import List, Tuple

from core.photo_processor import (
    PhotoProcessor,
    ProcessingCallbacks,
    ProcessingSettings,
)


def _setup_burst_group(tmp_path, low_confidence: bool) -> Tuple[PhotoProcessor, str]:
    """构造一个已落在「其他鸟类」里的 4 张连拍组。

    Build a 4-shot burst group already filed under the "other birds" folder.
    """
    logs: List[Tuple[str, str]] = []
    callbacks = ProcessingCallbacks(log=lambda msg, level="info": logs.append((level, msg)))
    processor = PhotoProcessor(str(tmp_path), ProcessingSettings(), callbacks)

    species = "青头鹦鹉"
    other_birds = processor.i18n.t("logs.folder_other_birds")
    # 主整理已按「低置信度不分鸟种目录」把它们放进了其他鸟类
    landed = tmp_path / other_birds / "3星_优选"
    landed.mkdir(parents=True)

    prefixes = [f"IMG_{i:04d}" for i in range(4)]
    for p in prefixes:
        (landed / f"{p}.NEF").write_bytes(b"stub")

    processor.burst_map = {str(tmp_path / f"{p}.NEF"): 1 for p in prefixes}
    processor.file_ratings = {p: 3 for p in prefixes}
    info = {"cn_name": species, "en_name": "Red-cheeked_Parrot"}
    if low_confidence:
        info["low_confidence"] = True
        info["confidence"] = 6.0
    processor.file_bird_species = {p: dict(info) for p in prefixes}
    return processor, species


def test_low_confidence_species_does_not_create_folder(tmp_path):
    """低置信度鸟名不得用于连拍目录命名——那会造出 DB 里不存在的幽灵目录。

    A low-confidence name must never name a burst folder.
    """
    processor, species = _setup_burst_group(tmp_path, low_confidence=True)
    raw_dict = {p: ".NEF" for p in processor.file_ratings}

    processor._consolidate_burst_groups(raw_dict)

    assert not (tmp_path / species).exists(), (
        f"低置信度鸟名建出了幽灵目录 {species}/ "
        f"/ low-confidence name created a ghost species folder"
    )


def test_high_confidence_species_still_creates_folder(tmp_path):
    """高置信度鸟名仍必须正常建目录——修复不能误伤正常识鸟。

    A confident species name must still create its folder.
    """
    processor, species = _setup_burst_group(tmp_path, low_confidence=False)
    raw_dict = {p: ".NEF" for p in processor.file_ratings}

    processor._consolidate_burst_groups(raw_dict)

    assert (tmp_path / species).exists(), (
        f"高置信度鸟名没有建出目录 {species}/ "
        f"/ confident species folder is missing"
    )
