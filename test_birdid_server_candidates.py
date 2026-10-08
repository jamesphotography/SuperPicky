# -*- coding: utf-8 -*-
"""完整候选与旧接口兼容性 / Full candidates and legacy API compatibility."""

import base64
from io import BytesIO
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from PIL import Image
import pytest


@pytest.fixture
def recognition_api(monkeypatch, tmp_path):
    """隔离模型与用户设置，走真实 HTTP 路由 / Exercise routes with isolated models/settings."""
    import birdid_server as server

    state = SimpleNamespace(result={}, calls=[])

    def identify(image_path, **kwargs):
        """记录实际请求与图片 / Record the actual request and image."""
        assert Path(image_path).exists()
        state.calls.append((image_path, kwargs))
        return state.result

    identifier = ModuleType("birdid.bird_identifier")
    identifier.identify_bird = identify
    # 同时替换 birdid 包本身：若包尚未导入，`from birdid.bird_identifier import …`
    # 会先执行 birdid/__init__.py，而它要从（已被替换的）bird_identifier 导入
    # quick_identify 等名字，单独运行本文件时就会 ImportError、接口返回 500；
    # 全量运行时因其他测试已在收集阶段导入真实 birdid 而碰巧通过。
    # 空壳包的 __path__ 指向真实目录，其他子模块照常可导入；monkeypatch 结束后恢复原状。
    # Also stub the birdid package: when it is not yet imported, importing the
    # submodule runs birdid/__init__.py, which pulls quick_identify and other names
    # from the stubbed module and fails when this file runs alone. The stub keeps
    # the real directory on __path__ so other submodules still import normally.
    package = ModuleType("birdid")
    package.__path__ = [str(Path(server.__file__).resolve().parent / "birdid")]
    package.bird_identifier = identifier
    monkeypatch.setitem(sys.modules, "birdid", package)
    monkeypatch.setitem(sys.modules, "birdid.bird_identifier", identifier)
    settings = ModuleType("advanced_config")
    settings.get_advanced_config = lambda: SimpleNamespace(name_format="default")
    monkeypatch.setitem(sys.modules, "advanced_config", settings)
    monkeypatch.setattr(
        server,
        "get_gui_settings",
        lambda: {
            "country_code": None,
            "region_code": None,
            "use_geo_filter": False,
        },
    )
    monkeypatch.setattr(server, "get_gui_language", lambda: "zh_CN")
    monkeypatch.setattr(server, "update_gui_settings_from_gps", lambda *args: None)
    photo = tmp_path / "白头鹎.jpg"
    Image.new("RGB", (8, 8)).save(photo)
    state.payload = {"image_path": str(photo)}
    state.client = server.app.test_client()
    state.server = server
    return state


@pytest.mark.parametrize(
    "confidences,expected",
    [
        ([95.5, 3.2, 1.1], [1]),
        ([60, 30, 10], [1, 2]),
        ([40, 35, 25], [1, 2, 3]),
        ([0, 0], [1]),
        ([85], [1]),
    ],
)
def test_all_candidates_preserve_legacy_filter(recognition_api, confidences, expected):
    """完整列表不改变旧筛选及分数 / The full list preserves legacy filtering and scores."""
    api = recognition_api
    api.result = {
        "success": True,
        "results": [
            {"cn_name": f"候选鸟{i}", "confidence": score}
            for i, score in enumerate(confidences, 1)
        ],
    }
    response = api.client.post("/recognize", json=api.payload)
    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert [r["rank"] for r in data["results"]] == expected
    assert [r["confidence"] for r in data["all_results"]] == confidences
    assert [r["cn_name"] for r in data["all_results"]] == [
        f"候选鸟{i}" for i in range(1, len(confidences) + 1)
    ]
    assert data["results"] == [data["all_results"][i - 1] for i in expected]
    assert api.calls[0][1]["top_k"] == 3
    assert len(api.calls) == 1


@pytest.mark.parametrize("language", ["zh_CN", "en_US"])
@pytest.mark.parametrize("transport", ["path", "base64"])
def test_complete_fields_and_request_options(
    recognition_api, monkeypatch, language, transport
):
    """中英文和两种传图方式保留完整字段 / Preserve fields across languages and transports."""
    api = recognition_api
    monkeypatch.setattr(api.server, "get_gui_language", lambda: language)
    bird = {
        "cn_name": "白头鹎",
        "en_name": "Light-vented Bulbul",
        "scientific_name": "Pycnonotus sinensis",
        "confidence": 3.2,
        "gbif_rarity_100": 0,
        "iucn_category": "LC",
        "ebird_match": True,
        "description": "低置信度候选也保留完整信息",
    }
    api.result = {
        "success": True,
        "results": [{"cn_name": "家燕", "confidence": 95.5}, bird],
        "yolo_info": "检测到鸟",
        "gps_info": {"latitude": 0, "longitude": 0},
        "geo_info": {"tier": "none", "enabled": False},
    }
    payload = dict(api.payload)
    if transport == "base64":
        image = BytesIO()
        Image.new("RGB", (8, 8)).save(image, format="JPEG")
        payload = {"image_base64": base64.b64encode(image.getvalue()).decode("ascii")}
    payload.update(
        top_k=7,
        use_yolo=False,
        use_gps=False,
        country_code="CN",
        region_code="CN-11",
        use_geo_filter=True,
    )
    response = api.client.post("/recognize", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["results"]) == 1
    assert data["all_results"][1] == {
        **bird,
        "rank": 2,
        "pinyin_name": "bái tóu bēi",
        "display_name": bird["cn_name"] if language == "zh_CN" else bird["en_name"],
    }
    assert data["all_results"][0]["gbif_rarity_100"] is None
    assert data["all_results"][0]["iucn_category"] is None
    for field in ("yolo_info", "gps_info", "geo_info"):
        assert data[field] == api.result[field]
    assert api.calls[0][1] == {
        "top_k": 7,
        "use_yolo": False,
        "use_gps": False,
        "country_code": "CN",
        "region_code": "CN-11",
        "use_geo_filter": True,
        "name_format": "default",
    }
    assert len(api.calls) == 1
    if transport == "base64":
        assert not Path(api.calls[0][0]).exists()


@pytest.mark.parametrize(
    "core,status",
    [
        ({"success": False, "error": "识别失败"}, 500),
        ({"success": True, "results": []}, 200),
        (
            {
                "success": True,
                "results": [],
                "geo_info": {
                    "enabled": True,
                    "region_code": "CN",
                    "species_count": 0,
                },
            },
            200,
        ),
    ],
)
def test_failure_contract_unchanged(recognition_api, core, status):
    """失败不伪装为成功的完整列表 / Errors never become successful full lists."""
    api = recognition_api
    api.result = core
    response = api.client.post("/recognize", json=api.payload)
    data = response.get_json()
    assert response.status_code == status
    assert data["success"] is False
    assert data["error"]
    assert "all_results" not in data
    assert "results" not in data
    if "geo_info" in core:
        assert data["geo_info"] == core["geo_info"]


def test_documented_response_matches_candidate_contract():
    """文档示例可解析且遵守新旧候选契约 / The documented JSON honors both contracts."""
    import json

    doc = (Path(__file__).parent / "docs" / "API使用说明.md").read_text(
        encoding="utf-8"
    )
    section = doc.split("### 2. 识别鸟类", 1)[1].split("**返回**", 1)[1]
    data = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    assert data["results"] == data["all_results"][:1]
    assert len(data["all_results"]) == 2
    assert data["all_results"][0]["cn_name"] == "白头鹎"
