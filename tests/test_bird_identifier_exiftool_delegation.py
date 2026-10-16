# -*- coding: utf-8 -*-
"""
birdid/bird_identifier.py 的 GPS/RAW 预览提取应复用 tools.exiftool_manager，
而不是自行拼 mac-only 路径 + 裸 subprocess.run。

用户反馈（Windows 11）：开启识鸟后处理文件夹时，每张照片都会一闪而过弹出/
关闭控制台窗口；关闭识鸟就没有这个问题。根因：extract_gps_from_exif /
_load_raw_via_exiftool 硬编码 "/usr/local/bin/exiftool"、"/opt/homebrew/bin/exiftool"
等 mac 路径，且 subprocess.run 调用从未在 Windows 上传 creationflags=
CREATE_NO_WINDOW（tools/exiftool_manager.py 早在 V3.9.4 就修过这个坑，
但这两个函数各写了一份自己的实现，没有复用）。

这里用假的 ExifToolManager 验证：GPS/RAW 提取改走
get_exiftool_manager().read_metadata()/.extract_binary()（内部走常驻进程，
路径解析和 Windows 隐藏窗口逻辑已经修好），且不再直接触发 subprocess.run。

Bird ID GPS/RAW-preview extraction should delegate to tools.exiftool_manager
instead of rolling its own mac-only path lookup + bare subprocess.run.

User report (Windows 11): with Bird ID enabled, every photo flashes open/close
a console window during folder processing; disabling Bird ID makes it stop.
Root cause: extract_gps_from_exif / _load_raw_via_exiftool hardcode macOS-only
paths ("/usr/local/bin/exiftool", "/opt/homebrew/bin/exiftool") and never pass
creationflags=CREATE_NO_WINDOW on Windows (tools/exiftool_manager.py already
fixed this in V3.9.4, but these two functions duplicated their own subprocess
logic instead of reusing it).

These tests use a fake ExifToolManager to verify GPS/RAW extraction goes
through get_exiftool_manager().read_metadata()/.extract_binary() (which use the
persistent process with correct path resolution and Windows window hiding
already fixed), and that subprocess.run is never invoked directly.
"""
import io
import subprocess

import pytest
from PIL import Image


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError(
        "bird_identifier 不应再直接调用 subprocess.run；"
        "GPS/RAW 提取应通过 get_exiftool_manager() 走常驻进程"
    )


@pytest.fixture(autouse=True)
def _forbid_direct_subprocess(monkeypatch):
    """全程禁止直接调用 subprocess.run，逼迫走 ExifToolManager 封装。"""
    monkeypatch.setattr(subprocess, "run", _fail_if_called)


class _FakeExifToolManager:
    def __init__(self, metadata=None, binary_by_tag=None):
        self._metadata = metadata or {}
        self._binary_by_tag = binary_by_tag or {}
        self.read_metadata_calls = []
        self.extract_binary_calls = []

    def read_metadata(self, file_path, extra_args=None):
        self.read_metadata_calls.append((file_path, extra_args))
        return self._metadata

    def extract_binary(self, file_path, tag):
        self.extract_binary_calls.append((file_path, tag))
        return self._binary_by_tag.get(tag)


def test_extract_gps_from_exif_delegates_to_exiftool_manager(monkeypatch, tmp_path):
    """GPS 提取应调用 ExifToolManager.read_metadata，而非自己拼路径起子进程。"""
    from birdid import bird_identifier

    fake_manager = _FakeExifToolManager(
        metadata={
            "GPSLatitude": "34 deg 3' 8.00\"",
            "GPSLatitudeRef": "S",
            "GPSLongitude": "151 deg 12' 36.00\"",
            "GPSLongitudeRef": "E",
        }
    )
    monkeypatch.setattr(
        "tools.exiftool_manager.get_exiftool_manager", lambda: fake_manager
    )

    img_path = tmp_path / "fake.jpg"
    img_path.write_bytes(b"not a real jpeg, exiftool call is mocked")

    lat, lon, msg = bird_identifier.extract_gps_from_exif(str(img_path))

    assert fake_manager.read_metadata_calls, "应调用 read_metadata 而不是自己起子进程"
    called_path, extra_args = fake_manager.read_metadata_calls[0]
    assert called_path == str(img_path)
    assert extra_args is not None and "-GPSLatitude" in extra_args

    # 南半球纬度取负、"GPS: ..." 文案与原实现保持一致
    assert lat == pytest.approx(-34.052222, abs=1e-4)
    assert lon == pytest.approx(151.21, abs=1e-2)
    assert msg.startswith("GPS:")


def test_load_raw_via_exiftool_delegates_to_exiftool_manager(monkeypatch, tmp_path):
    """RAW 预览提取应调用 ExifToolManager.extract_binary，而非自己拼路径起子进程。"""
    from birdid import bird_identifier

    preview = Image.effect_noise((256, 256), 64).convert("RGB")
    buf = io.BytesIO()
    preview.save(buf, format="JPEG", quality=95)
    preview_bytes = buf.getvalue()
    assert len(preview_bytes) > 1000, "测试用预览图需要超过函数内部 1000 字节的判定门槛"

    fake_manager = _FakeExifToolManager(
        binary_by_tag={"-JpgFromRaw": preview_bytes}
    )
    monkeypatch.setattr(
        "tools.exiftool_manager.get_exiftool_manager", lambda: fake_manager
    )

    raw_path = tmp_path / "fake.arw"
    raw_path.write_bytes(b"not a real RAW, exiftool call is mocked")

    img = bird_identifier._load_raw_via_exiftool(str(raw_path))

    assert fake_manager.extract_binary_calls, "应调用 extract_binary 而不是自己起子进程"
    called_path, tag = fake_manager.extract_binary_calls[0]
    assert called_path == str(raw_path)
    assert tag == "-JpgFromRaw"
    assert img.size == (256, 256)


def test_load_raw_via_exiftool_raises_when_all_tags_empty(monkeypatch, tmp_path):
    """三个 tag 都取不到可解码预览图时应抛出友好错误，而不是静默返回坏图。"""
    from birdid import bird_identifier

    fake_manager = _FakeExifToolManager(binary_by_tag={})
    monkeypatch.setattr(
        "tools.exiftool_manager.get_exiftool_manager", lambda: fake_manager
    )

    raw_path = tmp_path / "fake.arw"
    raw_path.write_bytes(b"not a real RAW, exiftool call is mocked")

    with pytest.raises(Exception):
        bird_identifier._load_raw_via_exiftool(str(raw_path))

    called_tags = [tag for _, tag in fake_manager.extract_binary_calls]
    assert called_tags == ["-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"]
