# -*- coding: utf-8 -*-
"""
增量选片 (#161) 端到端回归测试：
覆盖增量根目录扫描、#126 格式筛选、同前缀冲突保护、0 字节占位库兼容、
V2 历史参考池与鸟种保底跨批一致性、连拍编号偏移、Manifest 增量合并、
局部临时缓存清理、PhotoProcessor 增量模式、Picked 计算、断点续处理、
FolderQueue 状态分类以及 MainWindow UI 防护。

Regression tests for Incremental Culling (#161): root delta scanning, #126
format filtering, same-stem conflict protection, 0-byte placeholder DB safety,
V2 cross-batch quota & species floor context, burst ID offset, incremental
manifest merge, scoped temp-file cleanup, PhotoProcessor incremental runs,
picked threshold context, resume+incremental, FolderQueue, and MainWindow state.
"""
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.folder_queue import (  # noqa: E402
    STATE_INCREMENTAL,
    STATE_PROCESSED,
    FolderQueue,
    classify_folder,
)
from core.incremental_cull import (  # noqa: E402
    find_max_burst_id,
    inspect_directory_delta,
    load_historical_v2_context,
    merge_incremental_manifest,
)
from core.photo_processor import (  # noqa: E402
    PhotoProcessor,
    ProcessingCallbacks,
    ProcessingSettings,
)
from core.rating_quota import PhotoMetricsV2, assign_ratings  # noqa: E402
from tools.report_db import ReportDB  # noqa: E402
from tools.resume_state import ResumeStateManager  # noqa: E402


class _DummyExifToolManager:
    """Offline ExifTool stub for incremental processor unit tests."""

    def open_persistent_session(self, *args, **kwargs) -> None:
        pass

    def close_persistent_session(self, *args, **kwargs) -> None:
        pass

    def flush_batch(self, *args, **kwargs) -> None:
        pass

    def queue_metadata(self, *args, **kwargs) -> None:
        pass


def _make_photo_v2(
    key: str,
    sharp: float = 500.0,
    topiq: float = 5.0,
    species: str = "白鹭",
    **kw,
) -> PhotoMetricsV2:
    """构造 V2 评分指标测试样本。/ Build a PhotoMetricsV2 test fixture."""
    defaults = dict(
        detected=True,
        confidence=0.9,
        norm_sharpness=sharp,
        topiq=topiq,
        best_eye=0.9,
        beak_vis=0.9,
        species=species,
        species_confirmed=bool(species),
    )
    defaults.update(kw)
    return PhotoMetricsV2(key=key, **defaults)


def _mw_scan(directory: str):
    """
    构造单目录预扫描结果（替代 _prescan，避免测试依赖真实照片解码）。

    Build a one-folder pre-scan result in place of _prescan.
    """
    from core.recursive_scanner import ScannedDirectory

    return [ScannedDirectory(path=os.path.abspath(directory), depth=0,
                             photo_count=1, video_count=0, video_files=())]


def _seed_photo_row(
    db: ReportDB,
    filename: str,
    *,
    rating: int = 3,
    picked: int = 0,
    burst_id=None,
    species_cn: str = "东方白鹳",
    species_en: str = "Oriental Stork",
    orig_rel: str = "",
    curr_rel: str = "",
    temp_jpeg_path: str = "",
    debug_crop_path: str = "",
    sharpness: float = 650.0,
    topiq: float = 6.2,
    adj_sharpness: float = 650.0,
    adj_topiq: float = 6.2,
) -> None:
    """向 ReportDB 写入一条历史照片记录。/ Insert a historical photo row into ReportDB."""
    db.insert_photo(
        {
            "filename": filename,
            "original_path": orig_rel or f"{filename}.NEF",
            "current_path": curr_rel or f"{filename}.NEF",
            "has_bird": 1 if rating >= 0 else 0,
            "confidence": 0.92,
            "bird_bbox": [10, 10, 100, 100],
            "head_detected": 1,
            "head_bbox": [20, 20, 50, 50],
            "left_eye": 0.95,
            "right_eye": 0.90,
            "beak": 0.90,
            "head_sharp": sharpness,
            "nima_score": topiq,
            "adj_sharpness": adj_sharpness,
            "adj_topiq": adj_topiq,
            "rating": rating,
            "picked": picked,
            "is_flying": 0,
            "focus_status": "BEST",
            "bird_species_cn": species_cn,
            "bird_species_en": species_en,
            "burst_id": burst_id,
            "temp_jpeg_path": temp_jpeg_path,
            "debug_crop_path": debug_crop_path,
        }
    )


