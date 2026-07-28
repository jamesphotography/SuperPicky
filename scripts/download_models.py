"""
Model and resource download helpers for lightweight initialization.

This module prepares model files and local fallback resources needed by the
welcome onboarding flow. It emits structured progress events so callers can
aggregate real byte progress, item-level progress, and source retry state
without scraping ad-hoc log text.

Download strategy (aligned with hf-mirror.com official guidance):
  1. Respect a user-provided HF_ENDPOINT, but never force a regional endpoint
     globally from the app.
  2. Probe hf-mirror.com and huggingface.co against the actual file URL with
     latency and sampled download speed.
  3. Try a bounded multi-thread httpx direct download first, then Hugging Face
     CLI resume fallback, then bundled huggingface_hub, and finally urllib.

轻量化初始化所需的模型与资源下载辅助模块。

此模块负责准备欢迎引导流程所需的模型文件与本地回退资源，并发出结构化进度事件，
以便调用方能够聚合真实字节进度、条目级进度以及镜像重试状态，而不必再解析零散日志文本。

下载策略（对齐 hf-mirror.com 官方指南）：
  1. 尊重用户显式提供的 HF_ENDPOINT，但应用自身不全局强制区域端点。
  2. 基于实际文件 URL 同时探测 hf-mirror.com 与 huggingface.co，并同时考量
     延时与采样下载速度。
  3. 优先使用 16 线程以内 httpx 直拉；失败时再使用支持续传的 Hugging Face
     CLI 兜底；再失败则使用内置 huggingface_hub，最后才用 urllib。
"""

import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple, cast


def _reconfigure_text_stream(stream: object) -> None:
    """Use UTF-8 output when the active stream implementation supports it."""
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")


_reconfigure_text_stream(sys.stdout)
_reconfigure_text_stream(sys.stderr)

# 当以 `python scripts/download_models.py` 直接运行时，sys.path[0] 是 scripts/ 目录，
# 项目根不在搜索路径里，下方 `from core.*` 会 ModuleNotFoundError。
# When invoked as `python scripts/download_models.py`, sys.path[0] is the
# scripts/ directory and the project root is not on the import path, so the
# `from core.*` imports below would raise ModuleNotFoundError without this.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

HF_MIRROR_ENDPOINT = "https://hf-mirror.com"
HF_OFFICIAL_ENDPOINT = "https://huggingface.co"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

try:
    import httpx
except ImportError:
    httpx = None

try:
    from core.source_probe_parallel import probe_sources_parallel
except Exception:
    probe_sources_parallel = None

