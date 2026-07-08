# -*- coding: utf-8 -*-
"""闭环契约：App 产出 zip → 训练工具 intake.scan_inbox 识别为合法批次。"""
import os
import shutil
import tempfile

from PIL import Image

from core.submission_builder import SubmissionItem, build_submission
from tools.train.intake import scan_inbox


def _make_jpeg(path, w=1500, h=1000):
    Image.new("RGB", (w, h), (20, 120, 90)).save(path, "JPEG")


def test_zip_recognized_as_valid_inbox_batch():
    src_dir = tempfile.mkdtemp()
    out_dir = tempfile.mkdtemp()
    p = os.path.join(src_dir, "SAMPLE.jpg")
    _make_jpeg(p)
    res = build_submission(
        [SubmissionItem(photo_path=p, model_class_id=7447, chinese="棕脸鹟莺")],
        out_dir, app_version="test",
    )

    # 把产出 zip 放进模拟收件箱
    inbox = tempfile.mkdtemp()
    shutil.copy2(res.zip_path, os.path.join(inbox, os.path.basename(res.zip_path)))
    extract_root = tempfile.mkdtemp()

    batches = scan_inbox(inbox, extract_root)
    assert len(batches) == 1
    b = batches[0]
    assert b.label_csv  # 找到了 labels.csv
    assert b.md5_by_relpath  # 有图
    assert b.problems == []  # 无致命问题