class TestRootDeltaInspection:
    def test_dummy_zero_byte_db_is_not_valid_incremental(self, tmp_path):
        """0 字节占位 report.db 不触发增量过滤，兼容已有队列测试约定。"""
        sp_dir = tmp_path / ".superpicky"
        sp_dir.mkdir()
        (sp_dir / "report.db").write_bytes(b"")
        (tmp_path / "IMG_001.NEF").write_bytes(b"raw")

        delta = inspect_directory_delta(
            str(tmp_path),
            allowed_exts=frozenset({".nef", ".jpg"}),
            folder_layout="rating_first",
            video_auto_process=False,
        )
        assert delta.has_report_db is True
        assert delta.has_valid_db is False
        assert delta.new_count == 1
        assert delta.has_new_work is True

    def test_filters_existing_respects_format_filter_and_same_stem_conflict(self, tmp_path):
        """只筛出未处理前缀，遵守 #126 格式过滤，同前缀不同扩展名记冲突不覆盖。"""
        db = ReportDB(str(tmp_path))
        _seed_photo_row(
            db,
            "IMG_OLD_FLAT",
            orig_rel="IMG_OLD_FLAT.NEF",
            curr_rel="IMG_OLD_FLAT.NEF",
        )
        _seed_photo_row(
            db,
            "IMG_OLD_MOVED",
            orig_rel="IMG_OLD_MOVED.NEF",
            curr_rel="3星_精选/IMG_OLD_MOVED.NEF",
        )
        db.close()

        (tmp_path / "IMG_OLD_FLAT.NEF").write_bytes(b"old_flat")
        (tmp_path / "IMG_OLD_MOVED.JPG").write_bytes(b"conflict_jpg")
        (tmp_path / "IMG_NEW.NEF").write_bytes(b"new_raw")
        (tmp_path / "IMG_NEW_JPG_ONLY.JPG").write_bytes(b"filtered_out_jpg")

        # 仅允许 RAW：IMG_NEW_JPG_ONLY.JPG 应被 #126 过滤；IMG_OLD_MOVED.JPG 同前缀亦被拒收
        delta = inspect_directory_delta(
            str(tmp_path),
            allowed_exts=frozenset({".nef"}),
            folder_layout="rating_first",
            video_auto_process=False,
        )
        assert delta.has_valid_db is True
        assert delta.existing_count == 2
        assert delta.new_count == 1
        assert set(delta.new_raw_dict.keys()) == {"IMG_NEW"}
        assert delta.conflict_same_stem == []

        # 同时允许 RAW + JPG：IMG_OLD_MOVED.JPG 成为同前缀冲突，仍不重算覆盖
        delta_all = inspect_directory_delta(
            str(tmp_path),
            allowed_exts=frozenset({".nef", ".jpg"}),
            folder_layout="rating_first",
            video_auto_process=False,
        )
        assert delta_all.new_count == 2
        assert set(delta_all.new_raw_dict.keys()) == {"IMG_NEW"}
        assert set(delta_all.new_jpg_dict.keys()) == {"IMG_NEW_JPG_ONLY"}
        assert delta_all.conflict_same_stem == ["IMG_OLD_MOVED.JPG"]


class TestV2IncrementalRatingAndFloor:
    def test_small_afternoon_batch_uses_historical_reference_and_existing_floor(self):
        """
        历史批有 20 张高分白鹭；下午新增 1 张中等偏弱白鹭 + 1 张全新罕见鸟。
        增量池重用历史分布：白鹭不因为下午只有 1 张就白拿 3★ 或 2★ 保底；
        新鸟种仍享受鸟种保底；且返回字典只含新增照片键。
        """
        history = [
            _make_photo_v2(
                f"old_{i:02d}",
                sharp=800.0 - i * 10.0,
                topiq=6.5 - i * 0.03,
                species="白鹭",
            )
            for i in range(20)
        ]
        new_heron = _make_photo_v2("new_heron", sharp=300.0, topiq=4.2, species="白鹭")
        new_stork = _make_photo_v2(
            "new_stork",
            sharp=300.0,
            topiq=4.2,
            species="黑鹳",
        )

        # 不传历史参考时的反例：两张全被切成高星
        isolated = assign_ratings([new_heron, new_stork], quota3=20, quota2=25)
        assert isolated["new_heron"].rating == 3

        # 传入历史参考池 + 已有 2★+ 鸟种集合
        res = assign_ratings(
            [new_heron, new_stork],
            quota3=20,
            quota2=25,
            reference_photos=history,
            existing_floor_species={"白鹭"},
        )
        assert set(res.keys()) == {"new_heron", "new_stork"}
        assert res["new_heron"].rating == 1
        assert res["new_stork"].rating >= 2

    def test_load_historical_v2_context_extracts_floor_species_and_star3_scores(self, tmp_path):
        db = ReportDB(str(tmp_path))
        _seed_photo_row(
            db,
            "H1",
            rating=3,
            species_cn="白鹭",
            species_en="Little Egret",
            sharpness=700.0,
            topiq=6.4,
            adj_sharpness=710.0,
            adj_topiq=6.5,
        )
        _seed_photo_row(
            db,
            "H2",
            rating=1,
            species_cn="麻雀",
            species_en="Tree Sparrow",
            sharpness=310.0,
            topiq=4.1,
            adj_sharpness=310.0,
            adj_topiq=4.1,
        )
        ctx = load_historical_v2_context(db)

        assert len(ctx.reference_photos) == 2
        assert ctx.existing_floor_species == {"Little Egret"}
        assert ctx.existing_star_3_scores == [(6.5, 710.0)]

        # 排除当批前缀时（模拟插库后兜底加载），绝不把本批新图重复计入历史参考池
        ctx_excl = load_historical_v2_context(db, exclude_prefixes={"H1"})
        assert [p.key for p in ctx_excl.reference_photos] == ["__hist__:H2"]
        assert ctx_excl.existing_floor_species == set()
        assert ctx_excl.existing_star_3_scores == []
        db.close()


