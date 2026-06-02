# -*- coding: utf-8 -*-
"""
Parallel HTTP source probe with asyncio + httpx.

Replaces the sequential urllib probing in source_probe.py with concurrent
probes so all PyPI / Torch / HF endpoints are measured simultaneously,
cutting probe wall-clock time from O(N×timeout) to O(max(timeout)).

The module also supports geo-aware ranking that biases the result toward
the user's geographic region when latencies are close.

基于 asyncio + httpx 的并行 HTTP 源探测。

用并发探测替代 source_probe.py 中的串行 urllib 探测，使所有 PyPI / Torch / HF
端点同时检测，将探测耗时从 O(N×timeout) 降低到 O(max(timeout))。

模块还支持地理感知排序，当延迟接近时优先选择用户所在地区的镜像。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Iterable, List, Optional

from core.source_probe import ProbeResult, pick_best_source

logging.basicConfig(level=logging.INFO)


DEFAULT_TIMEOUT_SECONDS = 2.0
_PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD = 2.0


async def _httpx_probe_url_async(
    name: str,
    url: str,
    probe_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProbeResult:
    """
    Asynchronously probe a single URL for responsiveness.

    异步探测单个 URL 的响应能力。
    """
    try:
        import httpx
    except ImportError:
        return ProbeResult(
            name=name, url=url, ok=False, total_ms=0.0, first_byte_ms=0.0,
            error="httpx not available",
        )

    start = time.perf_counter()
    _normalized = probe_url or _normalize_probe_url(url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
            async with client.stream(
                "GET",
                _normalized,
                headers={
                    "User-Agent": "SuperPicky-InitProbe/2.0",
                    "Range": "bytes=0-0",
                },
            ) as response:
                first_byte_ms = (time.perf_counter() - start) * 1000.0
                async for _chunk in response.aiter_bytes(chunk_size=256):
                    break
                total_ms = (time.perf_counter() - start) * 1000.0
                ok = 200 <= response.status_code < 400
                status_code = response.status_code
            return ProbeResult(
                name=name,
                url=url,
                ok=ok,
                total_ms=total_ms,
                first_byte_ms=first_byte_ms,
                status_code=status_code,
                error=None if ok else f"HTTP {status_code}",
            )
    except Exception as exc:
        total_ms = (time.perf_counter() - start) * 1000.0
        logging.debug("源探测失败 %s (%s): %s", name, url, exc)
        return ProbeResult(
            name=name, url=url, ok=False, total_ms=total_ms, first_byte_ms=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )


def _normalize_probe_url(url: str) -> str:
    normalized = url.rstrip("/")
    if normalized.endswith("/simple"):
        return normalized + "/pip/"
    return url


def probe_sources_parallel(
    group_name: str,
    sources: List[dict],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> List[ProbeResult]:
    """
    Probe all sources concurrently and return latency-sorted results.

    并发探测所有源并返回按延迟排序的结果列表。

    Parameters:
        group_name: 源组名称（用于日志） / group label for logging
        sources: 源字典列表，每个包含 name 和 url
        timeout: 单个探测的超时（秒）
    """
    if not sources:
        return []

    start = time.perf_counter()
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _run_all():
        tasks = [
            _httpx_probe_url_async(
                item["name"],
                item["url"],
                probe_url=item.get("probe_url"),
                timeout=timeout,
            )
            for item in sources
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    results: List[ProbeResult] = loop.run_until_complete(_run_all())
    elapsed = (time.perf_counter() - start) * 1000.0
    successful = sum(1 for r in results if r.ok)
    logging.info(
        "源组 %s 并行探测完成: %d/%d 成功, 总耗时 %.2f ms",
        group_name, successful, len(results), elapsed,
    )
    return results


def pick_best_with_geo_and_ratio(
    results: List[ProbeResult],
    region_bias: List[str] | None = None,
) -> Optional[ProbeResult]:
    """
    Select the best source preferring geo-close mirrors within the 2× ratio.

    在 2× 比率内优先选择地理相近的镜像。

    Logic / 逻辑:
      1. Filter ok results.
      2. If a region-bias list is provided, prefer matches within 2× ratio
         of the fastest source overall.
      3. Fall back to _pick_preferred_with_ratio (mirror vs official).
    """
    ok = [r for r in results if r.ok]
    if not ok:
        logging.warning("pick_best_with_geo_and_ratio: 无可用源")
        return None

    fastest = min(ok, key=lambda r: r.total_ms)
    threshold = fastest.total_ms * _PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD

    if region_bias:
        region_matches = [
            r for r in ok
            if r.total_ms <= threshold and any(bias in r.name.lower() for bias in region_bias)
        ]
        if region_matches:
            best = min(region_matches, key=lambda r: r.total_ms)
            logging.info("geo+ratio: 选择 %s (%.1f ms)", best.name, best.total_ms)
            return best

    best = _pick_preferred_with_ratio(ok)
    if best:
        logging.info("geo+ratio: 回退 %s (%.1f ms)", best.name, best.total_ms)
    return best


def _pick_preferred_with_ratio(results: List[ProbeResult]) -> Optional[ProbeResult]:
    """
    Mirror-preferred selection within a 2× ratio of the fastest official.

    在 2× 官方最快速率内，优先选择镜像源。
    """
    if not results:
        return None
    non_official = [r for r in results if "official" not in r.name.lower()]
    official = [r for r in results if "official" in r.name.lower()]
    best_mirror = pick_best_source(non_official) if non_official else None
    best_official = pick_best_source(official) if official else None
    if best_mirror and best_official:
        if best_mirror.total_ms <= best_official.total_ms * _PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD:
            return best_mirror
        return best_official
    return best_mirror or best_official
