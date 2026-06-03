# -*- coding: utf-8 -*-
"""
HTTP source probe helpers for initialization.

Notes:
- We intentionally do not use ICMP ping as the primary selection mechanism.
- Some networks block ping while HTTPS still works normally.
- Selection is based on real HTTP responsiveness and cached for the current run.

HTTP 源探测辅助工具，用于初始化。

注意事项:
- 我们有意不使用 ICMP ping 作为主要选择机制。
- 某些网络阻止 ping，但 HTTPS 仍然正常工作。
- 选择基于真实的 HTTP 响应能力，并在当前运行中缓存。
"""

from __future__ import annotations

import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

logging.basicConfig(level=logging.INFO)


DEFAULT_TIMEOUT_SECONDS = 4.0
LARGE_FILE_TIMEOUT_SECONDS = 10.0


@dataclass
class ProbeResult:
    """
    源探测结果数据类。

    Source probe result dataclass.

    属性 Attributes:
        name (str): 源名称
        url (str): 源 URL
        ok (bool): 探测是否成功
        total_ms (float): 总响应时间（毫秒）
        first_byte_ms (float): 首字节响应时间（毫秒）
        error (Optional[str]): 错误信息（如果失败）
        status_code (Optional[int]): HTTP 状态码
        response_headers (Optional[Dict[str, str]]): 响应头
        region (Optional[str]): 源的地理区域提示 / Geographic source hint
        source_kind (Optional[str]): 源池类型，例如 pypi 或 torch / Source pool type
        trust_level (Optional[str]): 源可信级别，例如 official 或 trusted / Trust level
        is_official (bool): 是否为官方上游源 / Whether this is the official upstream
        bytes_read (int): 探测实际读取的字节数 / Bytes read during the probe
        sample_bytes (int): 探测请求的样本字节数 / Requested probe sample bytes
    """

    name: str
    url: str
    ok: bool
    total_ms: float
    first_byte_ms: float
    error: Optional[str] = None
    status_code: Optional[int] = None
    response_headers: Optional[Dict[str, str]] = None
    region: Optional[str] = None
    source_kind: Optional[str] = None
    trust_level: Optional[str] = None
    is_official: bool = False
    bytes_read: int = 0
    sample_bytes: int = 0


_PROBE_CACHE: Dict[str, List[ProbeResult]] = {}


def _normalize_probe_url(url: str) -> str:
    """
    标准化化探测 URL。

    Normalize probe URL.

    参数 Parameters:
        url (str): 原始 URL

    返回 Returns:
        str: 标准化后的 URL
    """
    if url.endswith("/simple"):
        return url.rstrip("/") + "/pip/"
    return url


def probe_url(
    name: str, url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> ProbeResult:
    """
    探测单个 URL 的响应能力。

    Probe the responsiveness of a single URL.

    参数 Parameters:
        name (str): 源名称
        url (str): 要探测的 URL
        timeout (float): 超时时间（秒）

    返回 Returns:
        ProbeResult: 探测结果
    """
    start = time.perf_counter()
    request = urllib.request.Request(
        _normalize_probe_url(url),
        headers={"User-Agent": "SuperPicky-InitProbe/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            first_byte_start = time.perf_counter()
            response.read(256)
            first_byte_ms = (time.perf_counter() - first_byte_start) * 1000.0

            status_code = response.getcode()
            response_headers = dict(response.headers.items())

        total_ms = (time.perf_counter() - start) * 1000.0

        logging.info(
            "源探测成功: %s (%s) - 状态码: %d, 总耗时: %.2f ms, 首字节: %.2f ms",
            name,
            url,
            status_code,
            total_ms,
            first_byte_ms,
        )

        return ProbeResult(
            name=name,
            url=url,
            ok=True,
            total_ms=total_ms,
            first_byte_ms=first_byte_ms,
            status_code=status_code,
            response_headers=response_headers,
        )
    except Exception as exc:
        total_ms = (time.perf_counter() - start) * 1000.0
        error_msg = f"{type(exc).__name__}: {exc}"

        logging.warning(
            "源探测失败: %s (%s) - 错误: %s, 耗时: %.2f ms",
            name,
            url,
            error_msg,
            total_ms,
        )

        return ProbeResult(
            name=name,
            url=url,
            ok=False,
            total_ms=total_ms,
            first_byte_ms=0.0,
            error=error_msg,
            status_code=None,
            response_headers=None,
        )


def probe_sources(
    group_name: str, sources: Iterable[dict], timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> List[ProbeResult]:
    """
    探测一组源并返回结果。

    Probe a group of sources and return results.

    参数 Parameters:
        group_name (str): 源组名称（用于缓存）
        sources (Iterable[dict]): 源列表，每个源包含 name 和 url
        timeout (float): 超时时间（秒）

    返回 Returns:
        List[ProbeResult]: 探测结果列表
    """
    if group_name in _PROBE_CACHE:
        logging.info("使用缓存的探测结果: %s", group_name)
        return list(_PROBE_CACHE[group_name])

    sources_list = list(sources)
    logging.info("开始探测源组: %s，共 %d 个源", group_name, len(sources_list))
    results: List[ProbeResult] = []
    for source in sources_list:
        results.append(probe_url(source["name"], source["url"], timeout=timeout))

    _PROBE_CACHE[group_name] = list(results)

    successful_count = sum(1 for item in results if item.ok)
    logging.info(
        "源组 %s 探测完成: %d/%d 成功", group_name, successful_count, len(results)
    )

    return results


def pick_best_source(results: Iterable[ProbeResult]) -> Optional[ProbeResult]:
    """
    从探测结果中选择最佳源。

    Select the best source from probe results.

    参数 Parameters:
        results (Iterable[ProbeResult]): 探测结果列表

    返回 Returns:
        Optional[ProbeResult]: 最佳源，如果没有成功的源则返回 None
    """
    successful = [item for item in results if item.ok]
    if not successful:
        logging.warning("没有可用的源")
        return None

    best = min(successful, key=lambda item: (item.total_ms, item.first_byte_ms))
    logging.info(
        "选择最佳源: %s (%s) - 总耗时: %.2f ms, 首字节: %.2f ms",
        best.name,
        best.url,
        best.total_ms,
        best.first_byte_ms,
    )
    return best


def clear_probe_cache() -> None:
    _PROBE_CACHE.clear()