class TestBurstOffsetManifestAndTempCleanup:
    def test_find_max_burst_id_reads_folders_and_db(self, tmp_path):
        (tmp_path / "3星_精选" / "burst_002").mkdir(parents=True)
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "P1", burst_id=4)
        db.close()

        assert find_max_burst_id(str(tmp_path)) == 4

    def test_merge_incremental_manifest_preserves_previous_entries(self, tmp_path):
        m1 = merge_incremental_manifest(
            str(tmp_path),
            new_files=[{"filename": "A.NEF", "target": "3星_精选/A.NEF"}],
            new_temp_jpegs=[".superpicky/cache/A.jpg"],
            folder_structure=["3星_精选", "2星_良好"],
            moved_delta=1,
        )
        assert m1["stats"]["total_moved"] == 1

        m2 = merge_incremental_manifest(
            str(tmp_path),
            new_files=[{"filename": "B.NEF", "target": "2星_良好/B.NEF"}],
            new_temp_jpegs=[".superpicky/cache/B.jpg"],
            folder_structure=["3星_精选", "2星_良好"],
            moved_delta=1,
        )
        assert [e["filename"] for e in m2["files"]] == ["A.NEF", "B.NEF"]
        assert m2["temp_jpegs"] == [".superpicky/cache/A.jpg", ".superpicky/cache/B.jpg"]
        assert m2["stats"]["total_moved"] == 2

    def test_clear_cache_paths_for_prefixes_only_clears_current_batch(self, tmp_path):
        db = ReportDB(str(tmp_path))
        _seed_photo_row(
            db,
            "OLD",
            temp_jpeg_path=".superpicky/cache/OLD.jpg",
            debug_crop_path=".superpicky/cache/OLD_crop.jpg",
        )
        _seed_photo_row(
            db,
            "NEW",
            temp_jpeg_path=".superpicky/cache/NEW.jpg",
            debug_crop_path=".superpicky/cache/NEW_crop.jpg",
        )
        cleared = db.clear_cache_paths_for_prefixes(["NEW"])
        old_row = db.get_photo("OLD")
        new_row = db.get_photo("NEW")
        db.close()

        assert cleared == 1
        assert old_row["temp_jpeg_path"] == ".superpicky/cache/OLD.jpg"
        assert old_row["debug_crop_path"] == ".superpicky/cache/OLD_crop.jpg"
        assert new_row["temp_jpeg_path"] is None
        assert new_row["debug_crop_path"] is None


