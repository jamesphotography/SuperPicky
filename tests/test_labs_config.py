# -*- coding: utf-8 -*-
"""实验室开关与最近目录组字段 / Lab switches and recent directory groups."""
from advanced_config import AdvancedConfig, get_advanced_config


def test_lab_flags_default_off():
    d = AdvancedConfig.DEFAULT_CONFIG
    assert (
        d["lab_report_export"] is False
        and d["lab_ebird_export"] is False
        and d["lab_multi_folder"] is False
        and d["lab_incremental_cull"] is False
    )


def test_lab_flags_not_preserved_groups_preserved():
    p = AdvancedConfig.PRESERVE_ON_RESET
    assert "recent_directory_groups" in p
    assert not {
        "lab_report_export",
        "lab_ebird_export",
        "lab_multi_folder",
        "lab_incremental_cull",
    } & p


def test_group_dedup_by_set_and_newest_first():
    cfg = get_advanced_config()
    cfg.add_recent_directory_group(["/a", "/b"])
    cfg.add_recent_directory_group(["/c", "/d"])
    cfg.add_recent_directory_group(["/b", "/a"])
    assert cfg.get_recent_directory_groups() == [["/b", "/a"], ["/c", "/d"]]


def test_group_cap_10_and_single_ignored():
    cfg = get_advanced_config()
    cfg.add_recent_directory_group(["/only"])
    for i in range(12):
        cfg.add_recent_directory_group([f"/x{i}", f"/y{i}"])
    groups = cfg.get_recent_directory_groups()
    assert len(groups) == 10 and groups[0] == ["/x11", "/y11"]


def test_reset_turns_labs_off_keeps_groups():
    cfg = get_advanced_config()
    cfg.set_lab_multi_folder(True)
    cfg.set_lab_incremental_cull(True)
    cfg.add_recent_directory_group(["/a", "/b"])
    cfg.reset_to_default()
    assert cfg.lab_multi_folder is False
    assert cfg.lab_incremental_cull is False
    assert cfg.get_recent_directory_groups() == [["/a", "/b"]]


def test_malformed_groups_filtered():
    cfg = get_advanced_config()
    cfg.config["recent_directory_groups"] = [["/a"], "x", ["/a", 3], ["/p", "/q"]]
    assert cfg.get_recent_directory_groups() == [["/p", "/q"]]
