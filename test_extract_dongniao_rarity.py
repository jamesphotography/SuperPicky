# -*- coding: utf-8 -*-
"""
懂鸟罕见指数提取脚本的单测 / Tests for the 懂鸟 rarity extractor.

这份数据的格式是从 app bundle 逆向出来的，没有 .proto 定义可依。把字段映射
钉进测试，是为了让这份知识留在仓库里而不是只存在于某次对话里：

    每条记录 field 2 是内嵌消息，其中
      3=目  4=科  5=中文名  6=英文名  7=学名  8=繁体名  12=罕见度(float64)
    罕见度 -1.0 是哨兵值（无评级/已灭绝），必须过滤。

用手工构造的 protobuf 夹具，不依赖本机是否装了懂鸟；另有一条对真实文件的
断言，装了才跑。**数据本身绝不进仓库**（懂鸟是商业软件），这里只有格式知识。

The wire format was reverse-engineered with no schema available, so the field
mapping lives in these tests. Fixtures are synthetic; the one test that reads
the real file skips when the app is not installed.
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def _varint(value: int) -> bytes:
    """编码一个 varint / Encode one varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _string(field: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return _tag(field, 2) + _varint(len(raw)) + raw


def _double(field: int, value: float) -> bytes:
    return _tag(field, 1) + struct.pack("<d", value)


def _record(cn: str, en: str, sci: str, rarity: float) -> bytes:
    """造一条与懂鸟同构的记录 / Build one record shaped like 懂鸟's."""
    inner = (_string(3, "PASSERIFORMES") + _string(4, "Falcunculidae")
             + _string(5, cn) + _string(6, en) + _string(7, sci)
             + _double(12, rarity))
    outer = _tag(1, 0) + _varint(0) + _tag(2, 2) + _varint(len(inner)) + inner
    return _tag(1, 2) + _varint(len(outer)) + outer


@pytest.fixture
def labels_file(tmp_path):
    """三条记录，其中一条是哨兵值 / Three records, one carrying the sentinel."""
    blob = (_record("东鵙雀鹟", "Eastern Shriketit", "Falcunculus frontatus", 9.09)
            + _record("北鵙雀鹟", "Northern Shriketit", "Falcunculus whitei", 9.8)
            + _record("某灭绝鸟", "Extinct Bird", "Extinctus avis", -1.0))
    path = tmp_path / "dn_labels.260630.picto.bin"
    path.write_bytes(blob)
    return str(path)


def test_field_mapping_is_pinned(labels_file):
    """
    字段映射必须与逆向结论一致：5=中文名 6=英文名 7=学名 12=罕见度。

    这条是整份格式知识的锚点——映射错了，提取出来的就是别的东西。
    """
    from scripts_dev.extract_dongniao_rarity import parse_dongniao

    data = parse_dongniao(labels_file)

    assert data["Falcunculus frontatus"] == {
        "cn": "东鵙雀鹟", "en": "Eastern Shriketit", "rarity": 9.09}


def test_sentinel_rows_are_dropped(labels_file):
    """
    -1.0 的记录必须被剔除。

    不剔除的话详情页会显示「罕见度 -1.0」。上一版提取已经过滤过，这条防回归。
    """
    from scripts_dev.extract_dongniao_rarity import parse_dongniao

    data = parse_dongniao(labels_file)

    assert "Extinctus avis" not in data
    assert all(rec["rarity"] >= 0 for rec in data.values())


def test_every_valid_species_survives(labels_file):
    """两条有效记录都要在，别把哨兵过滤写成了过度过滤。"""
    from scripts_dev.extract_dongniao_rarity import parse_dongniao

    data = parse_dongniao(labels_file)

    assert set(data) == {"Falcunculus frontatus", "Falcunculus whitei"}


def test_truncated_file_does_not_raise(tmp_path):
    """
    文件被截断时不得抛异常。

    app 升级换格式、或读到半个文件都可能发生，提取工具应当安静地少给数据，
    而不是炸在半路让人以为是别的问题。
    """
    from scripts_dev.extract_dongniao_rarity import parse_dongniao

    path = tmp_path / "broken.bin"
    path.write_bytes(_record("鸟", "Bird", "Avis avis", 5.0)[:12])

    assert parse_dongniao(str(path)) == {}


def test_locator_returns_none_when_not_installed(tmp_path):
    """没装懂鸟时定位函数返回 None，而不是抛异常。"""
    from scripts_dev.extract_dongniao_rarity import find_labels_file

    assert find_labels_file(str(tmp_path / "nope" / "*.bin")) is None


def test_real_app_yields_known_values():
    """
    装了懂鸟时，对真实文件断言一个已知取值。

    非洲鸵鸟 7.36 是逆向时用来确认 field 12 的那条；它变了说明格式或数据换了，
    此时应当先重新确认映射再跑提取。未安装则跳过。
    """
    from scripts_dev.extract_dongniao_rarity import find_labels_file, parse_dongniao

    path = find_labels_file()
    if not path:
        pytest.skip("本机未安装懂鸟")

    data = parse_dongniao(path)

    assert len(data) > 10000, f"只解析出 {len(data)} 条，格式可能变了"
    assert data["Struthio camelus"]["rarity"] == pytest.approx(7.36)
    assert data["Struthio camelus"]["cn"] == "非洲鸵鸟"
