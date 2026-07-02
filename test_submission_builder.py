# -*- coding: utf-8 -*-
"""core/submission_builder.py 单测：长边/抹EXIF/labels.csv/zip 结构/损坏跳过。"""
import csv
import io
import json
import os
import tempfile
import zipfile

from PIL import Image

from core.submission_builder import (
    LONG_EDGE, JPEG_QUALITY, SubmissionItem, build_submission,
)


def _make_jpeg_with_gps(path: str, w: int, h: int) -> None:
    """写一张带 GPS EXIF 的 JPEG，用于验证导出后 EXIF 被抹掉。"""
    img = Image.new("RGB", (w, h), (30, 90, 160))
    exif = img.getexif()
    exif[0x8825] = {}  # GPS IFD 占位
    img.save(path, "JPEG", exif=exif.tobytes())


def _open_zip(zip_path):
    return zipfile.ZipFile(zip_path)


def test_build_produces_valid_zip_1280_no_exif():
    tmp = tempfile.mkdtemp()
    out = tempfile.mkdtemp()
    src = os.path.join(tmp, "BIRD_A.jpg")
    _make_jpeg_with_gps(src, 3000, 2000)  # 长边 3000 → 应缩到 1280

    items = [SubmissionItem(photo_path=src, model_class_id=7447,
                            chinese="棕脸鹟莺", wrong_cn="白头鹎",
                            birdid_confidence=0.02, is_the_failed_one=True)]
    res = build_submission(items, out, app_version="4.3.1")

    assert res.count == 1
    assert res.skipped == []
    assert os.path.exists(res.zip_path)

    with _open_zip(res.zip_path) as z:
        names = set(z.namelist())
        assert "labels.csv" in names
        assert "submission.json" in names
        img_entries = [n for n in names if n.startswith("images/") and n.endswith(".jpg")]
        assert len(img_entries) == 1

        # 长边 = 1280，且无 EXIF
        with z.open(img_entries[0]) as fp:
            im = Image.open(io.BytesIO(fp.read()))
            assert max(im.size) == LONG_EDGE
            assert not im.getexif()  # 无 EXIF/GPS

        # labels.csv 列 = filename,model_class_id,chinese；filename 去扩展名
        rows = list(csv.DictReader(io.TextIOWrapper(z.open("labels.csv"), encoding="utf-8")))
        assert rows[0]["filename"] == "BIRD_A"
        assert rows[0]["model_class_id"] == "7447"
        assert rows[0]["chinese"] == "棕脸鹟莺"

        # submission.json 溯源字段
        meta = json.loads(z.read("submission.json").decode("utf-8"))
        assert meta["app_version"] == "4.3.1"
        assert meta["count"] == 1
        assert meta["per_photo"][0]["is_the_failed_one"] is True
        assert meta["per_photo"][0]["wrong_cn"] == "白头鹎"


def test_corrupt_image_is_skipped_not_fatal():
    tmp = tempfile.mkdtemp()
    out = tempfile.mkdtemp()
    good = os.path.join(tmp, "GOOD.jpg")
    _make_jpeg_with_gps(good, 1500, 1000)
    bad = os.path.join(tmp, "BAD.jpg")
    with open(bad, "wb") as f:
        f.write(b"not a real jpeg")

    items = [
        SubmissionItem(photo_path=good, model_class_id=1, chinese="甲"),
        SubmissionItem(photo_path=bad, model_class_id=2, chinese="乙"),
    ]
    res = build_submission(items, out, app_version="x")
    assert res.count == 1
    assert len(res.skipped) == 1
    assert os.path.basename(res.skipped[0][0]) == "BAD.jpg"


def test_duplicate_stems_deduped():
    tmp = tempfile.mkdtemp()
    out = tempfile.mkdtemp()
    a = os.path.join(tmp, "a"); os.makedirs(a)
    b = os.path.join(tmp, "b"); os.makedirs(b)
    p1 = os.path.join(a, "DUP.jpg"); _make_jpeg_with_gps(p1, 800, 600)
    p2 = os.path.join(b, "DUP.jpg"); _make_jpeg_with_gps(p2, 800, 600)
    items = [
        SubmissionItem(photo_path=p1, model_class_id=1, chinese="甲"),
        SubmissionItem(photo_path=p2, model_class_id=1, chinese="甲"),
    ]
    res = build_submission(items, out, app_version="x")
    with zipfile.ZipFile(res.zip_path) as z:
        imgs = sorted(n for n in z.namelist() if n.startswith("images/"))
    assert len(imgs) == 2  # stem 冲突已加序号，两张都在
