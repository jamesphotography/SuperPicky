# -*- coding: utf-8 -*-
"""
Trusted package source registry for initialization downloads.

This module keeps PyPI mirrors and Torch wheel mirrors in separate source pools.
PyPI mirrors are only used for ordinary Python packages, while Torch CPU/CUDA
wheels are resolved through direct wheel URLs from an independent Torch pool.

初始化下载使用的可信软件源注册表。

此模块将 PyPI 镜像与 Torch wheel 镜像拆分到独立源池中。PyPI 镜像只用于普通
Python 包，Torch CPU/CUDA wheel 则通过独立 Torch 源池中的直链 wheel URL 解析。
"""

from __future__ import annotations

import json
import locale
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from config import get_app_config_dir


SourceKind = Literal["pypi", "torch"]
TrustLevel = Literal["official", "trusted", "community"]


@dataclass(frozen=True)
class PackageSource:
    """
    One package source candidate.

    单个包下载源候选。

    参数 Parameters:
        name: Stable source identifier. / 稳定源标识。
        region: Geographic hint used for diagnostics. / 用于诊断的地理提示。
        url: Base package index or wheel directory URL. / 包索引或 wheel 目录 URL。
        source_kind: Source pool type. / 源池类型。
        trust_level: Source trust classification. / 源可信级别。
        enabled_by_default: Whether the built-in registry enables it. / 内置注册表是否默认启用。
        probe_packages: PyPI package names used for availability probes. / PyPI 可用性探测包名。
    """

    name: str
    region: str
    url: str
    source_kind: SourceKind
    trust_level: TrustLevel = "trusted"
    enabled_by_default: bool = True
    probe_packages: tuple[str, ...] = ("pip",)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_official(self) -> bool:
        """Return whether this source is the official upstream source."""
        return self.trust_level == "official" or "official" in self.name.lower()

    def to_probe_dict(self, *, probe_url: str | None = None) -> dict[str, str]:
        """
        Convert the source to the dict shape consumed by source probes.

        转换为源探测模块消费的字典形态。
        """
        payload = {
            "name": self.name,
            "url": self.url,
            "region": self.region,
            "source_kind": self.source_kind,
            "trust_level": self.trust_level,
            "is_official": "true" if self.is_official else "false",
        }
        if probe_url:
            payload["probe_url"] = probe_url
        return payload


@dataclass(frozen=True)
class SourcePool:
    """
    Named collection of source candidates.

    命名源候选集合。
    """

    name: str
    source_kind: SourceKind
    sources: tuple[PackageSource, ...]

    def enabled_sources(self) -> list[PackageSource]:
        """Return enabled built-in sources."""
        return [source for source in self.sources if source.enabled_by_default]


OFFICIAL_PYPI_URL = "https://pypi.org/simple"


_BUILTIN_PYPI_SOURCES: tuple[PackageSource, ...] = (
    PackageSource(
        name="pypi-official",
        region="global",
        url=OFFICIAL_PYPI_URL,
        source_kind="pypi",
        trust_level="official",
    ),
    PackageSource(
        name="tsinghua",
        region="cn",
        url="https://pypi.tuna.tsinghua.edu.cn/simple",
        source_kind="pypi",
    ),
    PackageSource(
        name="aliyun",
        region="cn",
        url="https://mirrors.aliyun.com/pypi/simple/",
        source_kind="pypi",
    ),
    PackageSource(
        name="ustc",
        region="cn",
        url="https://pypi.mirrors.ustc.edu.cn/simple/",
        source_kind="pypi",
    ),
    PackageSource(
        name="nju",
        region="cn",
        url="https://mirror.nju.edu.cn/pypi/web/simple/",
        source_kind="pypi",
    ),
    PackageSource(
        name="bfsu",
        region="cn",
        url="https://mirrors.bfsu.edu.cn/pypi/web/simple/",
        source_kind="pypi",
    ),
    PackageSource(
        name="cernet",
        region="cn",
        url="https://mirrors.cernet.edu.cn/pypi/web/simple",
        source_kind="pypi",
    ),
    PackageSource(
        name="tencent",
        region="cn",
        url="https://mirrors.cloud.tencent.com/pypi/simple/",
        source_kind="pypi",
    ),
    PackageSource(
        name="huawei",
        region="cn",
        url="https://repo.huaweicloud.com/repository/pypi/simple/",
        source_kind="pypi",
    ),
    PackageSource(
        name="sjtu",
        region="cn",
        url="https://mirror.sjtu.edu.cn/pypi/web/simple/",
        source_kind="pypi",
    ),
)


_BUILTIN_TORCH_TEMPLATE_SOURCES: tuple[PackageSource, ...] = (
    PackageSource(
        name="torch-official",
        region="global",
        url="https://download.pytorch.org/whl/{variant}",
        source_kind="torch",
        trust_level="official",
    ),
    PackageSource(
        name="torch-nju",
        region="cn",
        url="https://mirror.nju.edu.cn/pytorch/whl/{variant}/",
        source_kind="torch",
    ),
    PackageSource(
        name="torch-aliyun",
        region="cn",
        url="https://mirrors.aliyun.com/pytorch-wheels/{variant}/",
        source_kind="torch",
    ),
)


