# -*- coding: utf-8 -*-
"""
Runtime requirements manager for lightweight builds.

This module provides a unified interface for managing platform-specific runtime
dependencies across CPU, CUDA, and macOS builds. It consolidates the previously
separate requirements_runtime_*.txt files into a single Python module with
type-safe configuration access.

轻量化构建的运行时依赖管理模块。

该模块为 CPU、CUDA 和 macOS 构建统一描述运行时依赖，
避免把平台差异散落在多个 requirements 文本文件与调用点之间。
"""

from __future__ import annotations

import sys
import urllib.parse
from dataclasses import dataclass
from typing import Literal


PlatformType = Literal["cpu", "cuda", "mac"]


@dataclass(frozen=True)
class RuntimeRequirements:
    """
    Runtime dependency configuration for a specific platform.

    单个平台对应的运行时依赖配置。
    """

    torch_version: str
    torchvision_version: str
    torchaudio_version: str
    timm_version: str
    torch_index_urls: list[str]
    index_url: str | None = None

    @property
    def extra_index_urls(self) -> list[str]:
        """
        Backward-compatible alias for Torch-only index URLs.

        Torch 专用索引 URL 的向后兼容别名。
        """
        return list(self.torch_index_urls)

    @staticmethod
    def _format_pinned_requirement(package_name: str, version: str) -> str:
        """
        Return a pinned requirement only when a version is provided.

        仅在存在版本号时返回带固定版本的依赖声明。
        """

        normalized_version = version.strip()
        if not normalized_version:
            return package_name
        return f"{package_name}=={normalized_version}"

    def to_requirements_string(
        self,
        *,
        include_indexes: bool = True,
        package_urls: dict[str, str] | None = None,
    ) -> str:
        """
        Convert configuration to pip requirements file format.

        将配置转换为 pip requirements 文件格式。
        """
        lines = []
        package_urls = package_urls or {}
        if include_indexes and self.index_url:
            lines.append(f"--index-url {self.index_url}")
        if include_indexes:
            for url in self.torch_index_urls:
                lines.append(f"--extra-index-url {url}")
        lines.append(
            package_urls.get(
                "torch",
                self._format_pinned_requirement("torch", self.torch_version),
            )
        )
        lines.append(
            package_urls.get(
                "torchvision",
                self._format_pinned_requirement("torchvision", self.torchvision_version),
            )
        )
        lines.append(
            package_urls.get(
                "torchaudio",
                self._format_pinned_requirement("torchaudio", self.torchaudio_version),
            )
        )
        lines.append(f"timm{self.timm_version}")
        return "\n".join(lines)

    def to_requirements_lines(
        self,
        *,
        package_urls: dict[str, str] | None = None,
    ) -> list[str]:
        """
        Return requirement lines without index headers, suitable for uv.

        返回不含索引头的依赖行列表，适合 uv 使用。
        """
        lines: list[str] = []
        package_urls = package_urls or {}
        lines.append(
            package_urls.get(
                "torch",
                self._format_pinned_requirement("torch", self.torch_version),
            )
        )
        lines.append(
            package_urls.get(
                "torchvision",
                self._format_pinned_requirement("torchvision", self.torchvision_version),
            )
        )
        lines.append(
            package_urls.get(
                "torchaudio",
                self._format_pinned_requirement("torchaudio", self.torchaudio_version),
            )
        )
        lines.append(f"timm{self.timm_version}")
        return lines

    def torch_wheel_urls(
        self,
        source_base: str,
    ) -> dict[str, str]:
        """
        Build direct wheel URL references for Torch packages on Windows.

        为 Windows 构建 Torch 系列包的直链 wheel URL 引用。
        """
        if not source_base.strip():
            return {}
        python_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        abi_tag = python_tag
        platform_tag = "win_amd64"
        base = source_base.strip().rstrip("/")
        package_versions = {
            "torch": self.torch_version,
            "torchvision": self.torchvision_version,
            "torchaudio": self.torchaudio_version,
        }
        result: dict[str, str] = {}
        for package_name, version in package_versions.items():
            normalized = (version or "").strip()
            if not normalized:
                continue
            filename = (
                f"{package_name}-{normalized}-{python_tag}-{abi_tag}-{platform_tag}.whl"
            )
            result[package_name] = (
                f"{package_name} @ {base}/{urllib.parse.quote(filename)}"
            )
        return result


def get_cpu_requirements() -> RuntimeRequirements:
    """Get runtime requirements for CPU builds. / 获取 CPU 构建的运行时依赖。"""
    return RuntimeRequirements(
        torch_version="2.7.1+cpu",
        torchvision_version="0.22.1+cpu",
        torchaudio_version="2.7.1+cpu",
        timm_version=">=0.9.0",
        # 多镜像候选避免单点故障：南大 (校园网快)、阿里 (海内外都可达，海外亦能 200)、
        # 官方 (兜底)。源探测会现场实测延迟挑最快的（见 _pick_preferred_with_ratio）。
        # Multiple mirrors guard against single-point failure: NJU (fast on
        # campus nets), Aliyun (reachable both inside and outside CN), and the
        # official source as a fallback. Latency is probed at install time.
        torch_index_urls=[
            "https://mirror.nju.edu.cn/pytorch/whl/cpu/",
            "https://mirrors.aliyun.com/pytorch-wheels/cpu/",
            "https://download.pytorch.org/whl/cpu",
        ],
    )


def get_cuda_requirements() -> RuntimeRequirements:
    """Get runtime requirements for CUDA builds. / 获取 CUDA 构建的运行时依赖。"""
    return RuntimeRequirements(
        torch_version="2.7.1+cu118",
        torchvision_version="0.22.1+cu118",
        torchaudio_version="2.7.1+cu118",
        timm_version=">=0.9.0",
        torch_index_urls=[
            "https://mirror.nju.edu.cn/pytorch/whl/cu118/",
            "https://mirrors.aliyun.com/pytorch-wheels/cu118/",
            "https://download.pytorch.org/whl/cu118",
        ],
    )


def get_mac_requirements() -> RuntimeRequirements:
    """Get runtime requirements for macOS builds. / 获取 macOS 构建的运行时依赖。"""
    return RuntimeRequirements(
        torch_version="2.8.0",
        torchvision_version="",
        torchaudio_version="",
        timm_version=">=0.9.0",
        torch_index_urls=[],
    )


def detect_platform() -> PlatformType:
    """Detect the current platform type. / 检测当前平台类型。"""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform == "win32":
        return "cuda"
    return "cpu"


def get_runtime_requirements(platform: PlatformType | None = None) -> RuntimeRequirements:
    """
    Get runtime requirements for the specified or detected platform.

    Args:
        platform: Platform type ('cpu', 'cuda', 'mac'). If None, auto-detects.
                  平台类型；若为 None，则自动检测。

    Returns:
        RuntimeRequirements: Platform-specific dependency configuration.
        对应平台的依赖配置。

    Raises:
        ValueError: If platform type is invalid.
        当平台类型非法时抛出。
    """
    if platform is None:
        platform = detect_platform()

    requirements_getters = {
        "cpu": get_cpu_requirements,
        "cuda": get_cuda_requirements,
        "mac": get_mac_requirements,
    }

    getter = requirements_getters.get(platform)
    if getter is None:
        raise ValueError(f"Unsupported platform: {platform}")

    return getter()