from core.initialization_progress import (
    InitializationProgressEvent,
    PROGRESS_KIND_DOWNLOAD,
    STAGE_DOWNLOADING,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

DOWNLOAD_ENDPOINTS = [
    ("official", HF_OFFICIAL_ENDPOINT),
    ("hf-mirror", HF_MIRROR_ENDPOINT),
]

HF_CLI_TIMEOUT_SECONDS = 3600.0
HF_HUB_DOWNLOAD_TIMEOUT_SECONDS = 120.0
HF_DIRECT_DOWNLOAD_ATTEMPTS = 3
HF_DIRECT_RETRY_BASE_SECONDS = 1.5
HF_DIRECT_CONNECT_TIMEOUT_SECONDS = 20.0
HF_DIRECT_READ_TIMEOUT_SECONDS = 30.0
HF_DIRECT_PARALLEL_THREADS = 16
HF_DIRECT_PARALLEL_MIN_BYTES = 8 * 1024 * 1024
HF_DIRECT_PARALLEL_MIN_PART_BYTES = 4 * 1024 * 1024
HF_ENDPOINT_PROBE_SAMPLE_BYTES = 1024 * 1024
HF_ENDPOINT_PROBE_TIMEOUT_SECONDS = 8.0
HF_USER_AGENT = "SuperPicky-Downloader/4.2.6"


def _subprocess_no_window_kwargs() -> dict[str, int]:
    """
    返回 Windows 子进程隐藏控制台窗口所需的参数。

    Return subprocess keyword arguments that hide child console windows on
    Windows.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _format_mib_per_second(byte_count: int, elapsed_seconds: float) -> str:
    """
    格式化下载吞吐速度。

    Format download throughput in MiB/s.
    """
    if elapsed_seconds <= 0:
        return "n/a"
    return f"{(byte_count / 1048576) / elapsed_seconds:.2f} MiB/s"


@dataclass(frozen=True)
class HttpDownloadMetadata:
    """
    HTTP 直拉文件元数据。

    HTTP direct-download file metadata.
    """

    total_bytes: int | None
    supports_range: bool


@dataclass(frozen=True)
class DownloadSegment:
    """
    多线程直拉的单个字节分段。

    One byte range segment for multi-threaded direct download.
    """

    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        """返回分段字节数 / Return the segment size in bytes."""
        return self.end - self.start + 1

MODELS_TO_DOWNLOAD = [
    {
        "resource_id": "classification_model",
        "category": "Classification",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "model20240824.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["core_detection", "birdid"],
        "required": True,
        "sha256": "05e3f27d55ab1bfb1f01fa00a32e2a3308b2d9145954899ed34f6f5bc23666cc",
    },
    {
        "resource_id": "flight_model",
        "category": "Flight Detection",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "superFlier_efficientnet.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["flight"],
        "required": False,
        "sha256": "cf2d5bd10fff0af83fbf57fe365221339152aedd712c4bbbb5b757f2838451a7",
    },
    {
        "resource_id": "keypoint_model",
        "category": "Keypoint Detection",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "cub200_keypoint_resnet50_slim.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["keypoint"],
        "required": False,
        "sha256": "25bee11a4846e9065185bf22512e0ce4f81a62da79aece8f1683209f375904a6",
    },
    {
        "resource_id": "quality_model",
        "category": "Quality Assessment",
        "repo_id": "chaofengc/IQA-PyTorch-Weights",
        "filename": "cfanet_iaa_ava_res50-3cd62bb3.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["quality"],
        "required": False,
        "sha256": "3cd62bb33f9933ed7c6e3d5e79129e81c898eba78b7a2af516a0b0b974616975",
    },
    {
        # yolo11l-seg.pt 不在 jamesphotography/SuperPicky-models 中（该 repo 只放 .onnx 权重），
        # 改用 Ultralytics 官方公开仓库 Ultralytics/YOLO11，其中含完整 .pt 文件。
        # yolo11l-seg.pt is not hosted in jamesphotography/SuperPicky-models (that repo
        # only ships .onnx weights), so we point at Ultralytics' official public HF repo
        # Ultralytics/YOLO11 which provides the .pt file.
        "resource_id": "yolo_segmentation",
        "category": "Segmentation",
        "repo_id": "Ultralytics/YOLO11",
        "filename": "yolo11l-seg.pt",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["core_detection"],
        "required": True,
        "sha256": "cabe90049795dfc9a370b7934d6dec7f6b9e44a20e573b0ff81b7e205512c872",
    },
    {
        # SVDLUT 空间感知调色权重(自动修图) / SVDLUT color weight (auto-enhance)。
        "resource_id": "color_model",
        "category": "Enhance",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "svdlut.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["enhance"],
        "required": False,
        "sha256": "d4db6c5db125c271c71592c629375da92e4d6c975dbc5b8d637a3c66091bb6b1",
    },
    {
        # SCUNet 盲降噪权重(自动修图) / SCUNet denoise weight (auto-enhance)。
        "resource_id": "denoise_model",
        "category": "Enhance",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "scunet_color_real.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["enhance"],
        "required": False,
        "sha256": "fa78899ba2caec9d235a900e91d96c689da71c42029230c2028b00f09f809c2e",
    },
]

OPTIONAL_LOCAL_RESOURCES = [
    {
        "resource_id": "bird_reference_sqlite",
        "filename": "bird_reference.sqlite",
        "dest_dir": "birdid/data",
        "feature_tags": ["birdid"],
        "required": False,
        "sha256": None,
        "copy_only": True,
    },
    {
        "resource_id": "birdname_db",
        "filename": "birdname.db",
        "dest_dir": "ioc",
        "feature_tags": ["birdid"],
        "required": False,
        "sha256": None,
        "copy_only": True,
    },
]


def get_project_root() -> Path:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return Path(os.path.abspath(os.path.join(script_dir, "..")))


def _format_download_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = repr(exc)
    return f"{type(exc).__name__}: {message}"


def _normalize_endpoint(endpoint: str) -> str:
    """
    规范化 Hugging Face 端点地址，便于去重和比较。

    Normalize a Hugging Face endpoint URL for deduplication and comparison.
    """
    return (endpoint or "").strip().rstrip("/")


def _endpoint_name_for_url(endpoint: str) -> str:
    """
    为端点生成稳定的日志名称。

    Return a stable log name for an endpoint.
    """
    normalized = _normalize_endpoint(endpoint)
    if normalized == HF_OFFICIAL_ENDPOINT:
        return "official"
    if normalized == HF_MIRROR_ENDPOINT:
        return "hf-mirror"
    return "configured"


def _configured_hf_endpoint() -> str | None:
    """
    读取用户显式配置的 HF_ENDPOINT，不由应用主动覆盖。

    Read the user-configured HF_ENDPOINT without overriding it from the app.
    """
    endpoint = _normalize_endpoint(os.environ.get("HF_ENDPOINT", ""))
    return endpoint or None


def _hf_endpoint_candidates() -> List[Tuple[str, str]]:
    """
    构建候选端点池：用户显式配置优先，其后是官方与镜像。

    Build endpoint candidates: explicit user configuration first, then the
    official endpoint and the mirror.
    """
    candidates: List[Tuple[str, str]] = []
    seen: set[str] = set()

    def _add(endpoint: str | None, name: str | None = None) -> None:
        normalized = _normalize_endpoint(endpoint or "")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append((name or _endpoint_name_for_url(normalized), normalized))

    configured = _configured_hf_endpoint()
    _add(configured, _endpoint_name_for_url(configured or "") if configured else None)
    for name, endpoint in DOWNLOAD_ENDPOINTS:
        _add(endpoint, name)
    return candidates


def _hf_resolve_url(endpoint: str, repo_id: str, filename: str) -> str:
    """
    构造 Hugging Face 单文件 resolve 下载 URL。

    Build the Hugging Face single-file resolve download URL.
    """
    return f"{_normalize_endpoint(endpoint)}/{repo_id}/resolve/main/{filename}"


def _is_cancelled_exception(exc: BaseException) -> bool:
    """
    判断异常是否来自初始化取消回调。

    Return whether an exception came from the initialization cancellation hook.
    """
    return exc.__class__.__name__ in {"InitializationInterrupted", "DownloadInterrupted"}


def _check_cancelled(raise_if_cancelled: Optional[Callable[[], None]]) -> None:
    """
    执行取消检查；调用方传入的回调负责抛出业务异常。

    Run the cancellation check; the caller-provided hook raises the business
    interruption exception.
    """
    if raise_if_cancelled is not None:
        raise_if_cancelled()


def _sleep_before_retry(
    attempt_index: int,
    raise_if_cancelled: Optional[Callable[[], None]],
) -> None:
    """
    在重试前短暂退避，并在等待过程中响应取消。

    Back off briefly before retrying while still honoring cancellation.
    """
    deadline = time.perf_counter() + HF_DIRECT_RETRY_BASE_SECONDS * attempt_index
    while time.perf_counter() < deadline:
        _check_cancelled(raise_if_cancelled)
        time.sleep(0.2)


def _parse_content_range_total(content_range: str | None) -> int | None:
    """
    从 Content-Range 响应头提取远端总大小。

    Extract the remote total size from a Content-Range response header.
    """
    if not content_range or "/" not in content_range:
        return None
    total_text = content_range.rsplit("/", 1)[-1].strip()
    if not total_text.isdigit():
        return None
    return int(total_text)


def _authoritative_total_size(
    *,
    status_code: int,
    content_range: str | None,
    content_length: str | None,
    partial_bytes: int = 0,
) -> int | None:
    """
    从下载响应推导文件的权威总大小（字节）。

    优先使用 206 响应的 Content-Range 总数，其次使用 Content-Length；
    206 续传时用 partial_bytes + Content-Length 还原完整大小。该值来自服务器，
    必须用来「覆盖」而不是与来自 hf CLI 的四舍五入估算取 max——否则估算偏大时
    会把已经下载完整的文件误判为未完成（例如 Xet 文件真实 56096965 字节、
    估算 58825113）。无任何权威头时返回 None，由调用方退回估算值或交给
    下载后的 sha256/size 校验。

    Derive the authoritative total file size (in bytes) from a download response.
    Prefer the 206 Content-Range total, then Content-Length (a 206 resume is
    partial_bytes + Content-Length). This value comes from the server and must
    OVERRIDE rather than be max'd with the rounded hf CLI estimate; otherwise an
    over-estimate marks a complete download as incomplete. Returns None when the
    response carries no authoritative size header.

    参数 Parameters:
        status_code (int): 下载响应状态码 / Download response status code.
        content_range (str | None): Content-Range 响应头 / Content-Range header.
        content_length (str | None): Content-Length 响应头 / Content-Length header.
        partial_bytes (int): 已缓存的续传字节数 / Already-buffered resume bytes.

    返回 Returns:
        int | None: 权威总大小，或在无法判定时返回 None。
    """
    total_from_range = _parse_content_range_total(content_range)
    if total_from_range:
        return total_from_range
    if content_length is not None and str(content_length).isdigit():
        length_value = int(content_length)
        if status_code == 206:
            return partial_bytes + length_value
        return length_value
    return None


def _sha256_file(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_resource(resource: Dict[str, Any], file_path: Path) -> bool:
    expected_sha256 = resource.get("sha256")
    if not file_path.exists() or file_path.stat().st_size <= 0:
        return False
    if not expected_sha256:
        return True
    return _sha256_file(file_path) == expected_sha256.lower()


def _hf_endpoint_probe_download_ms(probe_result: Any) -> float:
    """
    估算端点下载固定样本所需时间。

    Estimate the time needed to download the fixed endpoint probe sample.
    """
    bytes_read = max(0, int(getattr(probe_result, "bytes_read", 0) or 0))
    sample_bytes = max(
        1,
        int(getattr(probe_result, "sample_bytes", 0) or HF_ENDPOINT_PROBE_SAMPLE_BYTES),
    )
    download_ms = max(
        0.0,
        float(probe_result.total_ms) - float(probe_result.first_byte_ms),
    )
    if bytes_read <= 0:
        return download_ms
    return download_ms * (sample_bytes / min(bytes_read, sample_bytes))


def _hf_endpoint_probe_score(probe_result: Any) -> tuple[float, float, float]:
    """
    按延时与样本下载速度给 HF 端点排序。

    Rank an HF endpoint by latency and sampled download speed.
    """
    first_byte_ms = max(0.0, float(probe_result.first_byte_ms))
    download_ms = _hf_endpoint_probe_download_ms(probe_result)
    score = (first_byte_ms * 0.35) + (download_ms * 0.65)
    return score, first_byte_ms, download_ms


def _rank_hf_endpoint_results(results: Iterable[Any]) -> list[Any]:
    """
    返回按双因子得分排序的可用 HF 端点。

    Return available HF endpoints sorted by the dual-factor score.
    """
    return sorted(
        [item for item in results if item.ok],
        key=_hf_endpoint_probe_score,
    )


def _resolve_hf_endpoints(
    repo_id: str | None = None,
    filename: str | None = None,
) -> List[Tuple[str, str]]:
    candidates = _hf_endpoint_candidates()
    if probe_sources_parallel is None:
        logging.info(
            "HF 端点探测不可用，使用候选顺序: %s",
            " -> ".join(f"{name}={endpoint}" for name, endpoint in candidates),
        )
        return candidates

    probe_input = []
    for name, endpoint in candidates:
        item = {
            "name": name,
            "url": endpoint,
            "source_kind": "hf",
            "probe_sample_bytes": str(HF_ENDPOINT_PROBE_SAMPLE_BYTES),
            "probe_timeout": str(HF_ENDPOINT_PROBE_TIMEOUT_SECONDS),
        }
        if repo_id and filename:
            item["probe_url"] = _hf_resolve_url(endpoint, repo_id, filename)
        probe_input.append(item)

    group_name = "huggingface-models"
    if repo_id and filename:
        group_name = f"huggingface-models:{repo_id}:{filename}"
    results = probe_sources_parallel(
        group_name,
        probe_input,
        timeout=HF_ENDPOINT_PROBE_TIMEOUT_SECONDS,
    )
    for item in results:
        if item.ok:
            score, first_byte_ms, download_ms = _hf_endpoint_probe_score(item)
            logging.info(
                "HF 端点探测: %s ok score=%.1fms first_byte=%.1fms "
                "sample_download=%.1fms bytes=%d/%d url=%s",
                item.name,
                score,
                first_byte_ms,
                download_ms,
                int(getattr(item, "bytes_read", 0) or 0),
                int(getattr(item, "sample_bytes", 0) or 0),
                item.url,
            )
        else:
            logging.warning(
                "HF 端点探测失败: %s error=%s url=%s",
                item.name,
                item.error or "unknown",
                item.url,
            )
    successful = _rank_hf_endpoint_results(results)
    if not successful:
        logging.warning(
            "HF 端点探测全部失败，回退候选顺序: %s",
            " -> ".join(f"{name}={endpoint}" for name, endpoint in candidates),
        )
        return candidates

    ranked: List[Tuple[str, str]] = []
    seen: set[str] = set()

    def _add(name: str, endpoint: str) -> None:
        normalized = _normalize_endpoint(endpoint)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ranked.append((name, normalized))

    for item in successful:
        _add(item.name, item.url)
    for name, endpoint in candidates:
        _add(name, endpoint)
    configured_endpoint = _configured_hf_endpoint()
    if configured_endpoint:
        configured_rank = next(
            (
                index + 1
                for index, (_name, endpoint) in enumerate(ranked)
                if _normalize_endpoint(endpoint) == configured_endpoint
            ),
            None,
        )
        logging.info(
            "HF_ENDPOINT 配置参与测速排序: endpoint=%s rank=%s",
            configured_endpoint,
            configured_rank or "fallback",
        )
    logging.info(
        "HF 端点最终排序: %s",
        " -> ".join(f"{name}={endpoint}" for name, endpoint in ranked),
    )
    return ranked


def _resource_matches_selection(resource: Dict[str, Any], selected: set[str]) -> bool:
    if resource.get("required"):
        return True
    feature_tags = set(resource.get("feature_tags", []))
    return not selected or bool(feature_tags & selected)


def _iter_selected_resources(
    resources: Iterable[Dict[str, Any]],
    selected_features: Optional[Iterable[str]],
) -> Iterator[Dict[str, Any]]:
    selected = set(selected_features or [])
    for item in resources:
        if _resource_matches_selection(item, selected):
            yield dict(item)


def resolve_download_plan(
    selected_features: Optional[Iterable[str]] = None,
    *,
    include_optional_local: bool = True,
) -> List[Dict[str, Any]]:
    plan = list(_iter_selected_resources(MODELS_TO_DOWNLOAD, selected_features))
    if include_optional_local:
        plan.extend(_iter_selected_resources(OPTIONAL_LOCAL_RESOURCES, selected_features))
    return plan


def _emit_resource_progress(
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]],
    event: InitializationProgressEvent,
) -> None:
    if progress_cb:
        progress_cb(event)


def _build_resource_progress_event(
    resource: Dict[str, Any],
    message: str,
    *,
    ratio: float | None = None,
    bytes_done: int | None = None,
    bytes_total: int | None = None,
    source: str | None = None,
    is_terminal: bool = False,
) -> InitializationProgressEvent:
    """
    Create a structured progress payload for one resource update.

    为单个资源更新创建结构化进度负载。
    """
    return InitializationProgressEvent(
        stage=STAGE_DOWNLOADING,
        progress_kind=PROGRESS_KIND_DOWNLOAD,
        message=message,
        ratio=ratio,
        bytes_done=bytes_done,
        bytes_total=bytes_total,
        resource_id=resource.get("resource_id"),
        source=source,
        is_terminal=is_terminal,
    )


def resolve_resource_destination_dir(project_root: Path, resource: Dict[str, Any]) -> Path:
    dest_dir = resource["dest_dir"]
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        dest_dir = resource.get("packaged_dest_dir", dest_dir)
    return project_root / dest_dir


def _copy_local_resource(
    resource: Dict[str, Any],
    project_root: Path,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
) -> Optional[Path]:
    """
    Copy a packaged local fallback resource into the expected destination.

    将打包时附带的本地回退资源复制到目标目录。
    """
    filename = resource["filename"]
    dest_dir = resolve_resource_destination_dir(project_root, resource)
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / filename

    if destination.exists():
        existing_size = destination.stat().st_size
        _emit_resource_progress(
            progress_cb,
            _build_resource_progress_event(
                resource,
                f"{filename} already present",
                ratio=1.0,
                bytes_done=existing_size,
                bytes_total=existing_size,
                is_terminal=True,
            ),
        )
        return destination

    local_candidates = [
        resolve_resource_destination_dir(project_root, resource) / filename,
        project_root / "resources" / resource["dest_dir"] / filename,
    ]
    for candidate in local_candidates:
        if candidate.exists():
            if candidate.resolve() != destination.resolve():
                destination.write_bytes(candidate.read_bytes())
            copied_size = destination.stat().st_size
            _emit_resource_progress(
                progress_cb,
                _build_resource_progress_event(
                    resource,
                    f"{filename} copied from local fallback",
                    ratio=1.0,
                    bytes_done=copied_size,
                    bytes_total=copied_size,
                    is_terminal=True,
                ),
            )
            return destination
    return None


# ---------------------------------------------------------------------------
#  Hf CLI helpers – 解析 `hf download --format json` 输出
#  Hf CLI helpers – parse `hf download --format json` output
# ---------------------------------------------------------------------------

_HF_CLI_CACHE: Optional[str] = None


def _resolve_hf_cli_path() -> Optional[str]:
    """Resolve the `hf` or `huggingface-cli` executable path, caching the result."""
    global _HF_CLI_CACHE
    if _HF_CLI_CACHE is not None:
        return _HF_CLI_CACHE
    _HF_CLI_CACHE = shutil.which("hf") or shutil.which("huggingface-cli") or ""
    if _HF_CLI_CACHE:
        logging.debug("hf CLI 已找到: %s", _HF_CLI_CACHE)
    else:
        logging.debug("hf CLI 未找到，将仅使用 httpx 直拉")
    return _HF_CLI_CACHE or None


def _hf_cli_kind(hf_path: str) -> str:
    """
    返回 Hugging Face CLI 类型：新版 hf 或旧版 huggingface-cli。

    Return the Hugging Face CLI kind: new `hf` or legacy `huggingface-cli`.
    """
    name = Path(hf_path).name.lower()
    return "huggingface-cli" if "huggingface-cli" in name else "hf"


def _estimate_file_size_via_cli(endpoint: str, repo_id: str, filename: str) -> int | None:
    """
    通过 `hf download --dry-run --format json` 获取远端文件大小。
    使用子进程方式，避免全局污染 HF_ENDPOINT 环境变量。

    Get remote file size via `hf download --dry-run --format json`.
    Uses subprocess to avoid polluting the global HF_ENDPOINT env var.
    """
    hf_path = _resolve_hf_cli_path()
    if not hf_path or _hf_cli_kind(hf_path) != "hf":
        return None

    env = os.environ.copy()
    env["HF_ENDPOINT"] = endpoint
    try:
        result = subprocess.run(
            [hf_path, "download", "--dry-run", "--format", "json", repo_id, filename],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            **_subprocess_no_window_kwargs(),
        )
        if result.returncode != 0:
            return None
        records: list[dict] = json.loads(result.stdout.strip())
        if not records:
            return None
        size_str = records[0].get("size", "")
        return _parse_hf_cli_size(size_str)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


def _parse_hf_cli_size(size_text: str) -> int | None:
    """
    解析 hf CLI JSON 输出中的文件大小（如 "107.8M"）。

    Parse file size from hf CLI JSON output (e.g. "107.8M").
    """
    if not size_text:
        return None
    size_text = size_text.strip().upper()
    # hf CLI 的人类可读大小使用十进制单位（M = 10^6），例如 56096965 字节显示为
    # "56.1M"。早前误用二进制（1024^2）换算会把它放大到 58825113（≈ ×1.048576），
    # 进而把完整下载误判为未完成。此处按十进制换算；该值仅作估算/进度用途，真正的
    # 完整性判定由 _authoritative_total_size 基于服务器响应头决定。
    # hf CLI human-readable sizes are decimal (M = 10^6): 56096965 bytes prints as
    # "56.1M". The previous binary (1024^2) parsing inflated it to 58825113
    # (~x1.048576), which marked complete downloads as incomplete. Parse as decimal;
    # this value is only an estimate/progress hint — real completeness is decided by
    # _authoritative_total_size from the server response headers.
    multipliers = {"K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}
    for suffix, multiplier in multipliers.items():
        if size_text.endswith(suffix):
            try:
                return int(float(size_text[:-1]) * multiplier)
            except ValueError:
                return None
    try:
        return int(size_text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
#  下载策略
#  Download strategies
# ---------------------------------------------------------------------------

def _download_via_cli(
    repo_id: str,
    filename: str,
    full_dest_dir: str,
    endpoint: str,
    source_name: str,
    *,
    expected_bytes: int | None = None,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    resource: Optional[Dict[str, Any]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    active_process_cb: Optional[
        Callable[[Optional[subprocess.Popen[str]]], None]
    ] = None,
) -> Optional[str]:
    """
    通过 `hf download` CLI 子进程下载文件。
    官方 hf-mirror.com 推荐的下载方式，正确设置 HF_ENDPOINT 即可使用镜像源。

    Download via `hf download` CLI subprocess.
    Recommended by hf-mirror.com; correctly uses the mirror by setting
    HF_ENDPOINT in the subprocess environment (no global pollution).

    参数 Parameters:
        repo_id (str): Hugging Face 仓库 ID
        filename (str): 要下载的文件名
        full_dest_dir (str): 目标目录路径
        endpoint (str): HF 端点 URL（含 https://）
        source_name (str): 端点名称（用于日志）
        expected_bytes (int | None): 预估文件大小
        progress_cb: 进度回调函数
        resource: 资源元数据字典
        raise_if_cancelled: 取消检查回调函数
        active_process_cb: 子进程登记回调函数

    返回 Returns:
        Optional[str]: 下载的文件路径，失败时返回 None
    """
    hf_path = _resolve_hf_cli_path()
    if not hf_path:
        logging.warning("hf CLI 不可用，跳过 CLI 下载")
        return None

    _check_cancelled(raise_if_cancelled)
    dest_path = Path(full_dest_dir) / filename

    env = os.environ.copy()
    env["HF_ENDPOINT"] = endpoint
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["HF_HUB_DISABLE_XET"] = "1"
    env["HF_HUB_DOWNLOAD_TIMEOUT"] = str(int(HF_HUB_DOWNLOAD_TIMEOUT_SECONDS))
    env["HF_HUB_ETAG_TIMEOUT"] = "30"
    env["DO_NOT_TRACK"] = "1"

    cli_kind = _hf_cli_kind(hf_path)
    cmd = [hf_path, "download"]
    if cli_kind == "huggingface-cli":
        cmd.append("--resume-download")
    cmd.extend([repo_id, filename, "--local-dir", full_dest_dir])
    if cli_kind == "hf":
        cmd.extend(["--format", "json"])

    logging.info("hf CLI: %s 尝试从 %s 下载 %s", source_name, endpoint, filename)

    _emit_resource_progress(
        progress_cb,
        _build_resource_progress_event(
            resource or _make_dummy_resource(repo_id, filename),
            f"{filename}: starting hf download via {source_name}",
            ratio=0.0 if expected_bytes else None,
            bytes_done=0,
            bytes_total=expected_bytes,
            source=source_name,
        ),
    )

    start = time.perf_counter()
    process: Optional[subprocess.Popen[str]] = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            **_subprocess_no_window_kwargs(),
        )
        if active_process_cb is not None:
            active_process_cb(process)

        stdout_text = ""
        stderr_text = ""
        deadline = start + HF_CLI_TIMEOUT_SECONDS
        while True:
            try:
                stdout_text, stderr_text = process.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                _check_cancelled(raise_if_cancelled)
                if time.perf_counter() >= deadline:
                    process.terminate()
                    try:
                        stdout_text, stderr_text = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        stdout_text, stderr_text = process.communicate(timeout=5)
                    raise

        elapsed = time.perf_counter() - start

        if process.returncode != 0:
            stderr_tail = stderr_text.strip()[-500:] if stderr_text else "(no stderr)"
            logging.warning(
                "hf CLI %s 下载失败 (exit=%d): %s",
                source_name,
                process.returncode,
                stderr_tail,
            )
            return None

        stdout_text = stdout_text.strip()
        if cli_kind == "hf" and stdout_text:
            try:
                records_obj = json.loads(stdout_text)
                if isinstance(records_obj, dict):
                    records = [records_obj]
                else:
                    records = list(records_obj)
            except (TypeError, json.JSONDecodeError):
                records = []
            if not records:
                logging.warning("hf CLI %s 输出非有效 JSON: %s...", source_name, stdout_text[:200])

        if not dest_path.exists():
            logging.warning("hf CLI %s 完成但目标文件不存在: %s", source_name, dest_path)
            return None

        file_size = dest_path.stat().st_size
        if file_size <= 0:
            logging.warning("hf CLI %s 完成但目标文件为空: %s", source_name, dest_path)
            dest_path.unlink(missing_ok=True)
            return None
        if expected_bytes and file_size < expected_bytes:
            logging.warning(
                "hf CLI %s 文件大小不足: %d < %d",
                source_name,
                file_size,
                expected_bytes,
            )
            dest_path.unlink(missing_ok=True)
            return None
        logging.info(
            "hf CLI 成功: %s 通过 %s 完成, %d 字节 (%.1f MB), "
            "耗时 %.2f 秒, 平均速度 %s",
            filename,
            source_name,
            file_size,
            file_size / 1048576,
            elapsed,
            _format_mib_per_second(file_size, elapsed),
        )

        if resource is not None and progress_cb is not None:
            _emit_resource_progress(
                progress_cb,
                _build_resource_progress_event(
                    resource,
                    f"{filename}: downloaded via {source_name} (hf CLI)",
                    ratio=1.0,
                    bytes_done=file_size,
                    bytes_total=file_size,
                    source=source_name,
                    is_terminal=True,
                ),
            )

        return str(dest_path)

    except subprocess.TimeoutExpired:
        logging.warning(
            "hf CLI %s 下载超时 (%ds)",
            source_name,
            int(HF_CLI_TIMEOUT_SECONDS),
        )
        return None
    except Exception as exc:
        if _is_cancelled_exception(exc):
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            raise
        logging.warning(
            "hf CLI %s 下载异常: %s",
            source_name,
            _format_download_error(exc),
        )
        return None
    finally:
        if active_process_cb is not None and process is not None:
            active_process_cb(None)


def _make_dummy_resource(repo_id: str, filename: str) -> Dict[str, Any]:
    """Create a minimal resource dict for progress events when no resource is provided."""
    return {"resource_id": f"{repo_id}/{filename}", "filename": filename}


def _download_via_huggingface_hub(
    repo_id: str,
    filename: str,
    full_dest_dir: str,
    endpoint: str,
    source_name: str,
    *,
    expected_bytes: int | None = None,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    resource: Optional[Dict[str, Any]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """
    使用应用内置的 huggingface_hub 下载文件。

    Download a file with the app-bundled huggingface_hub package.
    """
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:
        logging.warning("内置 huggingface_hub 不可用: %s", _format_download_error(exc))
        return None

    _check_cancelled(raise_if_cancelled)
    dest_path = Path(full_dest_dir) / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    effective_resource = resource or _make_dummy_resource(repo_id, filename)
    logging.info("内置 HF: 尝试从 %s 下载 %s", source_name, filename)
    _emit_resource_progress(
        progress_cb,
        _build_resource_progress_event(
            effective_resource,
            f"{filename}: starting built-in HF download via {source_name}",
            ratio=0.0 if expected_bytes else None,
            bytes_done=0,
            bytes_total=expected_bytes,
            source=source_name,
        ),
    )
    start = time.perf_counter()
    try:
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=full_dest_dir,
            endpoint=endpoint,
            etag_timeout=30,
            user_agent=HF_USER_AGENT,
        )
        _check_cancelled(raise_if_cancelled)
        downloaded_path = Path(downloaded)
        if downloaded_path != dest_path:
            dest_path.unlink(missing_ok=True)
            shutil.copy2(downloaded_path, dest_path)
        if not dest_path.exists():
            raise RuntimeError(f"built-in HF did not create {dest_path}")
        file_size = dest_path.stat().st_size
        if file_size <= 0:
            dest_path.unlink(missing_ok=True)
            raise RuntimeError("downloaded file is empty")
        if expected_bytes and file_size < expected_bytes:
            dest_path.unlink(missing_ok=True)
            raise RuntimeError(f"incomplete download: {file_size} < {expected_bytes}")
        elapsed = time.perf_counter() - start
        logging.info(
            "内置 HF 成功: %s 通过 %s 完成, %d 字节 (%.1f MB), "
            "耗时 %.2f 秒, 平均速度 %s",
            filename,
            source_name,
            file_size,
            file_size / 1048576,
            elapsed,
            _format_mib_per_second(file_size, elapsed),
        )
        _emit_resource_progress(
            progress_cb,
            _build_resource_progress_event(
                effective_resource,
                f"{filename}: downloaded via {source_name} (built-in HF)",
                ratio=1.0,
                bytes_done=file_size,
                bytes_total=file_size,
                source=source_name,
                is_terminal=True,
            ),
        )
        return str(dest_path)
    except Exception as exc:
        if _is_cancelled_exception(exc):
            raise
        logging.warning("内置 HF %s 下载失败: %s", source_name, _format_download_error(exc))
        return None


def _resolve_http_download_metadata(
    url: str,
    *,
    expected_bytes: int | None,
) -> HttpDownloadMetadata:
    """
    探测 HTTP 直拉的总大小与 Range 支持。

    Probe total size and Range support for HTTP direct download.
    """
    if httpx is None:
        return HttpDownloadMetadata(expected_bytes, False)

    headers = {
        "User-Agent": HF_USER_AGENT,
        "Range": "bytes=0-0",
    }
    timeout = httpx.Timeout(
        connect=HF_DIRECT_CONNECT_TIMEOUT_SECONDS,
        read=HF_DIRECT_READ_TIMEOUT_SECONDS,
        write=30.0,
        pool=30.0,
    )
    total_bytes = expected_bytes
    supports_range = False
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            response = client.get(url, headers=headers)
            if response.status_code == 206:
                supports_range = True
                # 服务器权威总数覆盖估算值；估算偏大时若取 max 会切出越界分段 → 416。
                # Server-authoritative total overrides the estimate; max() would keep
                # an over-estimate and build out-of-range segments → 416.
                authoritative_total = _authoritative_total_size(
                    status_code=response.status_code,
                    content_range=response.headers.get("content-range"),
                    content_length=response.headers.get("content-length"),
                )
                if authoritative_total:
                    total_bytes = authoritative_total
            elif 200 <= response.status_code < 400:
                accept_ranges = response.headers.get("accept-ranges", "").lower()
                supports_range = "bytes" in accept_ranges
                authoritative_total = _authoritative_total_size(
                    status_code=response.status_code,
                    content_range=response.headers.get("content-range"),
                    content_length=response.headers.get("content-length"),
                )
                if authoritative_total:
                    total_bytes = authoritative_total
            else:
                response.raise_for_status()
    except Exception as exc:
        logging.debug("HTTP 元数据探测失败 %s: %s", url, _format_download_error(exc))
    return HttpDownloadMetadata(total_bytes, supports_range)


def _build_download_segments(total_bytes: int) -> list[DownloadSegment]:
    """
    按固定任务块构建 HTTP Range 分段队列。

    Build an HTTP Range work queue. The worker count is capped separately, so
    finished workers can keep taking later segments instead of letting the
    transfer collapse to a few slow tail connections.
    """
    if total_bytes <= 0:
        return []
    part_size = HF_DIRECT_PARALLEL_MIN_PART_BYTES
    segments: list[DownloadSegment] = []
    for index, start in enumerate(range(0, total_bytes, part_size)):
        end = min(total_bytes - 1, start + part_size - 1)
        segments.append(DownloadSegment(index=index, start=start, end=end))
    return segments


def _segment_part_path(tmp_path: Path, segment: DownloadSegment) -> Path:
    """
    返回分段临时文件路径。

    Return the temporary file path for one segment.
    """
    return tmp_path.with_name(f"{tmp_path.name}.part{segment.index:02d}")


def _download_httpx_segment(
    *,
    url: str,
    segment: DownloadSegment,
    part_path: Path,
    progress_state: dict[str, int],
    progress_lock: threading.Lock,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]],
    resource: Dict[str, Any],
    source_name: str,
    total_bytes: int,
    raise_if_cancelled: Optional[Callable[[], None]],
) -> None:
    """
    下载单个 HTTP Range 分段，并更新聚合进度。

    Download one HTTP Range segment and update aggregate progress.
    """
    assert httpx is not None
    part_path.parent.mkdir(parents=True, exist_ok=True)
    expected_size = segment.size
    existing_size = part_path.stat().st_size if part_path.exists() else 0
    if existing_size > expected_size:
        part_path.unlink(missing_ok=True)
        existing_size = 0
    if existing_size == expected_size:
        return

    timeout = httpx.Timeout(
        connect=HF_DIRECT_CONNECT_TIMEOUT_SECONDS,
        read=HF_DIRECT_READ_TIMEOUT_SECONDS,
        write=30.0,
        pool=30.0,
    )
    last_error: Exception | None = None
    for attempt_index in range(1, HF_DIRECT_DOWNLOAD_ATTEMPTS + 1):
        try:
            _check_cancelled(raise_if_cancelled)
            resume_size = part_path.stat().st_size if part_path.exists() else 0
            if resume_size > expected_size:
                part_path.unlink(missing_ok=True)
                resume_size = 0
            if resume_size == expected_size:
                return
            range_start = segment.start + resume_size
            headers = {
                "User-Agent": HF_USER_AGENT,
                "Range": f"bytes={range_start}-{segment.end}",
            }
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    if response.status_code != 206:
                        raise RuntimeError("server ignored segment range")
                    bytes_written = resume_size
                    with part_path.open("ab" if resume_size else "wb") as handle:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            _check_cancelled(raise_if_cancelled)
                            if not chunk:
                                continue
                            handle.write(chunk)
                            chunk_size = len(chunk)
                            bytes_written += chunk_size
                            with progress_lock:
                                progress_state["done"] += chunk_size
                                done = min(progress_state["done"], total_bytes)
                            if progress_cb is not None:
                                ratio = min(1.0, done / total_bytes)
                                _emit_resource_progress(
                                    progress_cb,
                                    _build_resource_progress_event(
                                        resource,
                                        f"{resource['filename']}: downloading from {source_name} (parallel)",
                                        ratio=ratio,
                                        bytes_done=done,
                                        bytes_total=total_bytes,
                                        source=source_name,
                                        is_terminal=ratio >= 1.0,
                                    ),
                                )
            if bytes_written != expected_size:
                raise RuntimeError(
                    f"segment {segment.index} incomplete: {bytes_written} < {expected_size}"
                )
            return
        except Exception as exc:
            if _is_cancelled_exception(exc):
                raise
            last_error = exc
            logging.warning(
                "httpx 分段直拉 %s part=%d 失败 (%d/%d): %s",
                source_name,
                segment.index,
                attempt_index,
                HF_DIRECT_DOWNLOAD_ATTEMPTS,
                _format_download_error(exc),
            )
            if attempt_index < HF_DIRECT_DOWNLOAD_ATTEMPTS:
                _sleep_before_retry(attempt_index, raise_if_cancelled)
    raise RuntimeError(
        f"segment {segment.index} failed: {_format_download_error(last_error or RuntimeError('unknown'))}"
    )


def _combine_httpx_segments(
    *,
    tmp_path: Path,
    dest_path: Path,
    segments: list[DownloadSegment],
    total_bytes: int,
) -> None:
    """
    合并分段文件为最终目标文件。

    Combine segment files into the final destination file.
    """
    with tmp_path.open("wb") as output:
        for segment in segments:
            part_path = _segment_part_path(tmp_path, segment)
            if not part_path.exists() or part_path.stat().st_size != segment.size:
                raise RuntimeError(f"segment {segment.index} is missing or incomplete")
            with part_path.open("rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    if tmp_path.stat().st_size != total_bytes:
        raise RuntimeError(
            f"combined download size mismatch: {tmp_path.stat().st_size} != {total_bytes}"
        )
    dest_path.unlink(missing_ok=True)
    tmp_path.rename(dest_path)
    for segment in segments:
        _segment_part_path(tmp_path, segment).unlink(missing_ok=True)


def _download_via_httpx_parallel(
    repo_id: str,
    filename: str,
    full_dest_dir: str,
    endpoint: str,
    source_name: str,
    *,
    expected_bytes: int | None = None,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    resource: Optional[Dict[str, Any]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """
    使用最多 16 个 HTTP Range 分段并行直拉。

    Direct-download with up to 16 HTTP Range segments in parallel.
    """
    if httpx is None:
        return None
    _check_cancelled(raise_if_cancelled)
    dest_path = Path(full_dest_dir) / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    url = _hf_resolve_url(endpoint, repo_id, filename)
    metadata = _resolve_http_download_metadata(url, expected_bytes=expected_bytes)
    total_bytes = metadata.total_bytes
    if not metadata.supports_range:
        logging.info(
            "httpx 并行直拉跳过: %s source=%s reason=range_not_supported total=%s",
            filename,
            source_name,
            total_bytes,
        )
        return None
    if total_bytes is None:
        logging.info(
            "httpx 并行直拉跳过: %s source=%s reason=unknown_total_size",
            filename,
            source_name,
        )
        return None
    if total_bytes < HF_DIRECT_PARALLEL_MIN_BYTES:
        logging.info(
            "httpx 并行直拉跳过: %s source=%s reason=file_too_small "
            "total=%d threshold=%d",
            filename,
            source_name,
            total_bytes,
            HF_DIRECT_PARALLEL_MIN_BYTES,
        )
        return None

    segments = _build_download_segments(total_bytes)
    if len(segments) <= 1:
        logging.info(
            "httpx 并行直拉跳过: %s source=%s reason=single_segment total=%d",
            filename,
            source_name,
            total_bytes,
        )
        return None

    effective_resource = resource or _make_dummy_resource(repo_id, filename)
    for segment in segments:
        part_path = _segment_part_path(tmp_path, segment)
        if part_path.exists() and part_path.stat().st_size > segment.size:
            part_path.unlink(missing_ok=True)
    initial_done = sum(
        min(_segment_part_path(tmp_path, segment).stat().st_size, segment.size)
        for segment in segments
        if _segment_part_path(tmp_path, segment).exists()
    )
    worker_count = min(HF_DIRECT_PARALLEL_THREADS, len(segments))
    progress_state = {"done": initial_done}
    progress_lock = threading.Lock()
    logging.info(
        "httpx 并行直拉: %s 使用 %d 线程、%d 个任务块从 %s 下载 %.1f MB",
        filename,
        worker_count,
        len(segments),
        source_name,
        total_bytes / 1048576,
    )
    _emit_resource_progress(
        progress_cb,
        _build_resource_progress_event(
            effective_resource,
            f"{filename}: starting parallel httpx download via {source_name}",
            ratio=initial_done / total_bytes,
            bytes_done=initial_done,
            bytes_total=total_bytes,
            source=source_name,
        ),
    )

    start = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="sp-hf") as executor:
            futures = [
                executor.submit(
                    _download_httpx_segment,
                    url=url,
                    segment=segment,
                    part_path=_segment_part_path(tmp_path, segment),
                    progress_state=progress_state,
                    progress_lock=progress_lock,
                    progress_cb=progress_cb,
                    resource=effective_resource,
                    source_name=source_name,
                    total_bytes=total_bytes,
                    raise_if_cancelled=raise_if_cancelled,
                )
                for segment in segments
            ]
            for future in as_completed(futures):
                _check_cancelled(raise_if_cancelled)
                future.result()
        _combine_httpx_segments(
            tmp_path=tmp_path,
            dest_path=dest_path,
            segments=segments,
            total_bytes=total_bytes,
        )
        file_size = dest_path.stat().st_size
        elapsed = time.perf_counter() - start
        logging.info(
            "httpx 并行直拉成功: %s 通过 %s 完成, %d 字节 (%.1f MB), "
            "耗时 %.2f 秒, 平均速度 %s",
            filename,
            source_name,
            file_size,
            file_size / 1048576,
            elapsed,
            _format_mib_per_second(file_size, elapsed),
        )
        _emit_resource_progress(
            progress_cb,
            _build_resource_progress_event(
                effective_resource,
                f"{filename}: downloaded via {source_name} (parallel httpx)",
                ratio=1.0,
                bytes_done=file_size,
                bytes_total=file_size,
                source=source_name,
                is_terminal=True,
            ),
        )
        return str(dest_path)
    except Exception as exc:
        if _is_cancelled_exception(exc):
            raise
        for segment in segments:
            _segment_part_path(tmp_path, segment).unlink(missing_ok=True)
        logging.warning("httpx 并行直拉 %s 失败: %s", source_name, _format_download_error(exc))
        return None


def _download_via_httpx(
    repo_id: str,
    filename: str,
    full_dest_dir: str,
    endpoint: str,
    source_name: str,
    *,
    expected_bytes: int | None = None,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    resource: Optional[Dict[str, Any]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """
    用 httpx 流式 GET 直拉下载备用方案。
    `follow_redirects=True` 可正确处理 HTTP 308 Permanent Redirect
    （hf-mirror.com 使用 308 跳转到外部 CDN）。

    Fallback download via httpx streaming GET.
    `follow_redirects=True` correctly handles HTTP 308 Permanent Redirect
    used by hf-mirror.com to redirect to external CDN.

    参数 Parameters:
        repo_id (str): Hugging Face 仓库 ID
        filename (str): 要下载的文件名
        full_dest_dir (str): 目标目录路径
        endpoint (str): HF 端点 URL（含 https://）
        source_name (str): 端点名称（用于日志）
        expected_bytes (int | None): 预估文件大小
        progress_cb: 进度回调函数
        resource: 资源元数据字典

    返回 Returns:
        Optional[str]: 下载的文件路径，失败时返回 None
    """
    if httpx is None:
        logging.warning("httpx 不可用，跳过直拉下载")
        return None

    _check_cancelled(raise_if_cancelled)
    parallel_result = _download_via_httpx_parallel(
        repo_id=repo_id,
        filename=filename,
        full_dest_dir=full_dest_dir,
        endpoint=endpoint,
        source_name=source_name,
        expected_bytes=expected_bytes,
        progress_cb=progress_cb,
        resource=resource,
        raise_if_cancelled=raise_if_cancelled,
    )
    if parallel_result:
        return parallel_result

    dest_path = Path(full_dest_dir) / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    url = f"{endpoint.rstrip('/')}/{repo_id}/resolve/main/{filename}"

    effective_resource = resource or _make_dummy_resource(repo_id, filename)

    logging.info("httpx 直拉: 尝试 %s → %s", source_name, url)

    _emit_resource_progress(
        progress_cb,
        _build_resource_progress_event(
            effective_resource,
            f"{filename}: starting httpx download via {source_name}",
            ratio=0.0 if expected_bytes else None,
            bytes_done=0,
            bytes_total=expected_bytes,
            source=source_name,
        ),
    )

    start = time.perf_counter()
    timeout = httpx.Timeout(
        connect=HF_DIRECT_CONNECT_TIMEOUT_SECONDS,
        read=HF_DIRECT_READ_TIMEOUT_SECONDS,
        write=30.0,
        pool=30.0,
    )
    last_error: Exception | None = None

    for attempt_index in range(1, HF_DIRECT_DOWNLOAD_ATTEMPTS + 1):
        try:
            _check_cancelled(raise_if_cancelled)
            partial_bytes = tmp_path.stat().st_size if tmp_path.exists() else 0
            if expected_bytes and partial_bytes > expected_bytes:
                tmp_path.unlink(missing_ok=True)
                partial_bytes = 0

            headers = {"User-Agent": HF_USER_AGENT}
            if partial_bytes > 0:
                headers["Range"] = f"bytes={partial_bytes}-"

            logging.info(
                "httpx 直拉: %s 第 %d/%d 次尝试，已缓存 %d 字节",
                source_name,
                attempt_index,
                HF_DIRECT_DOWNLOAD_ATTEMPTS,
                partial_bytes,
            )

            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code == 416:
                        # 416 的 Content-Range（bytes */TOTAL）给出权威总数；据此判断
                        # 已缓存的 .part 是否其实已完整，避免误删完好文件。
                        # A 416 Content-Range (bytes */TOTAL) carries the authoritative
                        # total; use it to detect an already-complete .part instead of
                        # deleting a good file.
                        resume_target = _authoritative_total_size(
                            status_code=resp.status_code,
                            content_range=resp.headers.get("content-range"),
                            content_length=resp.headers.get("content-length"),
                        ) or expected_bytes
                        if resume_target and partial_bytes >= resume_target:
                            dest_path.unlink(missing_ok=True)
                            tmp_path.rename(dest_path)
                            return str(dest_path)
                        tmp_path.unlink(missing_ok=True)
                        raise RuntimeError("resume range is not satisfiable")

                    resp.raise_for_status()
                    if partial_bytes > 0 and resp.status_code != 206:
                        tmp_path.unlink(missing_ok=True)
                        raise RuntimeError("server ignored resume range")

                    # 服务器权威大小覆盖 hf CLI 估算值；只有它能作为完整性下限。
                    # Server-authoritative size overrides the hf CLI estimate and is
                    # the only valid completeness floor.
                    authoritative_total = _authoritative_total_size(
                        status_code=resp.status_code,
                        content_range=resp.headers.get("content-range"),
                        content_length=resp.headers.get("content-length"),
                        partial_bytes=partial_bytes,
                    )
                    if authoritative_total:
                        expected_bytes = authoritative_total

                    bytes_written = partial_bytes if resp.status_code == 206 else 0
                    file_mode = "ab" if bytes_written else "wb"
                    with open(tmp_path, file_mode) as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            _check_cancelled(raise_if_cancelled)
                            if not chunk:
                                continue
                            f.write(chunk)
                            bytes_written += len(chunk)
                            if (
                                progress_cb is not None
                                and expected_bytes
                                and expected_bytes > 0
                            ):
                                ratio = min(1.0, bytes_written / expected_bytes)
                                _emit_resource_progress(
                                    progress_cb,
                                    _build_resource_progress_event(
                                        effective_resource,
                                        f"{filename}: downloading from {source_name}",
                                        ratio=ratio,
                                        bytes_done=bytes_written,
                                        bytes_total=expected_bytes,
                                        source=source_name,
                                        is_terminal=ratio >= 1.0,
                                    ),
                                )
                    # 仅以服务器权威大小判定未完成；只有估算值时不硬判，交由
                    # 下载后的 sha256/size 校验，避免四舍五入估算导致误报。
                    # Only flag "incomplete" against the server-authoritative size; when
                    # only an estimate exists, defer to post-download sha256/size checks
                    # so a rounded estimate cannot cause a false negative.
                    if authoritative_total and bytes_written < authoritative_total:
                        raise RuntimeError(
                            f"incomplete download: {bytes_written} < {authoritative_total}"
                        )

            elapsed = time.perf_counter() - start

            dest_path.unlink(missing_ok=True)
            tmp_path.rename(dest_path)
            file_size = dest_path.stat().st_size
            logging.info(
                "httpx 直拉成功: %s 通过 %s 完成, %d 字节 (%.1f MB), "
                "耗时 %.2f 秒, 平均速度 %s",
                filename,
                source_name,
                file_size,
                file_size / 1048576,
                elapsed,
                _format_mib_per_second(file_size, elapsed),
            )

            if resource is not None and progress_cb is not None:
                _emit_resource_progress(
                    progress_cb,
                    _build_resource_progress_event(
                        effective_resource,
                        f"{filename}: downloaded via {source_name} (httpx direct)",
                        ratio=1.0,
                        bytes_done=file_size,
                        bytes_total=file_size,
                        source=source_name,
                        is_terminal=True,
                    ),
                )

            return str(dest_path)

        except Exception as exc:
            if _is_cancelled_exception(exc):
                raise
            last_error = exc
            logging.warning(
                "httpx 直拉 %s 失败 (%d/%d): %s",
                source_name,
                attempt_index,
                HF_DIRECT_DOWNLOAD_ATTEMPTS,
                _format_download_error(exc),
            )
            if attempt_index < HF_DIRECT_DOWNLOAD_ATTEMPTS:
                _sleep_before_retry(attempt_index, raise_if_cancelled)

    if last_error is not None:
        logging.warning(
            "httpx 直拉 %s 最终失败: %s",
            source_name,
            _format_download_error(last_error),
        )
    return None


def _urllib_direct_download(
    repo_id: str,
    filename: str,
    full_dest_dir: str,
    endpoints: List[Tuple[str, str]],
    expected_bytes: int | None = None,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    resource: Optional[Dict[str, Any]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """
    Final raw URL fallback using urllib.

    使用 urllib 直拉 raw URL 的最终兜底方案。
    """
    import urllib.request

    dest_path = Path(full_dest_dir) / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    effective_resource = resource or _make_dummy_resource(repo_id, filename)

    for source_name, endpoint in endpoints:
        _check_cancelled(raise_if_cancelled)
        url = f"{endpoint.rstrip('/')}/{repo_id}/resolve/main/{filename}"
        logging.info("urllib 直拉: 尝试 %s → %s", source_name, url)
        _emit_resource_progress(
            progress_cb,
            _build_resource_progress_event(
                effective_resource,
                f"{filename}: starting urllib download via {source_name}",
                ratio=0.0 if expected_bytes else None,
                bytes_done=0,
                bytes_total=expected_bytes,
                source=source_name,
            ),
        )
        try:
            partial_bytes = tmp_path.stat().st_size if tmp_path.exists() else 0
            if expected_bytes and partial_bytes > expected_bytes:
                tmp_path.unlink(missing_ok=True)
                partial_bytes = 0
            headers = {"User-Agent": HF_USER_AGENT}
            if partial_bytes > 0:
                headers["Range"] = f"bytes={partial_bytes}-"
            request = urllib.request.Request(
                url,
                headers=headers,
            )
            start = time.perf_counter()
            bytes_written = partial_bytes
            with urllib.request.urlopen(
                request,
                timeout=HF_DIRECT_READ_TIMEOUT_SECONDS,
            ) as response:
                status_code = response.getcode()
                if partial_bytes > 0 and status_code != 206:
                    tmp_path.unlink(missing_ok=True)
                    raise RuntimeError("server ignored resume range")
                # 服务器权威大小覆盖估算值；只有它能作为完整性下限。
                # Server-authoritative size overrides the estimate and is the only
                # valid completeness floor.
                authoritative_total = _authoritative_total_size(
                    status_code=status_code,
                    content_range=response.headers.get("Content-Range"),
                    content_length=response.headers.get("Content-Length"),
                    partial_bytes=partial_bytes,
                )
                if authoritative_total:
                    expected_bytes = authoritative_total
                file_mode = "ab" if partial_bytes > 0 else "wb"
                with open(tmp_path, file_mode) as handle:
                    while True:
                        _check_cancelled(raise_if_cancelled)
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        bytes_written += len(chunk)
                        if progress_cb is not None and expected_bytes and expected_bytes > 0:
                            ratio = min(1.0, bytes_written / expected_bytes)
                            _emit_resource_progress(
                                progress_cb,
                                _build_resource_progress_event(
                                    effective_resource,
                                    f"{filename}: downloading from {source_name} (urllib)",
                                    ratio=ratio,
                                    bytes_done=bytes_written,
                                    bytes_total=expected_bytes,
                                    source=source_name,
                                    is_terminal=ratio >= 1.0,
                                ),
                            )
            # 仅以服务器权威大小判定未完成；只有估算值时交由 sha256/size 校验。
            # Only flag "incomplete" against the server-authoritative size; defer to
            # post-download sha256/size checks when only an estimate exists.
            if authoritative_total and bytes_written < authoritative_total:
                raise RuntimeError(
                    f"incomplete download: {bytes_written} < {authoritative_total}"
                )
            dest_path.unlink(missing_ok=True)
            tmp_path.rename(dest_path)
            file_size = dest_path.stat().st_size
            if file_size <= 0:
                raise RuntimeError("downloaded file is empty")
            elapsed = time.perf_counter() - start
            logging.info(
                "urllib 直拉成功: %s 通过 %s 完成, %d 字节 (%.1f MB), "
                "耗时 %.2f 秒, 平均速度 %s",
                filename,
                source_name,
                file_size,
                file_size / 1048576,
                elapsed,
                _format_mib_per_second(file_size, elapsed),
            )
            _emit_resource_progress(
                progress_cb,
                _build_resource_progress_event(
                    effective_resource,
                    f"{filename}: downloaded via {source_name} (urllib fallback)",
                    ratio=1.0,
                    bytes_done=file_size,
                    bytes_total=file_size,
                    source=source_name,
                    is_terminal=True,
                ),
            )
            return str(dest_path)
        except Exception as exc:
            if _is_cancelled_exception(exc):
                raise
            logging.warning("urllib 直拉 %s 失败: %s", source_name, _format_download_error(exc))
    return None


# ---------------------------------------------------------------------------
#  下载编排器
#  Download orchestrator
# ---------------------------------------------------------------------------

def _download_with_fallback(
    resource: Dict[str, Any],
    repo_id: str,
    filename: str,
    full_dest_dir: str,
    *,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    active_process_cb: Optional[
        Callable[[Optional[subprocess.Popen[str]]], None]
    ] = None,
) -> Optional[str]:
    """
    逐源尝试下载：并行 httpx 直拉 → Hugging Face CLI → 内置 HF → 下一源。

    Try download per endpoint: parallel httpx direct download → Hugging Face
    CLI → bundled Hugging Face Hub → next endpoint.

    返回 Returns:
        Optional[str]: 下载的文件路径，全部失败则返回 None
    """
    _check_cancelled(raise_if_cancelled)
    endpoints = _resolve_hf_endpoints(repo_id, filename)

    # ── 预估文件大小：新版 hf CLI 支持 JSON dry-run，失败时由响应头补足。 ──
    # Pre-estimate file size when the new hf CLI supports JSON dry-run; when it
    # fails, streaming response headers provide the fallback estimate.
    expected_bytes: int | None = None
    for _name, _endpoint in endpoints:
        _check_cancelled(raise_if_cancelled)
        expected_bytes = _estimate_file_size_via_cli(_endpoint, repo_id, filename)
        if expected_bytes is not None:
            break

    for index, (source_name, endpoint) in enumerate(endpoints):
        _check_cancelled(raise_if_cancelled)
        logging.info(
            "下载策略: 尝试源 %d/%d %s (%s), 文件=%s, 预估大小=%s",
            index + 1,
            len(endpoints),
            source_name,
            endpoint,
            filename,
            expected_bytes if expected_bytes is not None else "unknown",
        )

        # ── 主方案：httpx 直拉；内部会先尝试 Range 并行下载。 ────────────
        # Primary path: httpx direct download; it tries Range parallelism first.
        logging.info("下载策略: %s 优先尝试 httpx 并行/直拉", source_name)
        httpx_result = _download_via_httpx(
            repo_id=repo_id,
            filename=filename,
            full_dest_dir=full_dest_dir,
            endpoint=endpoint,
            source_name=source_name,
            expected_bytes=expected_bytes,
            progress_cb=progress_cb,
            resource=resource,
            raise_if_cancelled=raise_if_cancelled,
        )
        if httpx_result:
            return httpx_result

        logging.info("下载策略: httpx %s 未完成，尝试 hf CLI 兜底", source_name)
        cli_result = _download_via_cli(
            repo_id=repo_id,
            filename=filename,
            full_dest_dir=full_dest_dir,
            endpoint=endpoint,
            source_name=source_name,
            expected_bytes=expected_bytes,
            progress_cb=progress_cb,
            resource=resource,
            raise_if_cancelled=raise_if_cancelled,
            active_process_cb=active_process_cb,
        )
        if cli_result:
            return cli_result

        logging.info("下载策略: hf CLI %s 未完成，尝试内置 HF 下载", source_name)
        hub_result = _download_via_huggingface_hub(
            repo_id=repo_id,
            filename=filename,
            full_dest_dir=full_dest_dir,
            endpoint=endpoint,
            source_name=source_name,
            expected_bytes=expected_bytes,
            progress_cb=progress_cb,
            resource=resource,
            raise_if_cancelled=raise_if_cancelled,
        )
        if hub_result:
            return hub_result

        if index < len(endpoints) - 1:
            next_source_name = endpoints[index + 1][0]
            logging.info("(%s) 切换到下一个源下载 %s...", next_source_name, filename)

    logging.error(
        "所有下载源均失败: %s 来自 %s",
        filename,
        repo_id,
    )
    return _urllib_direct_download(
        repo_id=repo_id,
        filename=filename,
        full_dest_dir=full_dest_dir,
        endpoints=endpoints,
        expected_bytes=expected_bytes,
        progress_cb=progress_cb,
        resource=resource,
        raise_if_cancelled=raise_if_cancelled,
    )


# ---------------------------------------------------------------------------
#  公共入口
#  Public API
# ---------------------------------------------------------------------------

def download_resource(
    resource: Dict[str, Any],
    *,
    project_root: Optional[Path] = None,
    progress_cb: Optional[Callable[[InitializationProgressEvent], None]] = None,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    active_process_cb: Optional[Callable[[Optional[subprocess.Popen[str]]], None]] = None,
) -> Path:
    """
    下载并验证资源文件。

    Download and verify resource file.

    参数 Parameters:
        resource (Dict[str, Any]): 资源元数据字典
        project_root (Optional[Path]): 项目根目录
        progress_cb (Optional[Callable[[InitializationProgressEvent], None]]): 进度回调函数
        raise_if_cancelled (Optional[Callable[[], None]]): 取消检查回调函数
        active_process_cb: 子进程登记回调

    返回 Returns:
        Path: 下载的文件路径

    异常 Raises:
        FileNotFoundError: 本地回退资源未找到
        RuntimeError: 下载失败或完整性验证失败
    """
    _check_cancelled(raise_if_cancelled)
    project_root = project_root or get_project_root()

    if resource.get("copy_only"):
        copied = _copy_local_resource(resource, project_root, progress_cb=progress_cb)
        if copied is None:
            raise FileNotFoundError(f"Local fallback resource not found: {resource['filename']}")
        return copied

    repo_id = resource["repo_id"]
    filename = resource["filename"]
    resource_id = resource.get("resource_id", "unknown")
    full_dest_dir = resolve_resource_destination_dir(project_root, resource)
    full_dest_dir.mkdir(parents=True, exist_ok=True)

    logging.info(
        "开始下载资源 [%s]: %s 来自仓库 %s",
        resource_id,
        filename,
        repo_id
    )

    _emit_resource_progress(
        progress_cb,
        _build_resource_progress_event(
            resource,
            f"Preparing download for {filename}",
            ratio=0.0,
            bytes_done=0,
            bytes_total=None,
        ),
    )

    download_start_time = time.perf_counter()
    downloaded_path = _download_with_fallback(
        resource=resource,
        repo_id=repo_id,
        filename=filename,
        full_dest_dir=str(full_dest_dir),
        progress_cb=progress_cb,
        raise_if_cancelled=raise_if_cancelled,
        active_process_cb=active_process_cb,
    )
    download_elapsed = time.perf_counter() - download_start_time

    if not downloaded_path:
        logging.error(
            "资源 [%s] 下载失败: %s 来自 %s，总耗时 %.2f 秒",
            resource_id,
            filename,
            repo_id,
            download_elapsed
        )
        raise RuntimeError(f"Failed to download {filename} from {repo_id}")

    path_obj = Path(downloaded_path)
    file_size = path_obj.stat().st_size if path_obj.exists() else 0

    logging.info(
        "资源 [%s] 下载文件大小: %d 字节 (%.2f MB)",
        resource_id,
        file_size,
        file_size / (1024 * 1024)
    )

    if not verify_resource(resource, path_obj):
        path_obj.unlink(missing_ok=True)
        logging.error(
            "资源 [%s] 完整性验证失败: %s",
            resource_id,
            filename
        )
        raise RuntimeError(f"Integrity verification failed for {filename}")

    logging.info(
        "资源 [%s] 下载并验证成功: %s，总耗时 %.2f 秒，文件大小 %.2f MB",
        resource_id,
        filename,
        download_elapsed,
        file_size / (1024 * 1024)
    )
    _emit_resource_progress(
        progress_cb,
        _build_resource_progress_event(
            resource,
            f"Validated {filename}",
            ratio=1.0,
            bytes_done=file_size,
            bytes_total=file_size,
            is_terminal=True,
        ),
    )
    return path_obj


def main():
    """
    Downloads required models and database files from Hugging Face Hub.
    Ensures files are placed in the correct directories for the application to function.
    """
    logging.info("Starting model download process...")
    if _resolve_hf_cli_path() is None and httpx is None:
        logging.warning(
            "Neither `hf`/`huggingface-cli` nor `httpx` is available; "
            "falling back to urllib direct downloads."
        )

    project_root = get_project_root()
    os.chdir(project_root)
    logging.info("Working directory set to: %s", project_root)

    # ExtremeSimple: 「enhance」(SVDLUT 调色 + SCUNet 降噪) 已从此打包下载集合剥离。
    # FULL_FEATURE_SET(core/initialization_manager.py、welcome_onboarding_dialog.py)
    # 本就不含 "enhance"，运行时从不需要这两个权重；但打包时 build_release_*.py
    # 会先调这个 main() 把模型下载到本地 models/，再被 .spec 的整目录打包规则
    # (os.path.join(base_path, 'models'), 'models') 原样带进 Mac/Windows Full 安装包。
    # 去掉 "enhance" 后打包机器不会再下载 svdlut.pth/scunet_color_real.pth，
    # 安装包也就不会再带上这约 73MB 的死重。未来要恢复 Enhance 功能时把
    # "enhance" 加回这个集合即可。
    # ExtremeSimple: "enhance" (SVDLUT color + SCUNet denoise) is stripped from
    # this packaging download set. FULL_FEATURE_SET already excludes "enhance",
    # so runtime never needs these weights; but build_release_*.py calls this
    # main() to populate the local models/ dir before PyInstaller runs, and the
    # .spec files' whole-directory bundling rule sweeps whatever is there into
    # the Mac/Windows Full installers. Dropping "enhance" here stops the build
    # machine from fetching svdlut.pth/scunet_color_real.pth, so the ~73MB of
    # dead weight no longer ships. Re-add "enhance" to restore it.
    plan = resolve_download_plan(
        {"core_detection", "quality", "keypoint", "flight", "birdid"},
        include_optional_local=False,
    )
    success_count = 0

    for item in plan:
        logging.info("[%s] Retrieving %s...", item.get("category", "Resource"), item["filename"])
        try:
            downloaded_path = download_resource(item, project_root=project_root)
            logging.info("✓ Successfully downloaded/verified: %s", os.path.basename(downloaded_path))
            success_count += 1
        except Exception as exc:
            logging.error("✗ Failed to prepare %s: %s", item["filename"], _format_download_error(exc))

    if success_count == len(plan):
        logging.info("All %s files are ready.", len(plan))
        logging.info("Application resources are ready to run.")
        sys.exit(0)

    logging.error("Only %s/%s files were successfully prepared.", success_count, len(plan))
    sys.exit(1)


if __name__ == "__main__":
    main()