_TORCH_VARIANT_SUBPATH = {
    "cpu": "cpu",
    "cuda": "cu118",
    "mac": "cpu",
}


def get_official_pypi_url() -> str:
    """Return the official PyPI simple API URL."""
    return OFFICIAL_PYPI_URL


def get_source_registry_path() -> Path:
    """Return the local user-extensible source registry path."""
    return get_app_config_dir() / "source_registry.json"


def get_preferred_region() -> str:
    """
    Infer a coarse region hint from the system locale.

    从系统 locale 推断粗粒度区域提示。
    """
    try:
        locale_name = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "").lower()
    except Exception:
        locale_name = ""
    if locale_name.startswith(("zh", "ja", "ko")):
        return "cn"
    if locale_name in {"en_au", "en_nz"}:
        return "oceania"
    if locale_name.startswith(("de", "fr", "it", "es", "nl", "pl", "sv", "en_gb")):
        return "europe"
    return "global"


def build_pypi_probe_url(index_url: str, package_name: str = "pip") -> str:
    """
    Build a simple API package probe URL from a PyPI index root.

    基于 PyPI 索引根路径构建 simple API 包探测 URL。
    """
    return f"{index_url.rstrip('/')}/{package_name.strip('/')}/"


def load_source_registry_overrides() -> dict[str, Any]:
    """
    Load optional user source registry overrides.

    加载可选的用户源注册表覆盖配置。

    Supported shape / 支持格式:
    {
      "disabled": ["source-name"],
      "pypi": [{"name": "...", "url": "...", "region": "..."}],
      "torch": [{"name": "...", "url": "https://.../{variant}/"}]
    }
    """
    path = get_source_registry_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_pypi_sources(*, include_overrides: bool = True) -> list[PackageSource]:
    """
    Return enabled PyPI sources, including optional local overrides.

    返回启用的 PyPI 源，并包含可选本地覆盖配置。
    """
    overrides = load_source_registry_overrides() if include_overrides else {}
    disabled = {str(item).strip().lower() for item in overrides.get("disabled", [])}
    sources: list[PackageSource] = [
        source
        for source in _BUILTIN_PYPI_SOURCES
        if source.enabled_by_default and source.name.lower() not in disabled
    ]
    sources.extend(
        _coerce_custom_sources(
            overrides.get("pypi", []),
            source_kind="pypi",
            default_region="custom",
        )
    )
    return _dedupe_sources(sources)


def get_torch_sources(
    runtime_variant: str,
    *,
    include_overrides: bool = True,
) -> list[PackageSource]:
    """
    Return enabled Torch wheel sources for one runtime variant.

    返回指定运行时变体对应的 Torch wheel 源。
    """
    subpath = _TORCH_VARIANT_SUBPATH.get(runtime_variant, runtime_variant)
    overrides = load_source_registry_overrides() if include_overrides else {}
    disabled = {str(item).strip().lower() for item in overrides.get("disabled", [])}
    templated_sources = [
        PackageSource(
            name=source.name,
            region=source.region,
            url=source.url.format(variant=subpath),
            source_kind="torch",
            trust_level=source.trust_level,
            enabled_by_default=source.enabled_by_default,
            probe_packages=source.probe_packages,
            metadata={"variant": subpath},
        )
        for source in _BUILTIN_TORCH_TEMPLATE_SOURCES
        if source.enabled_by_default and source.name.lower() not in disabled
    ]
    templated_sources.extend(
        _coerce_custom_sources(
            overrides.get("torch", []),
            source_kind="torch",
            default_region="custom",
            variant=subpath,
        )
    )
    return _dedupe_sources(templated_sources)


def sources_to_probe_dicts(
    sources: Iterable[PackageSource],
    *,
    probe_package: str = "pip",
) -> list[dict[str, str]]:
    """Convert PyPI package sources into probe dictionaries."""
    return [
        source.to_probe_dict(probe_url=build_pypi_probe_url(source.url, probe_package))
        for source in sources
    ]


def _coerce_custom_sources(
    entries: object,
    *,
    source_kind: SourceKind,
    default_region: str,
    variant: str | None = None,
) -> list[PackageSource]:
    if not isinstance(entries, list):
        return []
    result: list[PackageSource] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not name or not url or not url.startswith("https://"):
            continue
        if variant is not None:
            url = url.format(variant=variant)
        result.append(
            PackageSource(
                name=name,
                region=str(entry.get("region") or default_region).strip() or default_region,
                url=url,
                source_kind=source_kind,
                trust_level="community",
                enabled_by_default=bool(entry.get("enabled", True)),
                probe_packages=tuple(entry.get("probe_packages") or ("pip",)),
            )
        )
    return result


def _dedupe_sources(sources: Iterable[PackageSource]) -> list[PackageSource]:
    seen_urls: set[str] = set()
    seen_names: set[str] = set()
    result: list[PackageSource] = []
    for source in sources:
        normalized_url = source.url.rstrip("/")
        normalized_name = source.name.lower()
        if normalized_url in seen_urls or normalized_name in seen_names:
            continue
        seen_urls.add(normalized_url)
        seen_names.add(normalized_name)
        result.append(source)
    return result
