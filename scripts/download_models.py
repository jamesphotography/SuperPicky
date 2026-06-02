"""
Model and resource download helpers for lightweight initialization.

This module prepares model files and local fallback resources needed by the
welcome onboarding flow. It emits structured progress events so callers can
aggregate real byte progress, item-level progress, and source retry state
without scraping ad-hoc log text.

Download strategy (aligned with hf-mirror.com official guidance):
  1. Probe both hf-mirror.com and huggingface.co simultaneously.
  2. Sort by latency; apply 2x ratio threshold so overseas CI isn't forced
     onto a slow mirror.
  3. For each endpoint, try `hf download` CLI (subprocess) first, then
     httpx direct streaming as fallback.

轻量化初始化所需的模型与资源下载辅助模块。

此模块负责准备欢迎引导流程所需的模型文件与本地回退资源，并发出结构化进度事件，
以便调用方能够聚合真实字节进度、条目级进度以及镜像重试状态，而不必再解析零散日志文本。

下载策略（对齐 hf-mirror.com 官方指南）：
  1. 同时探测 hf-mirror.com 和 huggingface.co。
  2. 按延迟排序；应用 2x 比率阈值避免海外 CI 被强行引向慢镜像。
  3. 逐源：先 `hf download` CLI（子进程），失败则 httpx 流式直拉兜底。
"""

import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
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
    from core.source_probe import pick_best_source, probe_sources
