# -*- coding: utf-8 -*-
"""
birdid_server 轻量导入测试 — LR 插件"服务就绪慢"修复

验证 import birdid_server 不再把重型模型栈 (birdid.bird_identifier →
torch/YOLO) 拖进来，/health 在模型未加载时也能立即返回 ok。
这样打包版启动后 5156 端口可以秒级就绪，Lightroom 插件不再遇到
"主程序明明在跑却连不上"的窗口期。

birdid_server light-import tests — fix for the LR plugin "server slow to
become ready" issue.

Verify that importing birdid_server no longer drags in the heavy model stack
(birdid.bird_identifier → torch/YOLO), and that /health responds ok
immediately even before models are loaded. This lets port 5156 start
listening within seconds after app launch in packaged builds.
"""
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

_PROBE_CODE = r"""
import sys, json
import birdid_server

heavy_after_import = "birdid.bird_identifier" in sys.modules

client = birdid_server.app.test_client()
resp = client.get("/health")
data = resp.get_json()

heavy_after_health = "birdid.bird_identifier" in sys.modules

print(json.dumps({
    "heavy_after_import": heavy_after_import,
    "heavy_after_health": heavy_after_health,
    "health_status": data.get("status"),
    "http_status": resp.status_code,
}))
"""


def _run_probe() -> dict:
    """
    在干净子进程中导入 birdid_server 并请求 /health，返回探测结果。

    Import birdid_server in a clean subprocess, hit /health, and return the
    probe result.
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_CODE],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_ROOT,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"probe failed: {result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_import_birdid_server_does_not_load_model_stack():
    """import birdid_server 不应触发 birdid.bird_identifier（torch 栈）导入。"""
    probe = _run_probe()
    assert probe["heavy_after_import"] is False, (
        "import birdid_server 拖入了 birdid.bird_identifier（重型模型栈），"
        "服务器监听将被模型导入阻塞十几秒以上"
    )


def test_health_ok_without_model_stack():
    """/health 在模型栈未加载时也应返回 200 + status=ok，且不触发重型导入。"""
    probe = _run_probe()
    assert probe["http_status"] == 200
    assert probe["health_status"] == "ok"
    assert probe["heavy_after_health"] is False, (
        "/health 触发了重型模型栈导入，首次健康检查会被阻塞"
    )
