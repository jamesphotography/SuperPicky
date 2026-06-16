# -*- coding: utf-8 -*-
"""
CLI 设置解析器（共享）——把命令行参数 + advanced_config 解析成处理设置。

统一解析顺序：**CLI 显式参数 > 配置文件(advanced_config) > 硬编码默认**。
- 可覆盖项的 argparse `default=None`（哨兵）：None 表示「用户未指定」→ 回落到配置文件，
  使「不带参数」时的默认值与 GUI 完全一致（GUI 读的是同一份 advanced_config）。
- 每个设置都有对应 flag → CLI 完全可控（面向 agent 调用）。
- CLI 的覆盖**只改内存里的 config 单例，绝不 .save() 回写**，避免污染用户 GUI 配置。

供 superpicky_cli 的 process / batch / restar 三个命令共用，避免逻辑重复。

Shared CLI settings resolver. Resolution order: explicit CLI arg > config file >
hardcoded default. Overridable args use argparse default=None so "unset" falls back
to advanced_config (matching the GUI defaults). In-memory config only — never saved.
"""

from __future__ import annotations

from typing import Optional

from core.photo_processor import ProcessingSettings
from core.skill_presets import get_skill_level_thresholds


def _arg(args, name: str):
    """取 args 上的可选属性，缺失返回 None。Get optional arg attribute, None if absent."""
    return getattr(args, name, None)


def resolve_processing_settings(args, config) -> ProcessingSettings:
    """
    把 CLI 参数 + advanced_config 解析成 ProcessingSettings（A 通道：经 ProcessingSettings）。

    每个字段：CLI 显式值优先；未指定(None)则回落到配置/skill 预设，与 GUI 默认一致。

    参数:
        args: argparse 命名空间（可覆盖项 default=None）
        config: advanced_config 单例

    返回:
        ProcessingSettings: 解析好的处理设置
    """
    # —— skill 预设驱动 sharpness/aesthetics 默认（与 GUI 一致）——
    skill = _arg(args, "skill_level") or config.skill_level
    preset_sharp, preset_aesth = get_skill_level_thresholds(skill, config)

    sharpness = _arg(args, "sharpness")
    sharpness = preset_sharp if sharpness is None else sharpness

    nima = _arg(args, "nima_threshold")
    nima = preset_aesth if nima is None else nima

    # —— AI 置信度：GUI 里 ai_confidence 与 min_confidence 是同一个旋钮 ——
    #    --confidence 未指定时回落 int(min_confidence*100)
    confidence = _arg(args, "confidence")
    confidence = int(config.min_confidence * 100) if confidence is None else confidence

    # —— 三个检测开关：未指定回落到 config 的复选框状态（GUI 默认均为 False）——
    flight = _arg(args, "flight")
    flight = config.config.get("flight_check", False) if flight is None else flight

    burst = _arg(args, "burst")
    burst = config.config.get("burst_check", False) if burst is None else burst

    exposure = _arg(args, "exposure")
    exposure = config.config.get("exposure_check", False) if exposure is None else exposure

    exposure_threshold = _arg(args, "exposure_threshold")
    exposure_threshold = (
        config.exposure_threshold if exposure_threshold is None else exposure_threshold
    )

    # —— BirdID ——
    use_ebird = _arg(args, "ebird")
    use_ebird = True if use_ebird is None else use_ebird

    birdid_threshold = _arg(args, "birdid_threshold")
    birdid_threshold = (
        config.birdid_confidence if birdid_threshold is None else birdid_threshold
    )

    name_format = _arg(args, "name_format") or config.name_format

    return ProcessingSettings(
        ai_confidence=confidence,
        sharpness_threshold=sharpness,
        nima_threshold=nima,
        normalization_mode="log_compression",
        detect_flight=bool(flight),
        detect_exposure=bool(exposure),
        exposure_threshold=float(exposure_threshold),
        detect_burst=bool(burst),
        auto_identify=bool(_arg(args, "auto_identify") or False),
        save_crop=bool(_arg(args, "save_crop") or False),
        birdid_use_ebird=bool(use_ebird),
        birdid_country_code=_arg(args, "birdid_country") or "",
        birdid_region_code=_arg(args, "birdid_region") or "",
        birdid_confidence_threshold=float(birdid_threshold),
        name_format=name_format,
    )


def apply_config_overrides(args, config) -> None:
    """
    把 CLI 的「B 通道」参数写进**内存** config 单例（引擎从 config 读这些项）。
    **不 .save()**：只影响本次进程，不污染用户 GUI 配置文件。

    覆盖项：folder_layout / metadata_write_mode / arw_write_mode / min_confidence /
    min_sharpness / min_nima / picked_top_percentage / keep_temp_files。
    每项仅在 CLI 显式给出（非 None）时才覆盖；否则保持配置文件原值（= GUI）。

    Apply B-channel CLI overrides onto the in-memory config singleton (read by the
    engine). Never saved — process-local only, no pollution of the user's GUI config.
    """
    c = config.config  # 内存 dict

    # AI 置信度：GUI 里 ai_confidence ←→ min_confidence 同源，--confidence 同步覆盖
    confidence = _arg(args, "confidence")
    if confidence is not None:
        c["min_confidence"] = confidence / 100.0

    # ARW 写入：优先显式 --arw-write-mode；兼容旧 --xmp（True→sidecar）
    arw_mode = _arg(args, "arw_write_mode")
    if arw_mode is not None:
        c["arw_write_mode"] = arw_mode
    elif _arg(args, "xmp") is True:
        c["arw_write_mode"] = "sidecar"

    _set_if(c, "metadata_write_mode", _arg(args, "metadata_mode"))
    _set_if(c, "folder_layout", _arg(args, "folder_layout"))
    _set_if(c, "min_sharpness", _arg(args, "min_sharpness"))
    _set_if(c, "min_nima", _arg(args, "min_nima"))
    _set_if(c, "picked_top_percentage", _arg(args, "picked_top"))

    keep_temp = _arg(args, "keep_temp")
    if keep_temp is not None:
        c["keep_temp_files"] = keep_temp


def _set_if(c: dict, key: str, value) -> None:
    """value 非 None 时写入 dict。Write to dict only when value is not None."""
    if value is not None:
        c[key] = value
