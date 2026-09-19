# -*- coding: utf-8 -*-
"""
已处理照片历史结果查询测试 / Tests for recorded-result lookup.

覆盖 core.processed_lookup 的四条行为约定：
1. 从「鸟种/星级/连拍」子目录里的照片向上找到已处理目录。
2. 取回鸟种/星级等记录，并定位 crop_debug 预览图。
3. 批量模式下控制台日志在父目录，查询要向上找。
4. **只读**：查一次不得改动 report.db 的 schema（老库缺列也不许补）。

Covers walking up to the processed root, reading the record plus its crop
preview, finding the console log at the batch root, and the read-only
guarantee: a lookup must never migrate a legacy report.db.
"""
import os
import sqlite3


def _make_processed_dir(root, filename="DSC00014", species=("青脚鹬", "Common Greenshank")):
    """
    造一个「已处理目录」：report.db + crop_debug 预览图 + 照片已归入鸟种子目录。

    参数 / Parameters:
        root (Path): 目录路径 / Directory to populate.
        filename (str): 不含扩展名的文件名 / Extension-less file name.
        species (tuple): (中文名, 英文名) / Chinese and English species names.

    返回 / Returns:
        str: 归类后的照片绝对路径 / The organised photo's path.

    Build a processed directory fixture: report.db, a crop preview, and the
    photo already moved into its species/rating subfolder.
    """
    from tools.report_db import ReportDB

    crop_rel = os.path.join(".superpicky", "cache", "crop_debug", f"{filename}.jpg")
    crop_abs = root / crop_rel
    crop_abs.parent.mkdir(parents=True, exist_ok=True)
    crop_abs.write_bytes(b"not-a-real-jpeg")

    photo_dir = root / species[0] / "2星_良好"
    photo_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photo_dir / f"{filename}.ARW"
    photo_path.write_bytes(b"raw")

    db = ReportDB(str(root))
    db.insert_photo({
        "filename": filename,
        "has_bird": 1,
        "confidence": 0.85,
        "rating": 2,
        "focus_status": "GOOD",
        "adj_sharpness": 501.3,
        "adj_topiq": 5.4,
        "is_flying": 0,
        "bird_species_cn": species[0],
        "bird_species_en": species[1],
        "birdid_confidence": 90.3,
        "current_path": f"{species[0]}/2星_良好/{filename}.ARW",
        "debug_crop_path": crop_rel,
    })
    db.close()
    return str(photo_path)


def test_find_processed_root_walks_up_from_species_subfolder(tmp_path):
    """处理后的照片在 鸟种/星级/ 子目录里，应能向上找到带 report.db 的目录。
    A photo organised into species/rating subfolders still resolves its root."""
    from core.processed_lookup import find_processed_root

    photo = _make_processed_dir(tmp_path)
    assert find_processed_root(photo) == str(tmp_path)


def test_find_processed_root_returns_none_when_unprocessed(tmp_path):
    """没处理过的目录返回 None，调用方据此退回实时识别。
    An unprocessed directory yields None so callers fall back to live ID."""
    from core.processed_lookup import find_processed_root

    loose = tmp_path / "DSC99999.ARW"
    loose.write_bytes(b"raw")
    assert find_processed_root(str(loose)) is None


def test_lookup_returns_recorded_species_and_crop(tmp_path):
    """按文件名查到鸟种、置信度、星级，并定位到 crop_debug 预览图。
    The lookup returns species, confidence and rating plus the crop preview."""
    from core.processed_lookup import lookup_processed_photo

    photo = _make_processed_dir(tmp_path)
    result = lookup_processed_photo(photo)

    assert result is not None
    assert result.stem == "DSC00014"
    assert result.record["bird_species_cn"] == "青脚鹬"
    assert result.record["rating"] == 2
    assert round(result.record["birdid_confidence"]) == 90
    assert result.crop_path == str(
        tmp_path / ".superpicky" / "cache" / "crop_debug" / "DSC00014.jpg"
    )


def test_lookup_returns_none_when_photo_not_in_db(tmp_path):
    """目录处理过但库里没有这张照片时返回 None（例如用户拖进来一张新照片）。
    A processed directory without a row for this photo still yields None."""
    from core.processed_lookup import lookup_processed_photo

    _make_processed_dir(tmp_path)
    stranger = tmp_path / "DSC77777.ARW"
    stranger.write_bytes(b"raw")
    assert lookup_processed_photo(str(stranger)) is None


def test_crop_path_falls_back_to_conventional_location(tmp_path):
    """库里没记 debug_crop_path（老库/写库失败）时，按约定路径兜底找预览图。
    With no recorded crop path, fall back to the conventional location."""
    from core.processed_lookup import resolve_crop_path

    crop = tmp_path / ".superpicky" / "cache" / "crop_debug" / "DSC1.jpg"
    crop.parent.mkdir(parents=True, exist_ok=True)
    crop.write_bytes(b"x")

    assert resolve_crop_path(str(tmp_path), {"debug_crop_path": None}, "DSC1") == str(crop)
    assert resolve_crop_path(str(tmp_path), None, "DSC_missing") is None


def test_find_log_entries_walks_up_to_batch_root(tmp_path):
    """批量模式下控制台日志写在用户选中的父目录，查询必须向上找到它。
    In batch mode the console log lives in the selected parent directory."""
    from core.processed_lookup import find_log_entries

    batch_root = tmp_path / "RAW"
    sub_dir = batch_root / "10160919"
    sub_dir.mkdir(parents=True)

    # 子目录那份只有 photo_processor 直接写的调试细节，不含逐张结果行
    (sub_dir / "superpicky.log").write_text(
        "[2026-09-19 19:45:00]   补救扫描: 高分辨率重扫检出鸟 (置信度 0.51)\n",
        encoding="utf-8",
    )
    (batch_root / "superpicky.log").write_text(
        "[2026-09-19 20:08:00] [014/1639] DSC00014.jpg | 2★ (良好) | 711ms\n"
        "[2026-09-19 20:08:01]   🐦 Bird ID [DSC00014.ARW]: 青脚鹬 (90%)\n"
        "[2026-09-19 20:08:02] [015/1639] DSC00015.jpg | 0★ (锐度太低) | 300ms\n",
        encoding="utf-8",
    )

    lines = find_log_entries(str(sub_dir), "DSC00014")
    assert len(lines) == 2
    assert lines[0].startswith("[014/1639] DSC00014.jpg")
    assert "青脚鹬" in lines[1]


def test_lookup_does_not_migrate_legacy_schema(tmp_path):
    """只读约定：查一次老库不得补列、不得改 schema（用户只是想看一眼）。
    Read-only guarantee: peeking at a legacy report.db must not add columns."""
    from core.processed_lookup import load_photo_record

    db_dir = tmp_path / ".superpicky"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "report.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE photos (id INTEGER PRIMARY KEY, filename TEXT, "
        "bird_species_cn TEXT, rating INTEGER)"
    )
    conn.execute(
        "INSERT INTO photos (filename, bird_species_cn, rating) VALUES (?, ?, ?)",
        ("DSC1", "勺嘴鹬", 3),
    )
    conn.commit()
    conn.close()

    before = _column_names(str(db_path))
    record = load_photo_record(str(tmp_path), "DSC1")
    after = _column_names(str(db_path))

    assert record is not None and record["bird_species_cn"] == "勺嘴鹬"
    assert before == after, "只读查询不应改动老库 schema / lookup must not migrate"


def _column_names(db_path):
    """读出 photos 表的列名清单 / List the photos table's column names."""
    conn = sqlite3.connect(db_path)
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(photos)")]
    finally:
        conn.close()