class TestPhotoProcessorIncrementalE2E:
    def test_incremental_preserves_old_records_offsets_bursts_merges_manifest_and_cleans_only_new_temp(
        self, tmp_path, monkeypatch
    ):
        """
        端到端验证 PhotoProcessor.process(incremental=True)：
        1. 保留历史库中手工改过星级/鸟种的记录与历史预览文件；
        2. 新增两张连拍仅进入新偏移连拍号 (burst_002)；
        3. manifest 增量合并而非覆盖；
        4. 第二次重复调用无新文件时直接 0-op 返回，不重跑 AI。
        """
        old_dir = tmp_path / "3星_精选" / "burst_001"
        old_dir.mkdir(parents=True)
        (old_dir / "IMG_OLD.NEF").write_bytes(b"old_raw")
        cache_dir = tmp_path / ".superpicky" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "IMG_OLD.jpg").write_bytes(b"old_cache_jpg")
        (cache_dir / "IMG_OLD_crop.jpg").write_bytes(b"old_crop_jpg")

        db = ReportDB(str(tmp_path))
        _seed_photo_row(
            db,
            "IMG_OLD",
            rating=3,
            picked=1,
            burst_id=1,
            species_cn="白鹭",
            species_en="Little Egret",
            orig_rel="IMG_OLD.NEF",
            curr_rel="3星_精选/burst_001/IMG_OLD.NEF",
            temp_jpeg_path=".superpicky/cache/IMG_OLD.jpg",
            debug_crop_path=".superpicky/cache/IMG_OLD_crop.jpg",
        )
        db.close()

        (tmp_path / ".superpicky_manifest.json").write_text(
            json.dumps(
                {
                    "version": "2.0",
                    "files": [
                        {
                            "filename": "IMG_OLD.NEF",
                            "source": "IMG_OLD.NEF",
                            "target": "3星_精选/burst_001/IMG_OLD.NEF",
                            "rating": 3,
                        }
                    ],
                    "temp_jpegs": [".superpicky/cache/IMG_OLD.jpg"],
                    "stats": {"total_moved": 1},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 下午新增 4 张连拍照片到根目录（>=4 张才会触发 burst_NNN 子目录归并）
        new_stems = ["IMG_NEW1", "IMG_NEW2", "IMG_NEW3", "IMG_NEW4"]
        for s in new_stems:
            (tmp_path / f"{s}.NEF").write_bytes(s.encode("ascii"))

        logs: List[Tuple[str, str]] = []
        settings = ProcessingSettings(
            detect_burst=True,
            auto_identify=False,
            save_crop=False,
        )
        with patch("core.photo_processor.get_advanced_config") as mock_cfg:
            mock_cfg.return_value.folder_layout = "rating_first"
            mock_cfg.return_value.burst_group_folders = True
            mock_cfg.return_value.keep_temp_files = False
            mock_cfg.return_value.get_metadata_write_mode.return_value = "none"
            mock_cfg.return_value.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})

            proc = PhotoProcessor(
                str(tmp_path),
                settings,
                ProcessingCallbacks(log=lambda m, lvl="info": logs.append((lvl, m))),
            )

            def fake_convert(raw_files_to_convert, files_tbr):
                for item in raw_files_to_convert:
                    p = item[0] if isinstance(item, tuple) else item
                    f = cache_dir / f"{p}.jpg"
                    f.write_bytes(b"new_cache")
                    proc.temp_converted_jpegs.add(f".superpicky/cache/{p}.jpg")
                    if f"{p}.jpg" not in files_tbr:
                        files_tbr.append(f"{p}.jpg")
                proc.file_meta = {}

            def fake_process_images(files_tbr, raw_dict, **kwargs):
                if proc.report_db is None:
                    proc.report_db = ReportDB(proc.dir_path)
                for name in files_tbr:
                    stem = os.path.splitext(name)[0]
                    proc.file_ratings[stem] = 2
                    proc.file_bird_species[stem] = {
                        "cn_name": "白鹭",
                        "en_name": "Little Egret",
                        "low_confidence": False,
                    }
                    proc.report_db.insert_photo(
                        {
                            "filename": stem,
                            "original_path": f"{stem}.NEF",
                            "current_path": f"{stem}.NEF",
                            "has_bird": 1,
                            "confidence": 0.9,
                            "left_eye": 0.9,
                            "right_eye": 0.9,
                            "beak": 0.9,
                            "head_sharp": 620.0,
                            "nima_score": 6.0,
                            "adj_sharpness": 620.0,
                            "adj_topiq": 6.0,
                            "rating": 2,
                            "burst_id": proc.burst_map.get(stem),
                            "focus_status": "BEST",
                            "bird_species_cn": "白鹭",
                            "bird_species_en": "Little Egret",
                            "temp_jpeg_path": f".superpicky/cache/{stem}.jpg",
                        }
                    )
                proc.stats["total"] = len(files_tbr)
                proc.stats["star_2"] = len(files_tbr)

            def fake_detect_bursts_early(raw_dict):
                # 连拍器返回局部编号 1，增量模式须自动偏移到 > existing max (1 -> 2)
                proc.stats["burst_groups"] = 1
                return {os.path.join(str(tmp_path), k + ext): 1 for k, ext in raw_dict.items()}

            monkeypatch.setattr(proc, "_convert_raws", fake_convert)
            monkeypatch.setattr(proc, "_detect_bursts_early", fake_detect_bursts_early)
            monkeypatch.setattr(proc, "_process_images", fake_process_images)
            monkeypatch.setattr(proc, "_calculate_picked_flags", lambda: None)
            monkeypatch.setattr(
                "core.photo_processor.get_exiftool_manager",
                lambda: _DummyExifToolManager(),
            )
            res = proc.process(organize_files=True, cleanup_temp=True, incremental=True)

        assert res.stats["total"] == 4

        check_db = ReportDB(str(tmp_path))
        old_row = check_db.get_photo("IMG_OLD")
        new1_row = check_db.get_photo("IMG_NEW1")
        new4_row = check_db.get_photo("IMG_NEW4")
        check_db.close()

        # 历史记录完整保留，旧缓存未误删
        assert old_row["rating"] == 3
        assert old_row["picked"] == 1
        assert old_row["burst_id"] == 1
        assert old_row["temp_jpeg_path"] == ".superpicky/cache/IMG_OLD.jpg"
        assert (cache_dir / "IMG_OLD.jpg").exists()

        # 新增连拍偏移为 burst_002（绝不合并进 burst_001），且新临时 JPEG 被清理
        assert "burst_002" in new1_row["current_path"]
        assert "burst_002" in new4_row["current_path"]
        assert new1_row["temp_jpeg_path"] is None
        assert not (cache_dir / "IMG_NEW1.jpg").exists()
        assert (tmp_path / new1_row["current_path"]).exists()

        # Manifest 增量保留 IMG_OLD 且包含新增条目
        manifest = json.loads((tmp_path / ".superpicky_manifest.json").read_text(encoding="utf-8"))
        manifest_names = [f["filename"] for f in manifest["files"]]
        assert "IMG_OLD.NEF" in manifest_names
        assert "IMG_NEW1.NEF" in manifest_names
        assert manifest["stats"]["total_moved"] == 5

        # 第二次增量运行（根目录已无新文件）：0-op，不调用 AI
        ai_called = []
        with patch("core.photo_processor.get_advanced_config") as mock_cfg2:
            mock_cfg2.return_value.folder_layout = "rating_first"
            mock_cfg2.return_value.burst_group_folders = True
            mock_cfg2.return_value.keep_temp_files = False
            mock_cfg2.return_value.get_metadata_write_mode.return_value = "none"
            mock_cfg2.return_value.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})
            proc2 = PhotoProcessor(str(tmp_path), settings, ProcessingCallbacks())
            monkeypatch.setattr(proc2, "_process_images", lambda *a, **kw: ai_called.append(a))
            res2 = proc2.process(organize_files=True, cleanup_temp=True, incremental=True)
        assert res2.stats["total"] == 0
        assert ai_called == []

    def test_picked_uses_historical_star3_scores_and_skips_historical_rows(self, tmp_path, monkeypatch):
        """
        历史 3★ 分数远高于下午唯一 1 张 3★：下午这 1 张不应单独成王 (`picked=1`)，
        且历史 3★ 的 `picked` 标志不可被改写。
        """
        db = ReportDB(str(tmp_path))
        for i in range(10):
            _seed_photo_row(
                db,
                f"OLD3_{i}",
                rating=3,
                picked=1 if i == 0 else 0,
                sharpness=900.0,
                topiq=7.5,
                adj_sharpness=900.0,
                adj_topiq=7.5,
            )
        _seed_photo_row(
            db,
            "NEW3_WEAK",
            rating=3,
            picked=0,
            orig_rel="NEW3_WEAK.NEF",
            curr_rel="NEW3_WEAK.NEF",
            sharpness=360.0,
            topiq=4.8,
            adj_sharpness=360.0,
            adj_topiq=4.8,
        )

        proc = PhotoProcessor(str(tmp_path), ProcessingSettings(), ProcessingCallbacks())
        proc.report_db = db
        proc._incremental_mode = True
        ctx = load_historical_v2_context(db)
        ctx.existing_star_3_scores = [(7.5, 900.0)] * 10
        proc._historical_v2_ctx = ctx
        proc.star_3_photos = [{"file": "NEW3_WEAK", "nima": 4.8, "sharpness": 360.0}]
        proc.raw_dict = {"NEW3_WEAK": ".NEF"}
        proc.file_meta = {}

        class _DummyET:
            def queue_metadata(self, *args, **kwargs):
                pass

        monkeypatch.setattr("core.photo_processor.get_exiftool_manager", lambda: _DummyET())
        proc._calculate_picked_flags()

        assert db.get_photo("NEW3_WEAK")["picked"] == 0
        assert db.get_photo("OLD3_0")["picked"] == 1
        assert db.get_photo("OLD3_1")["picked"] == 0
        db.close()