except Exception:
    pick_best_source = None
    probe_sources = None

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
    ("hf-mirror", HF_MIRROR_ENDPOINT),
    ("official", HF_OFFICIAL_ENDPOINT),
]

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
        "sha256": None,
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
        "sha256": None,
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
        "sha256": None,
    },
    {
        "resource_id": "avonet_database",
        "category": "Database",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "avonet.db",
        "dest_dir": "birdid/data",
        "feature_tags": ["birdid"],
        "required": False,
        "sha256": None,
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
        "sha256": None,
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
        "sha256": None,
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


# 镜像源相对官方源的最大可接受延迟倍率：mirror 延迟 ≤ official 延迟 × 此值
# 时优先用 mirror，否则用 official。与 core/initialization_manager.py 的
# PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD 保持一致。修复 GitHub Actions runner
# (海外) 跑 CI 时强行用 hf-mirror.com (慢 20+ 倍) 导致下载失败的问题。
# Match initialization_manager's 2x ratio rule for endpoint selection. Fixes
# the case where GitHub Actions (overseas) was forced onto hf-mirror.com — its
# probe endpoint returned 200 but the download endpoint rejected runner IPs.
HF_ENDPOINT_MIRROR_RATIO_THRESHOLD = 2.0


def _resolve_hf_endpoints() -> List[Tuple[str, str]]:
    if probe_sources is None or pick_best_source is None:
        return list(DOWNLOAD_ENDPOINTS)

    probe_input = [{"name": name, "url": endpoint} for name, endpoint in DOWNLOAD_ENDPOINTS]
    if probe_sources_parallel is not None:
        results = probe_sources_parallel("huggingface-models", probe_input)
    else:
        results = probe_sources("huggingface-models", probe_input)
    successful = [item for item in results if item.ok]
    if not successful:
        return list(DOWNLOAD_ENDPOINTS)

    non_official = sorted(
        [item for item in successful if "official" not in item.name.lower()],
        key=lambda item: (item.total_ms, item.first_byte_ms),
    )
    official = sorted(
        [item for item in successful if "official" in item.name.lower()],
        key=lambda item: (item.total_ms, item.first_byte_ms),
    )

    # 镜像优先，但 mirror 显著慢于 official 时退到 official。
    # Prefer mirror, but fall back to official when mirror is dramatically slower.
    use_official_first = (
        non_official
        and official
        and non_official[0].total_ms
        > official[0].total_ms * HF_ENDPOINT_MIRROR_RATIO_THRESHOLD
    )

    ordered = (official + non_official) if use_official_first else (non_official + official)
    return [(item.name, item.url) for item in ordered]


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
    """Resolve the `hf` CLI executable path, caching the result."""
    global _HF_CLI_CACHE
    if _HF_CLI_CACHE is not None:
        return _HF_CLI_CACHE
    _HF_CLI_CACHE = shutil.which("hf") or ""
    if _HF_CLI_CACHE:
        logging.debug("hf CLI 已找到: %s", _HF_CLI_CACHE)
    else:
        logging.debug("hf CLI 未找到，将仅使用 httpx 直拉")
    return _HF_CLI_CACHE or None


def _estimate_file_size_via_cli(endpoint: str, repo_id: str, filename: str) -> int | None:
    """
    通过 `hf download --dry-run --format json` 获取远端文件大小。
    使用子进程方式，避免全局污染 HF_ENDPOINT 环境变量。

    Get remote file size via `hf download --dry-run --format json`.
    Uses subprocess to avoid polluting the global HF_ENDPOINT env var.
    """
    hf_path = _resolve_hf_cli_path()
    if not hf_path:
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
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
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

    返回 Returns:
        Optional[str]: 下载的文件路径，失败时返回 None
    """
    hf_path = _resolve_hf_cli_path()
    if not hf_path:
        logging.warning("hf CLI 不可用，跳过 CLI 下载")
        return None

    dest_path = Path(full_dest_dir) / filename
    full_dest_path = str(dest_path.resolve())

    env = os.environ.copy()
    env["HF_ENDPOINT"] = endpoint
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["HF_HUB_DISABLE_XET"] = "1"
    env["DO_NOT_TRACK"] = "1"

    cmd = [
        hf_path, "download",
        repo_id, filename,
        "--local-dir", full_dest_dir,
        "--format", "json",
    ]

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
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            env=env,
        )
        elapsed = time.perf_counter() - start

        if result.returncode != 0:
            stderr_tail = result.stderr.strip()[-500:] if result.stderr else "(no stderr)"
            logging.warning(
                "hf CLI %s 下载失败 (exit=%d): %s",
                source_name,
                result.returncode,
                stderr_tail,
            )
            return None

        stdout_text = result.stdout.strip()
        if not stdout_text:
            logging.warning("hf CLI %s 无输出，可能下载未完成", source_name)
            return None

        try:
            records: list[dict] = json.loads(stdout_text)
        except json.JSONDecodeError:
            logging.warning("hf CLI %s 输出非有效 JSON: %s...", source_name, stdout_text[:200])
            return None

        if not records or not dest_path.exists():
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
            "hf CLI 成功: %s 通过 %s 完成, %d 字节 (%.1f MB), 耗时 %.2f 秒",
            filename,
            source_name,
            file_size,
            file_size / 1048576,
            elapsed,
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
        logging.warning("hf CLI %s 下载超时 (3600s)", source_name)
        return None
    except Exception as exc:
        logging.warning(
            "hf CLI %s 下载异常: %s",
            source_name,
            _format_download_error(exc),
        )
        return None


def _make_dummy_resource(repo_id: str, filename: str) -> Dict[str, Any]:
    """Create a minimal resource dict for progress events when no resource is provided."""
    return {"resource_id": f"{repo_id}/{filename}", "filename": filename}


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
    try:
        with httpx.Client(follow_redirects=True, timeout=600.0) as client:
            with client.stream(
                "GET",
                url,
                headers={"User-Agent": "SuperPicky-Downloader/4.2.6"},
            ) as resp:
                resp.raise_for_status()

                # 从响应头提取 Content-Length，作为预期文件大小的独立估值
                # Extract Content-Length from response headers as independent size estimate
                if not expected_bytes:
                    content_length = resp.headers.get("content-length")
                    if content_length and content_length.isdigit():
                        expected_bytes = int(content_length)

                tmp_path.unlink(missing_ok=True)
                bytes_written = 0
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        bytes_written += len(chunk)
                        if progress_cb is not None and expected_bytes and expected_bytes > 0:
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
                if expected_bytes and bytes_written < expected_bytes:
                    raise RuntimeError(
                        f"incomplete download: {bytes_written} < {expected_bytes}"
                    )

        elapsed = time.perf_counter() - start

        dest_path.unlink(missing_ok=True)
        tmp_path.rename(dest_path)
        file_size = dest_path.stat().st_size
        logging.info(
            "httpx 直拉成功: %s 通过 %s 完成, %d 字节 (%.1f MB), 耗时 %.2f 秒",
            filename,
            source_name,
            file_size,
            file_size / 1048576,
            elapsed,
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
        tmp_path.unlink(missing_ok=True)
        logging.warning(
            "httpx 直拉 %s 失败: %s",
            source_name,
            _format_download_error(exc),
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
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SuperPicky-Downloader/4.2.6"},
            )
            start = time.perf_counter()
            bytes_written = 0
            tmp_path.unlink(missing_ok=True)
            with urllib.request.urlopen(request, timeout=120) as response:
                content_length = response.headers.get("Content-Length")
                if not expected_bytes and content_length and content_length.isdigit():
                    expected_bytes = int(content_length)
                with open(tmp_path, "wb") as handle:
                    while True:
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
            if expected_bytes and bytes_written < expected_bytes:
                raise RuntimeError(
                    f"incomplete download: {bytes_written} < {expected_bytes}"
                )
            dest_path.unlink(missing_ok=True)
            tmp_path.rename(dest_path)
            file_size = dest_path.stat().st_size
            if file_size <= 0:
                raise RuntimeError("downloaded file is empty")
            elapsed = time.perf_counter() - start
            logging.info(
                "urllib 直拉成功: %s 通过 %s 完成, %d 字节 (%.1f MB), 耗时 %.2f 秒",
                filename,
                source_name,
                file_size,
                file_size / 1048576,
                elapsed,
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
            tmp_path.unlink(missing_ok=True)
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
) -> Optional[str]:
    """
    逐源尝试下载：hf CLI → httpx 直拉 → 下一源。

    hf-mirror 对 `hf download` CLI 返回 HTTP 308 Permanent Redirect，
    huggingface_hub 的元数据检查仅跟随相对跳转（308 被拒绝），所以 CLI
    在 hf-mirror 上必然失败。此情况下自动回退到 httpx 直拉（follow_redirects=True
    可正确处理 308）。official 端点则两种方式均可正常工作。

    Try download per-endpoint: hf CLI → httpx direct → next endpoint.

    hf-mirror returns HTTP 308 Permanent Redirect to `hf download` CLI;
    huggingface_hub's metadata check only follows relative redirects
    (308 rejected), so CLI inevitably fails on hf-mirror. The fallback
    httpx direct download (follow_redirects=True) handles 308 correctly.
    The official endpoint works with both methods.

    返回 Returns:
        Optional[str]: 下载的文件路径，全部失败则返回 None
    """
    endpoints = _resolve_hf_endpoints()

    # ── 预估文件大小：用首个可用端点获取（CLI dry-run 仅 official 端点可用） ──
    # Pre-estimate file size using the first endpoint where CLI dry-run works
    # (CLI dry-run only works with the official endpoint; hf-mirror 308 rejected).
    expected_bytes: int | None = None
    for _name, _endpoint in endpoints:
        expected_bytes = _estimate_file_size_via_cli(_endpoint, repo_id, filename)
        if expected_bytes is not None:
            break

    for index, (source_name, endpoint) in enumerate(endpoints):
        logging.info("尝试从 %s (%s) 下载 %s", source_name, endpoint, filename)

        # ── 主方案：hf download CLI ───────────────────────────────────
        cli_result = _download_via_cli(
            repo_id=repo_id,
            filename=filename,
            full_dest_dir=full_dest_dir,
            endpoint=endpoint,
            source_name=source_name,
            expected_bytes=expected_bytes,
            progress_cb=progress_cb,
            resource=resource,
        )
        if cli_result:
            return cli_result

        # ── 备选方案：httpx 直拉（正确跟随 HTTP 308 跳转） ────────────
        # httpx direct (correctly follows HTTP 308 redirect).
        logging.info("hf CLI %s 失败，尝试 httpx 直拉...", source_name)
        httpx_result = _download_via_httpx(
            repo_id=repo_id,
            filename=filename,
            full_dest_dir=full_dest_dir,
            endpoint=endpoint,
            source_name=source_name,
            expected_bytes=expected_bytes,
            progress_cb=progress_cb,
            resource=resource,
        )
        if httpx_result:
            return httpx_result

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
) -> Path:
    """
    下载并验证资源文件。

    Download and verify resource file.

    参数 Parameters:
        resource (Dict[str, Any]): 资源元数据字典
        project_root (Optional[Path]): 项目根目录
        progress_cb (Optional[Callable[[InitializationProgressEvent], None]]): 进度回调函数

    返回 Returns:
        Path: 下载的文件路径

    异常 Raises:
        FileNotFoundError: 本地回退资源未找到
        RuntimeError: 下载失败或完整性验证失败
    """
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
        print("Error: Neither `hf` CLI nor `httpx` is available. Please run `pip install huggingface_hub httpx` first.")
        sys.exit(1)

    project_root = get_project_root()
    os.chdir(project_root)
    logging.info("Working directory set to: %s", project_root)

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
