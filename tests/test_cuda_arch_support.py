# -*- coding: utf-8 -*-
"""
RTX 50 系列（Blackwell，算力 12.0 / sm_120）配 cu118 版 torch：cuda.is_available() 为真、
推理不报错，但识鸟结果是错的（用户 RTX 5060 实测，换 CPU 版后识别全部正确）。
torch 没为这张卡编译时必须改走 CPU，并在会话日志里说明原因。

RTX 50 (sm_120) with a cu118 torch build: CUDA reports available and runs
without errors but Bird ID results are wrong (confirmed on an RTX 5060; the
CPU build identifies correctly). When torch has no kernels for the GPU we
must fall back to CPU and say why in the session log.
"""
from types import SimpleNamespace

import pytest

import config


def _fake_torch(capability, arch_list, available=True):
    """
    最小 torch 替身 / Minimal torch stand-in.
    """
    cuda = SimpleNamespace(
        is_available=lambda: available,
        get_device_capability=lambda _i=0: capability,
        get_arch_list=lambda: list(arch_list),
        get_device_name=lambda _i=0: "NVIDIA GeForce RTX 5060",
        get_device_properties=lambda _i=0: SimpleNamespace(total_memory=8 * 1024 ** 3),
    )
    return SimpleNamespace(
        cuda=cuda,
        device=lambda t: SimpleNamespace(type=t),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        version=SimpleNamespace(cuda="11.8"),
    )


CU118 = ["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]
CU128 = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]


@pytest.mark.parametrize("cap, arch, ok", [
    ((12, 0), CU118, False),            # RTX 50 + cu118：不支持 / unsupported
    ((12, 0), CU128, True),             # RTX 50 + cu128
    ((8, 9), CU118, True),              # RTX 40（sm_89 跑 sm_86 二进制）/ same-major binary compat
    ((8, 6), CU118, True),              # RTX 30
    ((7, 5), CU128, True),              # RTX 20
    ((6, 1), CU128, False),             # GTX 10 + cu128 未编进 / not built in
    ((12, 0), CU118 + ["compute_90"], False),  # 只有 PTX 不算 / PTX alone does not count
])
def test_cuda_arch_supported(cap, arch, ok):
    assert config.cuda_arch_supported(_fake_torch(cap, arch)) is ok


def test_unknown_arch_list_is_trusted():
    """拿不到编译列表时不误伤 / An empty or failing arch list is not held against the GPU."""
    assert config.cuda_arch_supported(_fake_torch((12, 0), [])) is True


@pytest.fixture
def patched(monkeypatch):
    def apply(fake):
        monkeypatch.setattr(config, "_get_torch_module", lambda: fake)
        monkeypatch.setattr(config.platform, "system", lambda: "Windows")
        monkeypatch.setattr(config, "_CUDA_SUPPORT_CACHE", None, raising=False)
    return apply


def test_best_device_falls_back_to_cpu_on_unsupported_gpu(patched):
    patched(_fake_torch((12, 0), CU118))
    assert config.get_best_device().type == "cpu"
    reason = config.cuda_unsupported_reason()
    assert reason and "sm_120" in reason and "11.8" in reason


def test_best_device_keeps_cuda_on_supported_gpu(patched):
    patched(_fake_torch((8, 9), CU118))
    assert config.get_best_device().type == "cuda"
    assert config.cuda_unsupported_reason() is None


def test_system_info_reports_fallback(monkeypatch):
    """会话日志头写明 GPU 不受支持、改用 CPU / The session header says why CPU is used."""
    import sys
    from tools import system_logger

    fake = _fake_torch((12, 0), CU118)
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setattr(config, "_get_torch_module", lambda: fake)
    monkeypatch.setattr(config.platform, "system", lambda: "Windows")
    monkeypatch.setattr(config, "_CUDA_SUPPORT_CACHE", None, raising=False)
    info = system_logger.collect_system_info()
    assert info["ai_device"].startswith("CPU")
    assert "sm_120" in info["gpu_warning"]
    assert info["gpu_name"] == "NVIDIA GeForce RTX 5060"