class TestFolderQueueAndMainWindowIncremental:
    def test_folder_queue_classifies_incremental_and_respects_reprocess_all(self, tmp_path):
        done_dir = tmp_path / "done_only"
        done_dir.mkdir()
        db1 = ReportDB(str(done_dir))
        _seed_photo_row(db1, "A1", orig_rel="A1.NEF", curr_rel="3星_精选/A1.NEF")
        db1.close()

        inc_dir = tmp_path / "has_new"
        inc_dir.mkdir()
        db2 = ReportDB(str(inc_dir))
        _seed_photo_row(db2, "B1", orig_rel="B1.NEF", curr_rel="3星_精选/B1.NEF")
        db2.close()
        (inc_dir / "B2.NEF").write_bytes(b"new_photo")

        assert classify_folder(str(done_dir)) == STATE_PROCESSED
        # 默认实验室开关关闭时，增量目录也按已处理看待（避免误跑实验流程）
        assert classify_folder(str(inc_dir)) == STATE_PROCESSED
        q_off = FolderQueue([str(done_dir), str(inc_dir)], reprocess_all=False)
        assert q_off.plan == [
            (str(done_dir), "skip"),
            (str(inc_dir), "skip"),
        ]

        # 开启实验室增量开关后才识别为 STATE_INCREMENTAL
        with patch("advanced_config.get_advanced_config") as mock_cfg:
            mock_cfg.return_value.lab_incremental_cull = True
            mock_cfg.return_value.folder_layout = "rating_first"
            mock_cfg.return_value.video_auto_process_in_main = False
            mock_cfg.return_value.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})
            assert classify_folder(str(done_dir)) == STATE_PROCESSED
            assert classify_folder(str(inc_dir)) == STATE_INCREMENTAL

            q_normal = FolderQueue([str(done_dir), str(inc_dir)], reprocess_all=False)
            assert q_normal.plan == [
                (str(done_dir), "skip"),
                (str(inc_dir), "incremental"),
            ]

            q_reprocess = FolderQueue([str(done_dir), str(inc_dir)], reprocess_all=True)
            assert q_reprocess.plan == [
                (str(done_dir), "reprocess"),
                (str(inc_dir), "reprocess"),
            ]

    def test_check_report_csv_incremental_does_not_auto_open_browser(self, tmp_path):
        """开启实验室增量开关时显示增量按钮且不自动弹出结果浏览器；关闭时维持常规已处理状态。"""
        from ui.main_window import SuperPickyMainWindow

        _app = QApplication.instance() or QApplication([])
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="3星_精选/OLD1.NEF")
        db.close()
        (tmp_path / "NEW1.NEF").write_bytes(b"new")

        w = SuperPickyMainWindow()
        w.directory_path = str(tmp_path)

        opened = []
        w._auto_open_results = lambda *a, **kw: opened.append(True)
        with patch("advanced_config.get_advanced_config") as mock_cfg:
            mock_cfg.return_value.keep_temp_files = True
            mock_cfg.return_value.folder_layout = "rating_first"
            mock_cfg.return_value.video_auto_process_in_main = False
            mock_cfg.return_value.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})

            # 开关关闭：不显示 (+1)
            mock_cfg.return_value.lab_incremental_cull = False
            w._check_report_csv(auto_open=False)
            assert "(+1)" not in w.start_btn.text()

            # 开关开启：显示 (+1) 且 auto_open=True 也不自动弹结果浏览器
            mock_cfg.return_value.lab_incremental_cull = True
            w._check_report_csv(auto_open=True)

        assert opened == []
        assert w.start_btn.isEnabled() is True
        assert "(+1)" in w.start_btn.text()
        assert not w.view_results_btn.isHidden()

    def test_same_dir_reselection_and_window_activate_refresh_incremental(self, tmp_path):
        """已选目录后外部新增照片：重选同目录或窗口切回前台 (WindowActivate) 会立即刷新 (+N)。"""
        from PySide6.QtCore import QEvent
        from advanced_config import get_advanced_config
        from ui.main_window import SuperPickyMainWindow

        _app = QApplication.instance() or QApplication([])
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="3星_精选/OLD1.NEF")
        db.close()

        cfg = get_advanced_config()
        old_flag = cfg.lab_incremental_cull
        old_fmt = cfg.format_filter_mode
        cfg.set_lab_incremental_cull(True)
        cfg.config["format_filter_mode"] = "all"
        try:
            w = SuperPickyMainWindow()
            w._auto_open_results = lambda *a, **kw: None
            w._handle_directory_selection(str(tmp_path))
            assert "(+" not in w.start_btn.text()

            # 外部复制第 1 张新照片后重新选中同一个目录
            (tmp_path / "NEW1.NEF").write_bytes(b"new1")
            w._handle_directory_selection(str(tmp_path))
            assert "(+1)" in w.start_btn.text()

            # 外部再复制第 2 张新照片后模拟切回主窗口 (WindowActivate)
            (tmp_path / "NEW2.NEF").write_bytes(b"new2")
            w.changeEvent(QEvent(QEvent.Type.WindowActivate))
            assert "(+2)" in w.start_btn.text()
        finally:
            cfg.set_lab_incremental_cull(old_flag)
            cfg.config["format_filter_mode"] = old_fmt

    def test_start_processing_incremental_choice_dialog(self, tmp_path):
        """实验室增量开启且检测到新文件时，弹窗提供 增量/重选/查看结果/取消 4 种选择。"""
        from advanced_config import get_advanced_config
        from ui import custom_dialogs as _cd
        from ui import main_window as _mw
        from ui.main_window import SuperPickyMainWindow

        _app = QApplication.instance() or QApplication([])
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="3星_精选/OLD1.NEF")
        db.close()
        (tmp_path / "NEW1.NEF").write_bytes(b"new1")

        w = SuperPickyMainWindow()
        w.directory_path = str(tmp_path)
        w._require_initialization_for_processing = lambda: True
        w._is_removable_source = lambda: False
        w._confirm_start = lambda: True
        w._is_dangerous_root_check = False

        launched = []
        opened = []
        restored = []
        w._launch_worker = lambda d, r, sr, clear_log=True: launched.append(
            (d, r, sr[0].photo_count if sr else 0, w._pending_worker_incremental)
        )
        w._open_results_smart = lambda: opened.append(True)
        w._quick_restore_directory = lambda: restored.append(True)

        seen_titles = []

        class _ChoiceDlg:
            next_choice = "cancel"

            def __init__(self, parent, title="", message="", buttons=None, links=None, default_key=None):
                seen_titles.append((title, [b[0] for b in (buttons or [])], [lk[0] for lk in (links or [])]))
                self.choice = None

            def exec(self):
                self.choice = _ChoiceDlg.next_choice
                return 0

        cfg = get_advanced_config()
        old_inc = cfg.lab_incremental_cull
        old_country = cfg.birdid_country_code
        old_auto = cfg.birdid_auto_identify
        old_fmt = cfg.format_filter_mode
        cfg.set_lab_incremental_cull(True)
        cfg.config["format_filter_mode"] = "all"
        cfg.config["birdid_auto_identify"] = False
        try:
            with (
                patch.object(_cd, "DeleteConfirmDialog", _ChoiceDlg),
                patch("tools.exiftool_manager.get_exiftool_manager"),
                patch("core.recursive_scanner.is_dangerous_root", return_value=(False, "")),
            ):
                _ChoiceDlg.next_choice = "cancel"
                w._start_processing()
                assert launched == [] and opened == [] and restored == []

                _ChoiceDlg.next_choice = "view"
                w._start_processing()
                assert opened == [True] and launched == [] and restored == []

                _ChoiceDlg.next_choice = "reprocess"
                with patch.object(_mw.StyledMessageBox, "question",
                                  return_value=_mw.StyledMessageBox.Yes):
                    w._start_processing()
                assert restored == [True] and launched == []
                assert w._reprocess_after_reset is True
                w._reprocess_after_reset = False
                w._suppress_results_browser_once = False

                _ChoiceDlg.next_choice = "incremental"
                w._start_processing()
                assert launched == [(str(tmp_path), False, 1, True)]

                assert all("incremental" in btns and "reprocess" in btns and "view" in lks
                           for _t, btns, lks in seen_titles)

                # 关闭增量开关后，有新图也不触发增量流程，而给实验室提示对话框
                launched.clear()
                cfg.set_lab_incremental_cull(False)
                seen_titles.clear()
                _ChoiceDlg.next_choice = "view"
                w._start_processing()
                assert launched == []
                assert "incremental" not in seen_titles[0][1]
        finally:
            cfg.set_lab_incremental_cull(old_inc)
            cfg.config["birdid_country_code"] = old_country
            cfg.config["birdid_auto_identify"] = old_auto
            cfg.config["format_filter_mode"] = old_fmt

    def test_start_after_reset_skips_check_and_forces_full_cull(self, tmp_path):
        """复原后那一次开始：快速复原保留了 report.db，必须跳过拦截且强制全量（即使实验室增量开启
        且根目录有新文件），否则会死循环在「已完成选片」或误走增量。标记只生效一次。"""
        from advanced_config import get_advanced_config
        from ui import custom_dialogs as _cd
        from ui.main_window import SuperPickyMainWindow

        _app = QApplication.instance() or QApplication([])
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="OLD1.NEF")
        db.close()
        (tmp_path / "OLD1.NEF").write_bytes(b"old")
        (tmp_path / "NEW1.NEF").write_bytes(b"new1")

        w = SuperPickyMainWindow()
        w.directory_path = str(tmp_path)
        w._require_initialization_for_processing = lambda: True
        w._is_removable_source = lambda: False
        w._confirm_start = lambda: True
        launched = []
        w._launch_worker = lambda d, r, sr, clear_log=True: launched.append(
            w._pending_worker_incremental)
        w._prescan = lambda d, warn=True: _mw_scan(d)

        dialogs = []

        class _Dlg:
            def __init__(self, *a, **kw):
                dialogs.append(kw.get("title"))
                self.choice = "cancel"

            def exec(self):
                return 0

        cfg = get_advanced_config()
        old = (cfg.lab_incremental_cull, cfg.birdid_auto_identify, cfg.format_filter_mode)
        cfg.set_lab_incremental_cull(True)
        cfg.config["birdid_auto_identify"] = False
        cfg.config["format_filter_mode"] = "all"
        try:
            with (
                patch.object(_cd, "DeleteConfirmDialog", _Dlg),
                patch("tools.exiftool_manager.get_exiftool_manager"),
            ):
                w._skip_processed_check_once = True
                w._start_processing()
                assert dialogs == []
                assert launched == [False]
                assert w._skip_processed_check_once is False

                # 第二次（标记已消费）：照常拦截
                w._start_processing()
                assert len(dialogs) == 1 and launched == [False]
        finally:
            cfg.set_lab_incremental_cull(old[0])
            cfg.config["birdid_auto_identify"] = old[1]
            cfg.config["format_filter_mode"] = old[2]

    def test_resume_is_incremental_only_when_lab_switch_on(self, tmp_path):
        """断点续跑：实验室增量关闭时保持原行为（非增量），开启时才走增量。"""
        from advanced_config import get_advanced_config
        from ui.main_window import SuperPickyMainWindow

        _app = QApplication.instance() or QApplication([])
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "DONE1", orig_rel="DONE1.NEF", curr_rel="DONE1.NEF")
        db.close()
        (tmp_path / "DONE1.NEF").write_bytes(b"done")
        (tmp_path / "TODO1.NEF").write_bytes(b"todo")
        assert ResumeStateManager(str(tmp_path)).start(["TODO1"]) is True

        w = SuperPickyMainWindow()
        w.directory_path = str(tmp_path)
        w._require_initialization_for_processing = lambda: True
        w._is_removable_source = lambda: False
        w._confirm_start = lambda: True
        launched = []
        w._launch_worker = lambda d, r, sr, clear_log=True: launched.append(
            (r, w._pending_worker_incremental))
        w._prescan = lambda d, warn=True: _mw_scan(d)

        cfg = get_advanced_config()
        old = (cfg.lab_incremental_cull, cfg.birdid_auto_identify, cfg.format_filter_mode)
        cfg.config["birdid_auto_identify"] = False
        cfg.config["format_filter_mode"] = "all"
        try:
            with (
                patch("tools.exiftool_manager.get_exiftool_manager"),
                patch("core.recursive_scanner.is_dangerous_root", return_value=(False, "")),
            ):
                cfg.set_lab_incremental_cull(False)
                w._resume_prompt_handled = True
                w._start_processing()
                cfg.set_lab_incremental_cull(True)
                w._resume_prompt_handled = True
                w._start_processing()
            assert launched == [(True, False), (True, True)]
        finally:
            cfg.set_lab_incremental_cull(old[0])
            cfg.config["birdid_auto_identify"] = old[1]
            cfg.config["format_filter_mode"] = old[2]

    def test_queue_launch_incremental_sets_worker_flag(self, tmp_path):
        """多目录队列中的增量目录走 _queue_launch(..., incremental=True) 并设置 incremental 传给 WorkerThread。"""
        from ui.main_window import SuperPickyMainWindow

        _app = QApplication.instance() or QApplication([])
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="3星_精选/OLD1.NEF")
        db.close()
        (tmp_path / "NEW1.NEF").write_bytes(b"new1")

        w = SuperPickyMainWindow()
        w._queue_counts = {str(tmp_path): 1}
        w._queue_total = 1

        captured = {}

        def _fake_launch(directory, resume, scan_results, clear_log=True):
            captured["directory"] = directory
            captured["resume"] = resume
            captured["scan_count"] = scan_results[0].photo_count if scan_results else 0
            captured["incremental"] = w._pending_worker_incremental
            w._pending_worker_incremental = False

        w._launch_worker = _fake_launch
        with (
            patch("core.recursive_scanner.is_dangerous_root", return_value=(False, "")),
            patch("advanced_config.get_advanced_config") as mock_cfg,
        ):
            mock_cfg.return_value.folder_layout = "rating_first"
            mock_cfg.return_value.video_auto_process_in_main = False
            mock_cfg.return_value.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})
            ok = w._queue_launch(str(tmp_path), resume=False, incremental=True)

        assert ok is True
        assert captured == {
            "directory": str(tmp_path),
            "resume": False,
            "scan_count": 1,
            "incremental": True,
        }

    def test_worker_thread_incremental_ignores_species_subfolders(self, tmp_path, monkeypatch):
        """species_first 布局已有鸟种结果子目录时，WorkerThread(incremental=True) 不应误入批量多子目录模式。"""
        from ui.main_window import WorkerSignals, WorkerThread

        _app = QApplication.instance() or QApplication([])
        # 已有鸟种输出子目录：白鹭/OLD1.NEF；根目录新增 NEW1.NEF
        species_dir = tmp_path / "3星_精选" / "白鹭"
        species_dir.mkdir(parents=True)
        (species_dir / "OLD1.NEF").write_bytes(b"old")
        species_dir_top = tmp_path / "苍鹭"
        species_dir_top.mkdir()
        (species_dir_top / "OLD2.NEF").write_bytes(b"old2")

        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="3星_精选/白鹭/OLD1.NEF")
        _seed_photo_row(db, "OLD2", orig_rel="OLD2.NEF", curr_rel="苍鹭/OLD2.NEF")
        db.close()
        (tmp_path / "NEW1.NEF").write_bytes(b"new1")

        calls = []

        class _FakeProc:
            def __init__(self, dir_path, settings, callbacks):
                self.dir_path = dir_path

            def process(self, organize_files=True, cleanup_temp=True, resume=False, incremental=False):
                calls.append((self.dir_path, resume, incremental))
                return type(
                    "Res",
                    (),
                    {"stats": {"total": 1, "star_3": 0, "picked": 0, "star_2": 1, "star_1": 0, "star_0": 0, "no_bird": 0, "bird_species": []}},
                )()

        monkeypatch.setattr("core.photo_processor.PhotoProcessor", _FakeProc)
        with patch("advanced_config.get_advanced_config") as mock_cfg:
            mock_cfg.return_value.folder_layout = "species_first"
            mock_cfg.return_value.keep_temp_files = True
            mock_cfg.return_value.video_auto_process_in_main = False
            mock_cfg.return_value.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})
            mock_cfg.return_value.nima_enabled = True
            mock_cfg.return_value.sharpness_threshold = 400
            mock_cfg.return_value.nima_threshold = 5.0
            mock_cfg.return_value.save_crop_debug = False
            mock_cfg.return_value.normalization_mode = "log"
            mock_cfg.return_value.birdid_use_ebird = False
            mock_cfg.return_value.birdid_country_code = None
            mock_cfg.return_value.birdid_region_code = None
            mock_cfg.return_value.birdid_confidence_threshold = 70
            mock_cfg.return_value.birdid_auto_identify = False
            mock_cfg.return_value.flight_check = False
            mock_cfg.return_value.exposure_check = False
            mock_cfg.return_value.burst_check = False

            signals = WorkerSignals()
            wt = WorkerThread(
                str(tmp_path),
                [50, 400, 5.0, False, "log_compression", False, False, False, False],
                signals,
                resume=False,
                scan_results=None,
                incremental=True,
            )
            wt.process_files()

        assert calls == [(str(tmp_path), False, True)]
        assert wt.stats["incremental"] is True

    @pytest.mark.parametrize("lab_on", [False, True])
    def test_worker_batch_subfolders_respect_lab_switch(self, tmp_path, monkeypatch, lab_on):
        """批量子目录：实验室增量关闭时逐个全量处理（原行为）；开启时跳过已选且无新增的子目录。
        被跳过的子目录排在第一个时也不能出错（曾引用未赋值的 dir_count）。"""
        from core.recursive_scanner import ScannedDirectory
        from ui.main_window import WorkerSignals, WorkerThread

        _app = QApplication.instance() or QApplication([])
        done = tmp_path / "a_done"
        fresh = tmp_path / "b_fresh"
        done.mkdir()
        fresh.mkdir()
        (done / "OLD1.NEF").write_bytes(b"old")
        db = ReportDB(str(done))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="OLD1.NEF")
        db.close()
        (fresh / "NEW1.NEF").write_bytes(b"new")

        calls = []

        class _FakeProc:
            def __init__(self, dir_path, settings, callbacks):
                self.dir_path = dir_path

            def process(self, organize_files=True, cleanup_temp=True, resume=False, incremental=False):
                calls.append((os.path.basename(self.dir_path), incremental))
                return type("Res", (), {"stats": {"total": 1, "star_3": 0, "picked": 0, "star_2": 1,
                                                  "star_1": 0, "star_0": 0, "no_bird": 0,
                                                  "bird_species": []}})()

        monkeypatch.setattr("core.photo_processor.PhotoProcessor", _FakeProc)
        scan = [ScannedDirectory(path=str(done), depth=1, photo_count=1, video_count=0, video_files=()),
                ScannedDirectory(path=str(fresh), depth=1, photo_count=1, video_count=0, video_files=())]
        with patch("advanced_config.get_advanced_config") as mock_cfg:
            c = mock_cfg.return_value
            c.lab_incremental_cull = lab_on
            c.folder_layout = "species_first"
            c.keep_temp_files = True
            c.video_auto_process_in_main = False
            c.get_allowed_extensions.return_value = frozenset({".nef", ".jpg"})
            c.sharpness_threshold = 400
            c.nima_threshold = 5.0
            c.save_crop_debug = False
            c.normalization_mode = "log"
            c.birdid_use_ebird = False
            c.birdid_country_code = None
            c.birdid_region_code = None
            c.birdid_confidence_threshold = 70
            c.birdid_auto_identify = False
            wt = WorkerThread(
                str(tmp_path),
                [50, 400, 5.0, False, "log_compression", False, False, False, False],
                WorkerSignals(),
                resume=False,
                scan_results=scan,
            )
            wt.process_files()

        if lab_on:
            assert calls == [("b_fresh", False)]
        else:
            assert calls == [("a_done", False), ("b_fresh", False)]

    def test_resume_incremental_appends_newly_added_root_file(self, tmp_path, monkeypatch):
        """断点续处理 + 增量：既保留未完成的待处理列表，又把中途新加入的文件纳入待处理且不重扫历史库。"""
        db = ReportDB(str(tmp_path))
        _seed_photo_row(db, "OLD1", orig_rel="OLD1.NEF", curr_rel="OLD1.NEF")
        db.close()

        (tmp_path / "OLD1.NEF").write_bytes(b"old")
        (tmp_path / "MID_PENDING.NEF").write_bytes(b"pending")
        (tmp_path / "NEW_LATER.NEF").write_bytes(b"new_later")

        rsm = ResumeStateManager(str(tmp_path))
        assert rsm.start(["MID_PENDING"]) is True

        proc = PhotoProcessor(str(tmp_path), ProcessingSettings(), ProcessingCallbacks())
        proc._incremental_mode = True
        captured_tbr = []
        def _fake_resume_convert(raw_files_to_convert, files_tbr):
            for item in raw_files_to_convert:
                p = item[0] if isinstance(item, tuple) else item
                if f"{p}.jpg" not in files_tbr:
                    files_tbr.append(f"{p}.jpg")

        def _capture_tbr(files_tbr, *args, **kwargs):
            captured_tbr.extend(files_tbr)

        monkeypatch.setattr(proc, "_detect_bursts_early", lambda *a, **kw: {})
        monkeypatch.setattr(proc, "_convert_raws", _fake_resume_convert)
        monkeypatch.setattr(proc, "_process_images", _capture_tbr)
        monkeypatch.setattr(proc, "_cleanup_temp_files", lambda *a, **kw: None)
        monkeypatch.setattr(
            "core.photo_processor.get_exiftool_manager",
            lambda: _DummyExifToolManager(),
        )
        res = proc.process(organize_files=False, cleanup_temp=False, resume=True, incremental=True)
        assert isinstance(res.stats, dict)
        assert sorted(captured_tbr) == ["MID_PENDING.jpg", "NEW_LATER.jpg"]
