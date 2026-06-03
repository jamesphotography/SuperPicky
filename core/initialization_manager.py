# -*- coding: utf-8 -*-
"""
First-run initialization manager for lightweight builds.

The old first-run onboarding path is intentionally preserved elsewhere for
full-package compatibility. This manager only takes over when runtime or
required resources are missing.

轻量级构建的首次运行初始化管理器。

旧的首次运行引导路径在其他地方保留，以实现完整包兼容性。
此管理器仅在运行时或所需资源缺失时接管。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from PySide6.QtCore import QObject, Signal

from advanced_config import get_advanced_config
from config import (
    get_app_config_dir,
    get_app_internal_dir,
    get_bundled_resource_dir,
)
from core.source_registry import (
    get_official_pypi_url,
    get_pypi_sources,
    get_torch_sources,
    sources_to_probe_dicts,
)
from core.initialization_progress import (
    InitializationProgressEvent,
    PROGRESS_KIND_DOWNLOAD,
    PROGRESS_KIND_RUNTIME,
    STAGE_DOWNLOADING,
    STAGE_PREPARING_RUNTIME,
    parse_pip_raw_progress_line,
)
from core.runtime_requirements import RuntimeRequirements, get_runtime_requirements
from core.source_probe import pick_best_source
from core.source_probe_parallel import (
    pick_best_with_geo_and_ratio,
    probe_sources_parallel,
)
from core.uv_runtime_manager import (
    build_install_command,
    create_venv,
    ensure_uv_managed_python_ready,
    ensure_uv_bootstrapped,
    is_uv_managed_python_path_error,
    is_uv_windows_mount_point_error,
    is_uv_python_interpreter_unavailable,
    run_uv_install,
    runtime_managed_python_dir,
)
from scripts.download_models import (
    download_resource,
    resolve_download_plan,
    resolve_resource_destination_dir,
    verify_resource,
)

logging.basicConfig(level=logging.INFO)


# ── 镜像核心选择策略 ───────────────────────────────────────────────────
# 沿用 2× 比率阈值，但源列表现在来自 source_registry.py 以满足全球覆盖。
# The 2× ratio threshold is preserved; source lists now come from
# source_registry.py for worldwide coverage.

PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD = 2.0


def _pick_preferred_with_ratio(successful):
    """
    在镜像与官方源之间按"镜像优先、但不显著慢于官方"的规则挑选最佳源。

    Pick the best source preferring mirrors when they aren't dramatically
    slower than the fastest official. ``successful`` 是已经过滤为 ok=True 的
    ``ProbeResult`` 列表。

    规则 / Rule:
    - 仅有官方或仅有镜像 → 返回该侧最快
    - 两侧都有 → 取镜像最快；若 mirror.total_ms 超过
      ``official.total_ms * PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD``
      则改用官方。这样大陆用户（镜像几百 ms vs 官方超时）继续走镜像，
      海外/边缘地区用户（镜像 800ms vs 官方 50ms）正确走官方。
    """
    if not successful:
        return None

    non_official = [
        item for item in successful if "official" not in item.name.lower()
    ]
    official = [
        item for item in successful if "official" in item.name.lower()
    ]

    best_mirror = pick_best_source(non_official) if non_official else None
    best_official = pick_best_source(official) if official else None

    if best_mirror and best_official:
        if best_mirror.total_ms <= best_official.total_ms * PREFERRED_SOURCE_MIRROR_RATIO_THRESHOLD:
            return best_mirror
        return best_official
    return best_mirror or best_official


FULL_FEATURE_SET = ("core_detection", "quality", "keypoint", "flight", "birdid")

STAGE_NOT_STARTED = "not_started"
STAGE_PROBING = "probing_sources"
STAGE_CHECKING_UPDATES = "checking_updates"
STAGE_PREPARING_RUNTIME = "preparing_runtime"
STAGE_DOWNLOADING = "downloading_resources"
STAGE_VERIFYING = "verifying"
STAGE_READY = "ready"
STAGE_FAILED = "failed"


class InitializationInterrupted(RuntimeError):
    """用户主动中断初始化 / User-requested initialization interruption."""


@dataclass
class RuntimeSelection:
    variant: str
    detected_cuda_capable: bool
    reason: str


@dataclass(frozen=True)
class RuntimeInstallLocation:
    key: str
    runtime_dir: Path
    free_bytes: Optional[int]
    writable: bool


@dataclass
class ResourceProgressState:
    """
    Aggregate state for one resource inside the download phase.

    下载阶段中单个资源的聚合状态。
    """

    ratio: float = 0.0
    bytes_done: int | None = None
    bytes_total: int | None = None
    is_terminal: bool = False
    last_logged_bucket: int = -1
    last_logged_message: str | None = None
    last_logged_source: str | None = None


@dataclass(frozen=True)
class RuntimeInstallAttempt:
    """
    One isolated runtime install attempt.

    单次隔离运行时安装尝试。
    """

    pypi_url: str
    torch_url: str
    attempt_index: int
    attempt_count: int


class InitializationManager(QObject):
    """
    Coordinate runtime repair, resource preparation, and structured progress events.

    负责协调运行时修复、资源准备以及结构化进度事件的初始化管理器。
    """

    stage_changed = Signal(str, str)
    progress_event = Signal(object)
    progress_changed = Signal(int, str, int, int)
    item_status_changed = Signal(str, str, str)
    finished = Signal(bool, object)

    def __init__(self, parent=None):
        """
        初始化初始化管理器。

        Initialize the initialization manager.

        参数 Parameters:
            parent: 父 QObject 对象
        """
        super().__init__(parent)
        self.config = get_advanced_config()
        self._thread: Optional[threading.Thread] = None
        self._last_options: Optional[dict] = None
        self._last_mode: str = "init"
        self._project_root = self._resolve_project_root()
        self._runtime_dir = self.resolve_runtime_dir(
            self.config.runtime_install_location_preference
        )
        self._source_map: Dict[str, Any] = {}
        self._cancel_requested = threading.Event()
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._resource_progress: dict[str, ResourceProgressState] = {}
        self._resource_progress_item_count = 0

        self._ensure_hf_endpoint_configured()
        logging.info("初始化管理器已创建，项目根目录: %s", self._project_root)

    def _resolve_project_root(self) -> Path:
        """
        解析项目根目录。

        Resolve project root directory.

        返回 Returns:
            Path: 项目根目录路径
        """
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            return get_app_internal_dir()
        return Path(__file__).resolve().parent.parent

    def _ensure_hf_endpoint_configured(self) -> None:
        """
        确保 Hugging Face 端点环境变量已正确设置。

        Ensure Hugging Face endpoint environment variables are properly configured.
        """
        hf_mirror_endpoint = "https://hf-mirror.com"

        if (
            "HF_ENDPOINT" not in os.environ
            or os.environ["HF_ENDPOINT"] != hf_mirror_endpoint
        ):
            os.environ["HF_ENDPOINT"] = hf_mirror_endpoint
            logging.info("已设置 HF_ENDPOINT = %s", hf_mirror_endpoint)

        env_vars = {
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_XET": "1",
            "DO_NOT_TRACK": "1",
        }

        for key, value in env_vars.items():
            if key not in os.environ or os.environ[key] != value:
                os.environ[key] = value
                logging.debug("已设置 %s = %s", key, value)

    def _resolve_runtime_requirements_path(
        self,
        runtime_variant: str,
        *,
        torch_source_url: str | None = None,
    ) -> Path:
        """
        Write an install-attempt-specific requirements file.

        写入与单次安装尝试绑定的 requirements 文件。
        """
        requirements = get_runtime_requirements(runtime_variant)  # pyright: ignore[reportArgumentType]
        requirements_content = requirements.to_requirements_string(
            include_indexes=False,
            package_urls=self._selected_torch_package_urls(
                runtime_variant,
                torch_source_url=torch_source_url,
            ),
        )

        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix=f"requirements_{runtime_variant}_",
            delete=False,
            encoding="utf-8",
        )
        try:
            temp_file.write(requirements_content)
            temp_file.close()
            return Path(temp_file.name)
        except Exception:
            temp_file.close()
            Path(temp_file.name).unlink(missing_ok=True)
            raise

    def _runtime_requirements(self, runtime_variant: str) -> RuntimeRequirements:
        """
        Return the unified runtime requirement definition for one variant.

        返回指定运行时变体的统一依赖定义。
        """
        return get_runtime_requirements(runtime_variant)  # pyright: ignore[reportArgumentType]

    def _selected_torch_package_urls(
        self,
        runtime_variant: str,
        *,
        torch_source_url: str | None = None,
    ) -> dict[str, str]:
        """
        Build direct wheel references for Torch packages on Windows runtime installs.

        为 Windows 运行时安装构建 Torch 系列包的直链引用。
        """
        if runtime_variant not in ("cpu", "cuda") or sys.platform != "win32":
            return {}
        primary_source = (torch_source_url or self._source_map.get("torch_primary", "")).strip()
        if not primary_source:
            return {}
        return self._runtime_requirements(runtime_variant).torch_wheel_urls(primary_source)

    @staticmethod
    def _torch_source_candidates(runtime_variant: str) -> list[dict[str, str]]:
        """
        Build Torch wheel source candidates from the global mirror registry.

        从全球镜像注册表构建 Torch wheel 源候选列表。
        """
        if runtime_variant not in ("cpu", "cuda") or sys.platform != "win32":
            return []
        requirements = get_runtime_requirements(runtime_variant)  # pyright: ignore[reportArgumentType]
        candidates: list[dict[str, str]] = []
        for source in get_torch_sources(runtime_variant):
            payload = source.to_probe_dict()
            torch_urls = requirements.torch_wheel_urls(source.url)
            torch_requirement = torch_urls.get("torch", "")
            if " @ " in torch_requirement:
                payload["probe_url"] = torch_requirement.split(" @ ", 1)[1]
            candidates.append(payload)
        return candidates

    @staticmethod
    def _normalize_features(selected_features: Optional[Iterable[str]]) -> list[str]:
        features = list(selected_features or FULL_FEATURE_SET)
        if "core_detection" not in features:
            features.insert(0, "core_detection")
        return features

    def _save_config(self, **updates) -> None:
        setters = {
            "initialization_completed": self.config.set_initialization_completed,
            "initialization_in_progress": self.config.set_initialization_in_progress,
            "selected_runtime_variant": self.config.set_selected_runtime_variant,
            "detected_cuda_capable": self.config.set_detected_cuda_capable,
            "runtime_install_location_preference": self.config.set_runtime_install_location_preference,
            "resolved_runtime_dir": self.config.set_resolved_runtime_dir,
            "enabled_feature_set": self.config.set_enabled_feature_set,
            "downloaded_resources": self.config.set_downloaded_resources,
            "resolved_source_map": self.config.set_resolved_source_map,
            "last_init_error": self.config.set_last_init_error,
            "last_init_exit_reason": self.config.set_last_init_exit_reason,
            "last_init_mode": self.config.set_last_init_mode,
            "is_first_run": self.config.set_is_first_run,
        }
        for key, value in updates.items():
            setter = setters.get(key)
            if setter is not None:
                setter(value)
        self.config.save()

    def _emit_item_status(self, resource_id: str, status: str, detail: str) -> None:
        self.item_status_changed.emit(resource_id, status, detail)

    def _emit_progress_event(self, event: InitializationProgressEvent) -> None:
        """
        Emit the new structured progress event and the deprecated legacy signal.

        发出新的结构化进度事件，并兼容发出旧版信号。
        """
        self.progress_event.emit(event)
        ratio = event.normalized_ratio()
        if ratio is None:
            return
        self.progress_changed.emit(
            int(round(ratio * 100.0)),
            event.message,
            event.item_index or 0,
            event.item_count or 0,
        )

    def _emit_phase_completion(
        self,
        progress_kind: str,
        message: str,
        *,
        bytes_done: int | None = None,
        bytes_total: int | None = None,
    ) -> None:
        """
        Emit an explicit terminal progress event for one visual phase.

        为单个视觉阶段发出显式终态进度事件。
        """
        stage = (
            STAGE_PREPARING_RUNTIME
            if progress_kind == PROGRESS_KIND_RUNTIME
            else STAGE_DOWNLOADING
        )
        self._emit_progress_event(
            InitializationProgressEvent(
                stage=stage,
                progress_kind=progress_kind,
                message=message,
                ratio=1.0,
                bytes_done=bytes_done,
                bytes_total=bytes_total,
                is_terminal=True,
            )
        )

    def _installation_root(self) -> Path:
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable).resolve()
            if sys.platform == "darwin" and executable.parent.name == "MacOS":
                return executable.parents[2]
            return executable.parent
        return self._project_root

    def _runtime_install_locations(self) -> dict[str, Path]:
        install_runtime_dir = self._installation_root() / "runtime_env"
        if self._requires_install_local_runtime():
            install_runtime_dir = get_app_internal_dir() / "runtime_env"
        return {
            "default": get_app_config_dir() / "runtime_env",
            "install": install_runtime_dir,
        }

    def _requires_install_local_runtime(self) -> bool:
        return getattr(sys, "frozen", False) and sys.platform == "win32"

    def _uses_bundled_runtime(self) -> bool:
        return getattr(sys, "frozen", False) and sys.platform == "darwin"

    @staticmethod
    def _existing_probe_path(path: Path) -> Path:
        current = path
        while not current.exists() and current != current.parent:
            current = current.parent
        return current

    def _free_bytes_for_path(self, path: Path) -> Optional[int]:
        probe_path = self._existing_probe_path(path)
        try:
            return shutil.disk_usage(probe_path).free
        except Exception:
            return None

    def _writable_probe_dir(self, path: Path) -> Path:
        probe_root = path if path.exists() else path.parent
        probe_dir = self._existing_probe_path(probe_root)
        return probe_dir if probe_dir.is_dir() else probe_dir.parent

    def _is_runtime_dir_writable(self, path: Path) -> bool:
        try:
            probe_dir = self._writable_probe_dir(path)
            with tempfile.TemporaryDirectory(dir=probe_dir, prefix="sp_runtime_probe_"):
                pass
            return True
        except Exception:
            return False

    def get_runtime_install_location_options(self) -> list[RuntimeInstallLocation]:
        if self._requires_install_local_runtime():
            install_dir = self._runtime_install_locations()["install"]
            return [
                RuntimeInstallLocation(
                    key="install",
                    runtime_dir=install_dir,
                    free_bytes=self._free_bytes_for_path(install_dir),
                    writable=self._is_runtime_dir_writable(install_dir),
                )
            ]

        options = []
        for key, runtime_dir in self._runtime_install_locations().items():
            options.append(
                RuntimeInstallLocation(
                    key=key,
                    runtime_dir=runtime_dir,
                    free_bytes=self._free_bytes_for_path(runtime_dir),
                    writable=self._is_runtime_dir_writable(runtime_dir),
                )
            )
        return options

    def choose_runtime_install_location(
        self, preferred_key: Optional[str] = None
    ) -> RuntimeInstallLocation:
        if self._requires_install_local_runtime():
            install_dir = self._runtime_install_locations()["install"]
            return RuntimeInstallLocation(
                "install",
                install_dir,
                self._free_bytes_for_path(install_dir),
                self._is_runtime_dir_writable(install_dir),
            )

        options = [
            item
            for item in self.get_runtime_install_location_options()
            if item.writable
        ]
        if not options:
            default_dir = self._runtime_install_locations()["default"]
            return RuntimeInstallLocation("default", default_dir, None, True)

        by_key = {item.key: item for item in options}
        if preferred_key in by_key:
            return by_key[preferred_key]

        comparable = [item for item in options if item.free_bytes is not None]
        if comparable:
            return max(
                comparable,
                key=lambda item: (item.free_bytes or -1, item.key == "default"),
            )
        return by_key.get("default", options[0])

    def resolve_runtime_dir(self, preferred_key: Optional[str] = None) -> Path:
        return self.choose_runtime_install_location(preferred_key).runtime_dir

    def runtime_display_dir(self, preferred_key: Optional[str] = None) -> Path:
        if self._uses_bundled_runtime():
            return get_bundled_resource_dir()
        if self._requires_install_local_runtime():
            return get_app_internal_dir() / "runtime_env"
        return self.resolve_runtime_dir(preferred_key)

    def start(self, options: dict, mode: str = "init") -> None:
        normalized_options = dict(options)
        normalized_options["features"] = self._normalize_features(
            normalized_options.get("features")
        )
        normalized_options["runtime_install_location"] = (
            self.choose_runtime_install_location(
                normalized_options.get("runtime_install_location")
                or self.config.runtime_install_location_preference
            ).key
        )
        self._last_options = normalized_options
        self._last_mode = mode
        if self._thread and self._thread.is_alive():
            return
        self._cancel_requested.clear()
        self._thread = threading.Thread(
            target=self._run, args=(dict(normalized_options), mode), daemon=True
        )
        self._thread.start()

    def start_initialization(self, options: dict) -> None:
        self.start(options, mode="init")

    def start_repair(self, options: dict) -> None:
        self.start(options, mode="repair")

    def retry_failed(self) -> None:
        if self._last_options is not None:
            self.start(self._last_options, mode=self._last_mode)

    def resume_pending(self) -> None:
        if self._last_options is not None:
            self.start(self._last_options, mode=self._last_mode)

    def cancel(self) -> None:
        self._cancel_requested.set()
        process = self._active_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception:
                    pass
            except Exception:
                pass

    def _set_active_process(self, process: Optional[subprocess.Popen[str]]) -> None:
        """
        注册当前初始化子进程，确保取消和退出时可以统一清理。

        参数:
        process (Optional[subprocess.Popen[str]]): 正在运行的子进程；None 表示清空。

        Register the current initialization subprocess so cancellation and app
        exit can clean it up consistently.

        Parameters:
        process (Optional[subprocess.Popen[str]]): Running subprocess, or None
        to clear the registration.
        """
        self._active_process = process

    def is_ready_for_main_ui(
        self, selected_features: Optional[Iterable[str]] = None
    ) -> bool:
        return self._has_runtime_available() and self._resources_available(
            selected_features
        )

    def needs_initialization(
        self, selected_features: Optional[Iterable[str]] = None
    ) -> bool:
        return not self.is_ready_for_main_ui(selected_features)

    def check_runtime_health(self) -> bool:
        """
        检查运行时健康状态。

        Check runtime health status.

        返回 Returns:
            bool: 运行时是否健康
        """
        runtime_available = self._has_runtime_available()
        import_ok = self._runtime_import_ok()

        logging.info("运行时健康检查: 可用=%s, 导入=%s", runtime_available, import_ok)

        return runtime_available and import_ok

    def check_resource_health(
        self, selected_features: Optional[Iterable[str]]
    ) -> Dict[str, bool]:
        """
        检查资源健康状态。

        Check resource health status.

        参数 Parameters:
            selected_features (Optional[Iterable[str]]): 选定的功能特性

        返回 Returns:
            Dict[str, bool]: 资源 ID 到健康状态的映射
        """
        plan = resolve_download_plan(self._normalize_features(selected_features))
        health_status = {
            item["resource_id"]: self._resource_item_available(item) for item in plan
        }

        healthy_count = sum(1 for status in health_status.values() if status)
        logging.info("资源健康检查: %d/%d 资源可用", healthy_count, len(health_status))

        return health_status

    def repair_runtime_if_needed(self, runtime_variant: str) -> bool:
        """
        如果需要，修复运行时环境。

        Repair runtime environment if needed.

        参数 Parameters:
            runtime_variant (str): 运行时变体（cpu/cuda/mac）

        返回 Returns:
            bool: 是否执行了修复
        """
        if self.check_runtime_health():
            self._emit_item_status("runtime", "done", "Runtime already healthy")
            self._emit_phase_completion(
                PROGRESS_KIND_RUNTIME,
                f"{runtime_variant} runtime already available",
            )
            logging.info("运行时环境健康，无需修复")
            return False

        if self._uses_bundled_runtime():
            raise RuntimeError(
                "Bundled macOS Torch runtime is unavailable; rebuild the app bundle."
            )

        logging.info("运行时环境需要修复，开始准备 %s 运行时...", runtime_variant)
        self._emit_stage(
            STAGE_PREPARING_RUNTIME, f"Preparing {runtime_variant} runtime..."
        )
        self._cleanup_partial_runtime()
        self._purge_pip_cache_if_needed()

        start_time = time.perf_counter()
        try:
            self._prepare_runtime(runtime_variant)
            self._emit_phase_completion(
                PROGRESS_KIND_RUNTIME,
                f"{runtime_variant} runtime ready",
            )
            elapsed = time.perf_counter() - start_time
            logging.info("运行时环境修复完成，耗时 %.2f 秒", elapsed)
            return True
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            logging.error("运行时环境修复失败，耗时 %.2f 秒: %s", elapsed, exc)
            raise

    def repair_resources_if_needed(
        self, selected_features: Optional[Iterable[str]]
    ) -> bool:
        """
        如果需要，修复资源文件。

        Repair resource files if needed.

        参数 Parameters:
            selected_features (Optional[Iterable[str]]): 选定的功能特性

        返回 Returns:
            bool: 是否执行了修复
        """
        plan = resolve_download_plan(self._normalize_features(selected_features))
        pending = [item for item in plan if not self._resource_item_available(item)]
        total_items = max(1, len(pending))

        if not pending:
            self._emit_item_status("resources", "done", "Resources already healthy")
            logging.info("所有资源已就绪，无需修复")
            return False

        self._resource_progress = {
            item["resource_id"]: ResourceProgressState() for item in pending
        }
        self._resource_progress_item_count = total_items
        logging.info("需要修复 %d 个资源文件", len(pending))
        self._emit_stage(STAGE_DOWNLOADING, "Downloading required resources...")

        start_time = time.perf_counter()
        success_count = 0

        for index, resource in enumerate(pending, start=1):
            label = resource["filename"]
            resource_id = resource["resource_id"]

            logging.info("开始下载资源 [%d/%d]: %s", index, total_items, label)

            self._emit_item_status(resource_id, "running", f"Preparing {label}")

            try:
                download_resource(
                    resource,
                    project_root=self._project_root,
                    progress_cb=self._resource_progress_cb(index, total_items, resource_id),
                )
                success_count += 1
                self._emit_item_status(resource_id, "done", f"{label} ready")
                logging.info("资源 [%s] 下载成功", resource_id)
            except Exception as exc:
                logging.error("资源 [%s] 下载失败: %s", resource_id, exc)
                self._emit_item_status(resource_id, "error", f"{label} failed: {exc}")
                raise

        elapsed = time.perf_counter() - start_time
        logging.info(
            "资源修复完成: %d/%d 成功，总耗时 %.2f 秒",
            success_count,
            total_items,
            elapsed,
        )
        self._emit_phase_completion(
            PROGRESS_KIND_DOWNLOAD,
            "All required resources ready",
        )

        return True

    def detect_runtime_selection(
        self, preferred_variant: str = "auto"
    ) -> RuntimeSelection:
        if sys.platform == "darwin":
            if preferred_variant in ("cpu", "mac"):
                return RuntimeSelection("mac", False, "macOS runtime")
            return RuntimeSelection("mac", False, "macOS runtime")

        detected_cuda = self._detect_cuda_capable()
        if preferred_variant == "cuda" and detected_cuda:
            return RuntimeSelection("cuda", True, "user requested CUDA")
        if preferred_variant == "cuda" and not detected_cuda:
            return RuntimeSelection(
                "cpu", False, "CUDA unavailable, falling back to CPU"
            )
        if preferred_variant == "cpu":
            return RuntimeSelection("cpu", detected_cuda, "user requested CPU")
        if detected_cuda:
            return RuntimeSelection("cuda", True, "detected NVIDIA/CUDA support")
        return RuntimeSelection("cpu", False, "default CPU runtime")

    def _run(self, options: dict, mode: str) -> None:
        try:
            self._raise_if_cancelled()
            selected_features = self._normalize_features(options.get("features"))
            self._last_mode = mode
            runtime_location = self.choose_runtime_install_location(
                options.get("runtime_install_location")
                or self.config.runtime_install_location_preference
            )
            self._runtime_dir = runtime_location.runtime_dir
            self._save_config(
                initialization_in_progress=(mode == "init"),
                last_init_error=None,
                last_init_exit_reason="none",
                last_init_mode=mode,
                runtime_install_location_preference=runtime_location.key,
                resolved_runtime_dir=str(runtime_location.runtime_dir),
            )

            runtime_choice = self.detect_runtime_selection(
                options.get("runtime_variant", "auto")
            )
            if mode == "init":
                self._save_config(
                    selected_runtime_variant=runtime_choice.variant,
                    detected_cuda_capable=runtime_choice.detected_cuda_capable,
                    runtime_install_location_preference=runtime_location.key,
                    resolved_runtime_dir=str(runtime_location.runtime_dir),
                    enabled_feature_set=selected_features,
                )

            self._raise_if_cancelled()
            self._emit_stage(STAGE_PROBING, "Probing download sources...")
            self._source_map = self._resolve_best_sources(runtime_choice.variant)
            self._emit_item_status(
                "source_probe", "done", f"PyPI -> {self._source_map['pypi_primary']}"
            )
            if self._source_map.get("torch_primary"):
                self._emit_item_status(
                    "source_probe", "done", f"Torch -> {self._source_map['torch_primary']}"
                )
            else:
                self._emit_item_status(
                    "source_probe", "done", "Torch -> bundled runtime"
                )
            self._save_config(resolved_source_map=self._source_map)

            if options.get("auto_update_enabled", True):
                self._emit_stage(STAGE_CHECKING_UPDATES, "Checking updates...")
                self._check_updates_if_enabled()
            else:
                try:
                    from tools.patch_manager import safe_clear_patch

                    cleared, clear_message = safe_clear_patch()
                    clear_status = "done" if cleared else "warning"
                    self._emit_item_status("updates", clear_status, clear_message)
                except Exception as exc:
                    self._emit_item_status(
                        "updates", "warning", f"Patch cleanup skipped: {exc}"
                    )
                self._emit_item_status(
                    "updates", "skipped", "Automatic updates disabled by user"
                )

            self._raise_if_cancelled()
            self.repair_runtime_if_needed(runtime_choice.variant)
            self._raise_if_cancelled()
            self.repair_resources_if_needed(selected_features)

            self._emit_stage(STAGE_VERIFYING, "Verifying resources...")
            if not self.is_ready_for_main_ui(selected_features):
                raise RuntimeError(
                    "Initialization completed with missing runtime or resources"
                )

            self._cleanup_uv_cache_after_initialization()

            success_updates: dict[str, object] = {
                "initialization_in_progress": False,
                "last_init_mode": "none",
            }
            if mode == "init":
                success_updates.update(
                    initialization_completed=True,
                    last_init_exit_reason="none",
                    is_first_run=False,
                    downloaded_resources={
                        item["resource_id"]: True
                        for item in resolve_download_plan(selected_features)
                    },
                )
            self._save_config(**success_updates)
            final_message = (
                "Initialization completed"
                if mode == "init"
                else "Environment repair completed"
            )
            self._emit_stage(STAGE_READY, final_message)
            self.finished.emit(
                True,
                {
                    "runtime_variant": runtime_choice.variant,
                    "source_map": self._source_map,
                    "mode": mode,
                },
            )
        except InitializationInterrupted:
            self._cleanup_partial_runtime()
            self._purge_pip_cache_if_needed()
            self._save_config(
                initialization_in_progress=False,
                last_init_error=None,
                last_init_exit_reason="interrupted",
                last_init_mode=mode,
            )
            self.finished.emit(False, {"interrupted": True, "mode": mode})
        except Exception as exc:
            self._save_config(
                initialization_in_progress=False,
                last_init_error=str(exc),
                last_init_exit_reason="failed",
                last_init_mode=mode,
            )
            self._emit_stage(STAGE_FAILED, str(exc))
            self.finished.emit(False, {"error": str(exc), "mode": mode})

    def _resource_progress_cb(
        self,
        item_index: int,
        total_items: int,
        fallback_resource_id: str,
    ):
        """
        Adapt per-resource download events into one aggregated download stream.

        将单个资源的下载事件适配为统一的聚合下载进度流。
        """

        def _callback(event: InitializationProgressEvent) -> None:
            resource_id = event.resource_id or fallback_resource_id
            enriched_event = InitializationProgressEvent(
                stage=event.stage,
                progress_kind=event.progress_kind,
                message=event.message,
                ratio=event.ratio,
                bytes_done=event.bytes_done,
                bytes_total=event.bytes_total,
                item_index=item_index - 1,
                item_count=total_items,
                resource_id=resource_id,
                source=event.source,
                is_terminal=event.is_terminal,
            )
            aggregate_event = self._update_resource_aggregate(enriched_event)
            if self._should_emit_resource_log(enriched_event):
                self._emit_item_status(resource_id, "progress", enriched_event.message)
            self._emit_progress_event(aggregate_event)

        return _callback

    def _update_resource_aggregate(
        self,
        event: InitializationProgressEvent,
    ) -> InitializationProgressEvent:
        """
        Fold one resource event into the cross-resource aggregate download ratio.

        将单个资源事件折叠到跨资源的聚合下载进度中。
        """
        resource_id = event.resource_id or f"resource-{len(self._resource_progress)}"
        ratio = event.normalized_ratio()
        bytes_total = event.bytes_total if event.bytes_total and event.bytes_total > 0 else None

        state = self._resource_progress.get(resource_id)
        if state is None:
            state = ResourceProgressState()
            self._resource_progress[resource_id] = state

        if ratio is not None:
            state.ratio = max(state.ratio, ratio)
        elif event.is_terminal:
            state.ratio = 1.0

        if event.bytes_done is not None:
            state.bytes_done = max(state.bytes_done or 0, event.bytes_done)
        if bytes_total is not None:
            state.bytes_total = max(state.bytes_total or 0, bytes_total)
        state.is_terminal = state.is_terminal or event.is_terminal or state.ratio >= 1.0

        total_items = max(1, self._resource_progress_item_count or len(self._resource_progress))
        completed_ratio_sum = sum(item.ratio for item in self._resource_progress.values())
        overall_ratio = completed_ratio_sum / total_items

        known_total = 0
        known_done = 0
        all_have_bytes = bool(self._resource_progress)
        for item in self._resource_progress.values():
            if item.bytes_total is None:
                all_have_bytes = False
                continue
            known_total += item.bytes_total
            known_done += min(item.bytes_done or 0, item.bytes_total)

        aggregate_terminal = (
            len(self._resource_progress) >= self._resource_progress_item_count
            and all(item.is_terminal for item in self._resource_progress.values())
        )
        return InitializationProgressEvent(
            stage=STAGE_DOWNLOADING,
            progress_kind=event.progress_kind,
            message=event.message,
            ratio=min(1.0, max(0.0, overall_ratio)),
            bytes_done=known_done if all_have_bytes else None,
            bytes_total=known_total if all_have_bytes else None,
            item_index=event.item_index,
            item_count=event.item_count,
            resource_id=event.resource_id,
            source=event.source,
            is_terminal=aggregate_terminal,
        )

    def _should_emit_resource_log(self, event: InitializationProgressEvent) -> bool:
        """
        Throttle noisy per-byte download events into human-readable milestone logs.

        将高频字节级下载事件节流为更适合阅读的里程碑日志。
        """
        resource_id = event.resource_id
        if not resource_id:
            return False

        state = self._resource_progress.get(resource_id)
        if state is None:
            return True

        ratio = event.normalized_ratio()
        message = event.message
        source_changed = bool(event.source and event.source != state.last_logged_source)
        terminal_message = event.is_terminal or any(
            token in message.lower()
            for token in ("failed", "validated", "downloaded", "already present", "copied from local fallback")
        )

        should_log = False
        if state.last_logged_message is None:
            should_log = True
        elif terminal_message or source_changed:
            should_log = True
        elif ratio is not None:
            bucket = min(10, int(ratio * 10.0))
            if bucket > state.last_logged_bucket:
                should_log = True
        elif message != state.last_logged_message:
            should_log = True

        if should_log:
            if ratio is not None:
                state.last_logged_bucket = max(
                    state.last_logged_bucket,
                    min(10, int(ratio * 10.0)),
                )
            state.last_logged_message = message
            state.last_logged_source = event.source or state.last_logged_source

        return should_log

    def _emit_stage(self, stage: str, message: str) -> None:
        self.stage_changed.emit(stage, message)

    def _check_updates_if_enabled(self) -> None:
        try:
            from tools.update_checker import UpdateChecker

            self._raise_if_cancelled()
            checker = UpdateChecker()
            checker.check_for_updates()
            self._emit_item_status("updates", "done", "Update probe finished")
        except Exception as exc:
            self._emit_item_status("updates", "warning", f"Update probe skipped: {exc}")

    def _resolve_best_sources(self, runtime_variant: str) -> Dict[str, Any]:
        """
        Probe PyPI and Torch mirrors in parallel, then select best with geo bias.

        并行探测 PyPI 和 Torch 镜像，结合地理偏好选出最佳源。
        """
        pypi_sources = get_pypi_sources()
        pypi_probe_sources = sources_to_probe_dicts(pypi_sources)
        torch_sources = self._torch_source_candidates(runtime_variant)

        # PyPI 与 Torch 源池彼此独立，使用线程并行调度两个 asyncio 探测器。
        # PyPI and Torch source pools are independent; run both asyncio probes
        # concurrently so wall-clock time is bounded by the slower pool only.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="sp-source-probe") as executor:
            pypi_future = executor.submit(
                probe_sources_parallel,
                "pypi",
                pypi_probe_sources,
            )
            torch_future = (
                executor.submit(
                    probe_sources_parallel,
                    f"torch-{runtime_variant}",
                    torch_sources,
                )
                if torch_sources
                else None
            )
            pypi_results = pypi_future.result()
            torch_results = torch_future.result() if torch_future is not None else []

        best_pypi = pick_best_with_geo_and_ratio(pypi_results)

        pypi_candidates = self._rank_source_urls(
            pypi_results,
            [source.url for source in pypi_sources],
            preferred_url=best_pypi.url if best_pypi else get_official_pypi_url(),
        )
        pypi_primary = pypi_candidates[0] if pypi_candidates else get_official_pypi_url()
        pypi_fallback = pypi_candidates[1] if len(pypi_candidates) > 1 else pypi_primary

        # ── Torch 源并行探测 ───────────────────────────────────────────
        torch_primary = ""
        torch_fallback = ""
        torch_candidates: list[str] = []
        if torch_sources:
            best_torch = pick_best_with_geo_and_ratio(torch_results)
            torch_candidates = self._rank_source_urls(
                torch_results,
                [source["url"] for source in torch_sources],
                preferred_url=best_torch.url if best_torch else torch_sources[0]["url"],
            )
            torch_primary = torch_candidates[0] if torch_candidates else torch_sources[0]["url"]
            torch_fallback = torch_candidates[1] if len(torch_candidates) > 1 else torch_primary

        selected = {
            "pypi_primary": pypi_primary,
            "pypi_fallback": pypi_fallback,
            "pypi_candidates": pypi_candidates,
            "torch_primary": torch_primary,
            "torch_fallback": torch_fallback,
            "torch_candidates": torch_candidates,
        }
        return selected

    @staticmethod
    def _pick_preferred_source(results):
        return _pick_preferred_with_ratio(
            [item for item in results if item.ok]
        )

    @staticmethod
    def _resolve_fallback_url(results, primary_url: str) -> str:
        successful = [item for item in results if item.ok and item.url != primary_url]
        if not successful:
            return primary_url
        fallback = _pick_preferred_with_ratio(successful)
        return fallback.url if fallback else primary_url

    @staticmethod
    def _rank_source_urls(
        results,
        fallback_urls: Iterable[str],
        *,
        preferred_url: str | None = None,
    ) -> list[str]:
        """
        Build a deterministic URL order from probe results plus fallback URLs.

        基于探测结果和兜底 URL 构建确定性的源顺序。
        """
        ranked: list[str] = []
        seen: set[str] = set()

        def _add(url: str | None) -> None:
            normalized = (url or "").strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            ranked.append(normalized)

        _add(preferred_url)
        for result in sorted(
            [item for item in results if item.ok],
            key=lambda item: (item.total_ms, item.first_byte_ms),
        ):
            _add(result.url)
        for url in fallback_urls:
            _add(url)
        return ranked

    def _prepare_runtime(self, runtime_variant: str) -> None:
        """
        Create or repair the runtime environment using uv for speed.

        使用 uv 快速创建或修复运行环境。
        """
        if self._uses_bundled_runtime():
            raise RuntimeError(
                "Bundled macOS Torch runtime is unavailable; runtime installation is disabled."
            )

        if self._use_packaged_runtime_bootstrap():
            self._prepare_runtime_with_packaged_bootstrap(runtime_variant)
            return

        uv_path = ensure_uv_bootstrapped(self._runtime_dir)

        if not self._runtime_dir.exists():
            create_venv(uv_path, self._runtime_dir)

        successful_attempt = self._install_runtime_with_uv(
            uv_path=uv_path,
            runtime_variant=runtime_variant,
            python_executable=self._runtime_python_executable(),
        )
        self._inject_runtime_site_packages()
        self._verify_runtime_import(runtime_variant)
        self._write_runtime_install_manifest(
            runtime_variant,
            successful_attempt,
            install_mode="venv",
        )

    def _use_packaged_runtime_bootstrap(self) -> bool:
        return getattr(sys, "frozen", False) and sys.platform == "win32"

    def _runtime_site_packages_candidates(self) -> list[Path]:
        candidates: list[Path] = [
            self._runtime_dir / "site-packages",
            self._runtime_dir / "Lib" / "site-packages",
        ]
        lib_dir = self._runtime_dir / "lib"
        if lib_dir.exists():
            candidates.extend(sorted(lib_dir.glob("python*/site-packages")))
        version_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
        candidates.append(self._runtime_dir / "lib" / version_tag / "site-packages")

        unique_candidates: list[Path] = []
        seen_paths: set[Path] = set()
        for candidate in candidates:
            if candidate in seen_paths:
                continue
            seen_paths.add(candidate)
            unique_candidates.append(candidate)
        return unique_candidates

    def _runtime_python_executable(self) -> Path:
        if os.name == "nt":
            return self._runtime_dir / "Scripts" / "python.exe"
        return self._runtime_dir / "bin" / "python3"

    def _prepare_runtime_with_packaged_bootstrap(self, runtime_variant: str) -> None:
        """Install runtime via uv into the packaged bootstrap directory."""
        uv_path = ensure_uv_bootstrapped(self._runtime_dir)
        runtime_site_packages = self._runtime_dir / "site-packages"
        staging_site_packages = self._runtime_dir / "site-packages.__installing__"
        shutil.rmtree(staging_site_packages, ignore_errors=True)
        staging_site_packages.mkdir(parents=True, exist_ok=True)
        try:
            successful_attempt = self._install_runtime_with_uv(
                uv_path=uv_path,
                runtime_variant=runtime_variant,
                target_dir=staging_site_packages,
            )
            shutil.rmtree(runtime_site_packages, ignore_errors=True)
            staging_site_packages.rename(runtime_site_packages)
            self._inject_runtime_site_packages()
            self._verify_runtime_import(runtime_variant)
            self._write_runtime_install_manifest(
                runtime_variant,
                successful_attempt,
                install_mode="target",
            )
        except Exception:
            shutil.rmtree(staging_site_packages, ignore_errors=True)
            shutil.rmtree(runtime_site_packages, ignore_errors=True)
            raise

    def _uv_progress_cb(self):
        """Create a progress callback that adapts uv/pip raw output to progress events."""
        last_logged_message = ""
        last_logged_at = 0.0

        def _callback(event: InitializationProgressEvent) -> None:
            nonlocal last_logged_at, last_logged_message
            self._emit_progress_event(event)
            message = event.message.strip()
            now = time.monotonic()
            lowered = message.lower()
            should_log = event.is_terminal or any(
                token in lowered
                for token in (
                    "started",
                    "resolved",
                    "download",
                    "prepared",
                    "installed",
                    "still working",
                    "completed",
                )
            )
            if should_log and message != last_logged_message and now - last_logged_at >= 1.5:
                last_logged_message = message
                last_logged_at = now
                self._emit_item_status("runtime", "progress", message)

        return _callback

    def _install_runtime_with_uv(
        self,
        *,
        uv_path: str,
        runtime_variant: str,
        target_dir: Path | None = None,
        python_executable: Path | None = None,
    ) -> RuntimeInstallAttempt:
        """
        Install runtime dependencies by trying isolated PyPI/Torch source pairs.

        通过隔离的 PyPI/Torch 源组合尝试安装运行时依赖。
        """
        allow_managed_python_fallback = (
            target_dir is not None and self._use_packaged_runtime_bootstrap()
        )
        uv_python_dir = (
            runtime_managed_python_dir(self._runtime_dir)
            if allow_managed_python_fallback
            else None
        )
        managed_python_prepared = False

        attempts = self._runtime_install_attempts(runtime_variant)
        current_attempt = attempts[0]
        tried_pairs: set[tuple[str, str]] = set()
        last_error: Exception | None = None
        for display_index in range(1, len(attempts) + 1):
            self._raise_if_cancelled()
            attempt = RuntimeInstallAttempt(
                pypi_url=current_attempt.pypi_url,
                torch_url=current_attempt.torch_url,
                attempt_index=display_index,
                attempt_count=len(attempts),
            )
            tried_pairs.add((attempt.pypi_url, attempt.torch_url))
            requirements_file = self._resolve_runtime_requirements_path(
                runtime_variant,
                torch_source_url=attempt.torch_url or None,
            )
            label = (
                f"Install {runtime_variant} runtime "
                f"({attempt.attempt_index}/{attempt.attempt_count}, "
                f"PyPI={attempt.pypi_url}, Torch={attempt.torch_url or 'pypi'})"
            )
            try:
                self._emit_item_status("runtime", "progress", label)
                command = build_install_command(
                    uv_path=uv_path,
                    requirements_file=requirements_file,
                    index_url=attempt.pypi_url,
                    target_dir=target_dir,
                    python_executable=python_executable,
                    cache_dir=self._uv_cache_dir(),
                    use_managed_python=managed_python_prepared,
                )
                run_uv_install(
                    uv_path,
                    command,
                    label,
                    progress_cb=self._uv_progress_cb(),
                    uv_managed_python_dir=(
                        uv_python_dir if managed_python_prepared else None
                    ),
                    raise_if_cancelled=self._raise_if_cancelled,
                    active_process_cb=self._set_active_process,
                    cwd=self._runtime_dir,
                )
                return attempt
            except Exception as exc:
                if (
                    allow_managed_python_fallback
                    and not managed_python_prepared
                    and uv_python_dir is not None
                    and (
                        is_uv_python_interpreter_unavailable(str(exc))
                        or is_uv_windows_mount_point_error(str(exc))
                    )
                ):
                    self._emit_item_status(
                        "runtime",
                        "warning",
                        "System Python search path is unavailable for uv target install; "
                        "trying uv managed Python fallback",
                    )
                    try:
                        self._emit_item_status(
                            "runtime",
                            "progress",
                            f"Prepare uv managed Python at {uv_python_dir}",
                        )
                        ensure_uv_managed_python_ready(
                            uv_path,
                            uv_python_dir,
                            progress_cb=self._uv_progress_cb(),
                            raise_if_cancelled=self._raise_if_cancelled,
                            active_process_cb=self._set_active_process,
                            cwd=self._runtime_dir,
                        )
                    except Exception as fallback_exc:
                        if is_uv_managed_python_path_error(str(fallback_exc)):
                            raise RuntimeError(
                                "uv managed Python fallback failed before runtime "
                                "source attempts; please launch SuperPicky from "
                                "Explorer or Windows Terminal and avoid terminals "
                                "that trigger ERROR_UNTRUSTED_MOUNT_POINT. "
                                f"Path: {uv_python_dir}"
                            ) from fallback_exc
                        raise RuntimeError(
                            "uv managed Python fallback failed before runtime "
                            f"source attempts: {fallback_exc}"
                        ) from fallback_exc
                    managed_python_prepared = True
                    try:
                        managed_command = build_install_command(
                            uv_path=uv_path,
                            requirements_file=requirements_file,
                            index_url=attempt.pypi_url,
                            target_dir=target_dir,
                            python_executable=python_executable,
                            cache_dir=self._uv_cache_dir(),
                            use_managed_python=True,
                        )
                        run_uv_install(
                            uv_path,
                            managed_command,
                            f"{label} with managed Python fallback",
                            progress_cb=self._uv_progress_cb(),
                            uv_managed_python_dir=uv_python_dir,
                            raise_if_cancelled=self._raise_if_cancelled,
                            active_process_cb=self._set_active_process,
                            cwd=self._runtime_dir,
                        )
                        return attempt
                    except Exception as managed_exc:
                        exc = managed_exc
                if uv_python_dir is not None and is_uv_managed_python_path_error(str(exc)):
                    raise RuntimeError(
                        "uv managed Python local path failed during runtime "
                        "install; please launch SuperPicky from Explorer or "
                        "Windows Terminal and avoid terminals that trigger "
                        f"ERROR_UNTRUSTED_MOUNT_POINT. Path: {uv_python_dir}"
                    ) from exc
                if (
                    not allow_managed_python_fallback
                    and not managed_python_prepared
                    and is_uv_windows_mount_point_error(str(exc))
                ):
                    raise RuntimeError(
                        "uv hit a Windows mount-point traversal failure during "
                        "runtime install. This is often caused by launching from "
                        "the Inno Setup completion screen or another installer "
                        "context. Please close SuperPicky and start it again from "
                        "the Start menu or Explorer, then retry initialization. "
                        f"Original error: {exc}"
                    ) from exc
                last_error = exc
                failed_pool = self._classify_runtime_install_failure(
                    str(exc),
                    attempt,
                )
                logging.warning(
                    "运行时安装源组合失败 (%d/%d): failed_pool=%s, PyPI=%s, Torch=%s, error=%s",
                    attempt.attempt_index,
                    attempt.attempt_count,
                    failed_pool,
                    attempt.pypi_url,
                    attempt.torch_url or "pypi",
                    exc,
                )
                current_attempt = self._next_runtime_install_attempt(
                    attempts,
                    tried_pairs,
                    attempt,
                    failed_pool,
                )
                if current_attempt is None:
                    break
            finally:
                requirements_file.unlink(missing_ok=True)
        raise RuntimeError(
            f"All runtime install source attempts failed: {last_error}"
        )

    @staticmethod
    def _classify_runtime_install_failure(
        error_text: str,
        attempt: RuntimeInstallAttempt,
    ) -> str:
        """
        Best-effort classification for deciding which source pool to switch next.

        为下一次重试应切换哪个源池做尽力分类。
        """
        lowered = (error_text or "").lower()
        torch_url = (attempt.torch_url or "").lower()
        pypi_url = (attempt.pypi_url or "").lower()

        if is_uv_managed_python_path_error(lowered) or is_uv_windows_mount_point_error(lowered):
            return "local_runtime"
        if torch_url and torch_url in lowered:
            return "torch"
        if pypi_url and pypi_url in lowered:
            return "pypi"

        torch_tokens = (
            "download.pytorch.org",
            "pytorch-wheels",
            "pytorch/whl",
            "torch-",
            "torchvision-",
            "torchaudio-",
            "+cu",
            "+cpu",
        )
        pypi_tokens = (
            "pypi",
            "simple",
            "timm",
            "no matching distribution found for timm",
            "failed to fetch",
        )
        if attempt.torch_url and any(token in lowered for token in torch_tokens):
            return "torch"
        if any(token in lowered for token in pypi_tokens):
            return "pypi"
        return "unknown"

    @staticmethod
    def _next_runtime_install_attempt(
        attempts: list[RuntimeInstallAttempt],
        tried_pairs: set[tuple[str, str]],
        current: RuntimeInstallAttempt,
        failed_pool: str,
    ) -> RuntimeInstallAttempt | None:
        """
        Pick the next source pair, switching only the failed pool when possible.

        选择下一组源；如果能识别失败源池，则优先只切换该源池。
        """

        def _untried(candidate: RuntimeInstallAttempt) -> bool:
            return (candidate.pypi_url, candidate.torch_url) not in tried_pairs

        if failed_pool == "local_runtime":
            return None
        if failed_pool == "pypi":
            for candidate in attempts:
                if candidate.torch_url == current.torch_url and _untried(candidate):
                    return candidate
        elif failed_pool == "torch":
            for candidate in attempts:
                if candidate.pypi_url == current.pypi_url and _untried(candidate):
                    return candidate

        for candidate in attempts:
            if _untried(candidate):
                return candidate
        return None

    def _runtime_install_attempts(self, runtime_variant: str) -> list[RuntimeInstallAttempt]:
        """
        Build deterministic source-pair attempts.

        构建确定性的源组合尝试顺序。
        """
        pypi_urls = self._source_candidates("pypi_candidates", "pypi_primary")
        if not pypi_urls:
            pypi_urls = [get_official_pypi_url()]

        torch_urls = self._source_candidates("torch_candidates", "torch_primary")
        if runtime_variant not in ("cpu", "cuda") or sys.platform != "win32":
            torch_urls = [""]
        elif not torch_urls:
            torch_urls = [
                source["url"] for source in self._torch_source_candidates(runtime_variant)
            ]

        pairs: list[tuple[str, str]] = []
        for torch_url in torch_urls:
            for pypi_url in pypi_urls:
                pairs.append((pypi_url, torch_url))
        total = max(1, len(pairs))
        return [
            RuntimeInstallAttempt(
                pypi_url=pypi_url,
                torch_url=torch_url,
                attempt_index=index,
                attempt_count=total,
            )
            for index, (pypi_url, torch_url) in enumerate(pairs, start=1)
        ]

    def _source_candidates(self, list_key: str, primary_key: str) -> list[str]:
        values = self._source_map.get(list_key)
        if isinstance(values, list):
            candidates = [str(value).strip() for value in values if str(value).strip()]
        else:
            candidates = []
        primary = str(self._source_map.get(primary_key) or "").strip()
        if primary and primary not in candidates:
            candidates.insert(0, primary)
        seen: set[str] = set()
        unique: list[str] = []
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            unique.append(candidate)
        return unique

    def _uv_cache_dir(self) -> Path:
        cache_dir = get_app_internal_dir() / "uv-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _uv_cache_cleanup_roots(self) -> list[Path]:
        """
        Return app-owned uv cache directories that may be removed after success.

        返回初始化成功后可以删除的应用自有 uv 缓存目录。
        """
        roots = [
            get_app_internal_dir() / "uv-cache",
            self._runtime_dir / "uv-cache",
        ]
        unique_roots: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                resolved = root
            if resolved in seen:
                continue
            seen.add(resolved)
            unique_roots.append(root)
        return unique_roots

    def _cleanup_uv_cache_after_initialization(self) -> None:
        """
        Remove app-owned uv caches after initialization has fully succeeded.

        在初始化完全成功后删除应用自有 uv 缓存。

        The install commands use dedicated cache directories under the app
        internal directory or runtime directory. Cleaning only these paths keeps
        user/global uv caches intact while preventing large wheel downloads from
        occupying storage after the runtime has been verified.

        安装命令只使用应用内部目录或运行时目录下的专用缓存。这里只清理这些路径，
        不触碰用户或系统全局 uv 缓存，从而避免大型 wheel 下载文件在运行时校验
        完成后继续占用存储空间。
        """
        total_bytes = 0
        removed_count = 0
        for cache_root in self._uv_cache_cleanup_roots():
            if not cache_root.exists():
                continue
            try:
                total_bytes += self._directory_size_bytes(cache_root)
                if cache_root.is_dir():
                    shutil.rmtree(cache_root, ignore_errors=True)
                else:
                    cache_root.unlink(missing_ok=True)
                removed_count += 1
            except Exception as exc:
                self._emit_item_status(
                    "runtime",
                    "warning",
                    f"uv cache cleanup skipped for {cache_root}: {exc}",
                )
        if removed_count:
            self._emit_item_status(
                "runtime",
                "done",
                f"uv cache cleaned ({self._format_bytes(total_bytes)})",
            )

    @staticmethod
    def _directory_size_bytes(path: Path) -> int:
        """
        Calculate total bytes below a file or directory, ignoring vanished files.

        计算文件或目录下的总字节数，并忽略遍历期间消失的文件。
        """
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _format_bytes(value: int) -> str:
        """
        Format byte counts for initialization logs.

        为初始化日志格式化字节数。
        """
        size = float(max(0, value))
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0 or unit == "GB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0

    def _write_runtime_install_manifest(
        self,
        runtime_variant: str,
        attempt: RuntimeInstallAttempt,
        *,
        install_mode: str,
    ) -> None:
        """
        Persist a successful runtime install manifest.

        持久化运行时安装成功清单。
        """
        manifest = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime_variant": runtime_variant,
            "install_mode": install_mode,
            "runtime_dir": str(self._runtime_dir),
            "pypi_url": attempt.pypi_url,
            "torch_url": attempt.torch_url,
            "attempt_index": attempt.attempt_index,
            "attempt_count": attempt.attempt_count,
            "python_version": sys.version,
            "source_map": self._source_map,
        }
        manifest_path = self._runtime_dir / "runtime_install_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _should_retry_pypi_mirror(self, error_text: str) -> bool:
        """
        判断是否应从镜像回退至官方 pypi.org 重试。

        Decide whether to fall back from a mirror to pypi.org for retry.

        回退条件 / Retry conditions:
        - 镜像返回 404（包或 wheel 在该镜像上不存在）
        - 连接超时 / connection timeout
        - TLS/SSL 错误 / TLS/SSL errors
        - 但如果 primary 已是官方源则不再回退
        """
        primary = (self._source_map.get("pypi_primary") or "").strip()
        if not primary:
            return False
        if primary == get_official_pypi_url():
            return False
        lowered = error_text.lower()
        retryable = (
            "404" in lowered
            or "403" in lowered
            or "timeout" in lowered
            or "timed out" in lowered
            or "ssl" in lowered
            or "tls" in lowered
            or "connection reset" in lowered
            or "connection refused" in lowered
            or "could not find a version" in lowered
        )
        return bool(retryable)

    def _run_subprocess(
        self,
        command: list[str],
        label: str,
        *,
        progress_stage: str | None = None,
        progress_kind: str | None = None,
    ) -> None:
        """
        Run a subprocess while forwarding structured progress updates when possible.

        运行子进程，并在可能时转发结构化进度更新。
        """
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        process = subprocess.Popen(command, **popen_kwargs)
        self._active_process = process
        latest_progress_bytes: tuple[int, int | None] | None = None
        output_lines: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self._raise_if_cancelled()
                text = line.strip()
                if text:
                    output_lines.append(text)
                    parsed = parse_pip_raw_progress_line(text)
                    if (
                        parsed is not None
                        and progress_stage is not None
                        and progress_kind is not None
                    ):
                        current, total = parsed
                        latest_progress_bytes = (current, total if total > 0 else None)
                        self._emit_progress_event(
                            InitializationProgressEvent(
                                stage=progress_stage,
                                progress_kind=progress_kind,
                                message=f"{label}: raw progress {current}/{total}",
                                ratio=(current / total) if total > 0 else None,
                                bytes_done=current,
                                bytes_total=total if total > 0 else None,
                            )
                        )
                        continue
                    self.item_status_changed.emit("runtime", "progress", f"{label}: {text}")
            return_code = process.wait()
            if self._cancel_requested.is_set():
                raise InitializationInterrupted("Initialization interrupted by user")
            if return_code != 0:
                tail = "\n".join(output_lines[-12:])
                if tail:
                    raise RuntimeError(
                        f"{label} failed with exit code {return_code}\n{tail}"
                    )
                raise RuntimeError(f"{label} failed with exit code {return_code}")
            if progress_stage is not None and progress_kind is not None:
                done_bytes = latest_progress_bytes[0] if latest_progress_bytes else None
                total_bytes = latest_progress_bytes[1] if latest_progress_bytes else None
                self._emit_progress_event(
                    InitializationProgressEvent(
                        stage=progress_stage,
                        progress_kind=progress_kind,
                        message=f"{label} completed",
                        ratio=1.0,
                        bytes_done=total_bytes or done_bytes,
                        bytes_total=total_bytes,
                        is_terminal=True,
                    )
                )
        finally:
            self._active_process = None

    def _resolve_python_command(self) -> list[str]:
        if os.environ.get("VIRTUAL_ENV") and shutil.which("python"):
            return [shutil.which("python") or "python"]

        candidates = [
            (
                [sys.executable]
                if sys.executable and not getattr(sys, "frozen", False)
                else None
            ),
            [shutil.which("python3")] if shutil.which("python3") else None,
            [shutil.which("python")] if shutil.which("python") else None,
            ["py", "-3"] if shutil.which("py") else None,
        ]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                subprocess.run(
                    [*candidate, "-c", "import sys; print(sys.executable)"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=True,
                )
                return candidate
            except Exception:
                continue
        raise RuntimeError("Unable to find a Python interpreter for runtime bootstrap")

    def _inject_runtime_site_packages(self) -> None:
        if self._uses_bundled_runtime():
            return
        importlib.invalidate_caches()
        for candidate in self._runtime_site_packages_candidates():
            if candidate.exists():
                path = str(candidate)
                if path not in sys.path:
                    sys.path.insert(0, path)

    def _verify_runtime_import(self, runtime_variant: str) -> None:
        try:
            if self._uses_bundled_runtime():
                torch_module = importlib.import_module("torch")
                torch_version = getattr(torch_module, "__version__", "unknown")
                self._emit_item_status(
                    "runtime",
                    "done",
                    f"Bundled Torch import OK: {torch_version} ({runtime_variant})",
                )
                return
            importlib.invalidate_caches()
            self._inject_runtime_site_packages()
            self._verify_packaged_runtime_stdlib_imports()
            runtime_python = self._runtime_python_executable()
            if runtime_python.exists():
                result = subprocess.run(
                    [
                        str(runtime_python),
                        "-c",
                        (
                            "import torch, sys; "
                            "print(torch.__version__); "
                            "print(sys.executable); "
                            "print(getattr(torch.version, 'cuda', '') or '')"
                        ),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=True,
                )
                runtime_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                torch_version = runtime_lines[0] if runtime_lines else "unknown"
                runtime_executable = runtime_lines[1] if len(runtime_lines) > 1 else str(runtime_python)
                cuda_version = runtime_lines[2] if len(runtime_lines) > 2 else ""
                self._validate_runtime_torch_variant(
                    runtime_variant,
                    torch_version,
                    cuda_version,
                )
                self._emit_item_status(
                    "runtime",
                    "done",
                    f"Torch import OK: {torch_version} ({runtime_variant}) via {runtime_executable}",
                )
                return
            torch_module = importlib.import_module("torch")
            torch_version = getattr(torch_module, "__version__", "unknown")
            cuda_version = getattr(getattr(torch_module, "version", None), "cuda", "") or ""
            self._validate_runtime_torch_variant(
                runtime_variant,
                str(torch_version),
                str(cuda_version),
            )
            self._emit_item_status(
                "runtime",
                "done",
                f"Torch import OK: {torch_version} ({runtime_variant})",
            )
        except Exception as exc:
            raise RuntimeError(
                f"Runtime installed but import verification failed: {exc}"
            ) from exc

    @staticmethod
    def _validate_runtime_torch_variant(
        runtime_variant: str,
        torch_version: str,
        cuda_version: str,
    ) -> None:
        """
        Validate that the imported Torch build matches the selected runtime.

        校验导入的 Torch 构建是否匹配所选运行时。
        """
        normalized_version = (torch_version or "").lower()
        normalized_cuda = (cuda_version or "").strip()
        if runtime_variant == "cuda":
            if "+cu" not in normalized_version and not normalized_cuda:
                raise RuntimeError(
                    f"Expected CUDA Torch runtime, got torch {torch_version}"
                )
        elif runtime_variant == "cpu":
            if "+cu" in normalized_version:
                raise RuntimeError(
                    f"Expected CPU Torch runtime, got torch {torch_version}"
                )

    @staticmethod
    def _verify_packaged_runtime_stdlib_imports() -> None:
        """
        Verify stdlib modules required by packages loaded from the target runtime.

        校验 target runtime 中第三方包会依赖的标准库模块。

        The Windows Lite build installs heavy packages into an app-local
        ``site-packages`` directory and imports them from the frozen process.
        PyInstaller must therefore bundle stdlib modules that those runtime
        packages import indirectly; checking them here fails early during
        initialization instead of during model preload or API startup.

        Windows Lite 包会把重型依赖安装到应用本地 ``site-packages``，并在冻结进程
        中直接导入这些包。因此 PyInstaller 必须同时打入这些运行时包间接依赖的标准
        库模块；这里提前检查，避免延迟到模型预加载或 API 启动阶段才失败。
        """
        if not getattr(sys, "frozen", False):
            return
        for module_name in (
            "html",
            "html.entities",
            "html.parser",
            "_markupbase",
            "logging.config",
            "logging.handlers",
        ):
            importlib.import_module(module_name)

    def _runtime_import_ok(self) -> bool:
        try:
            self._verify_packaged_runtime_stdlib_imports()
            if self._uses_bundled_runtime():
                importlib.invalidate_caches()
                importlib.import_module("torch")
                return True
            self._inject_runtime_site_packages()
            importlib.invalidate_caches()
            importlib.import_module("torch")
            return True
        except Exception:
            return False

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise InitializationInterrupted("Initialization interrupted by user")

    def _cleanup_partial_runtime(self) -> None:
        if self._uses_bundled_runtime():
            return
        runtime_dir = self._runtime_dir
        if not runtime_dir.exists():
            return

        removable_paths = [
            runtime_dir / "site-packages",
            runtime_dir / "site-packages.__installing__",
            runtime_dir / "Lib" / "site-packages",
            runtime_managed_python_dir(runtime_dir),
            runtime_dir / "runtime_install_manifest.json",
        ]
        for candidate in removable_paths:
            try:
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                else:
                    candidate.unlink(missing_ok=True)
            except Exception as exc:
                logging.warning("清理运行时残留失败: %s (%s)", candidate, exc)

    def _pip_cache_roots(self) -> list[Path]:
        roots: list[Path] = []
        if sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                roots.append(Path(local_app_data) / "pip" / "Cache")
        else:
            roots.append(Path.home() / ".cache" / "pip")
        return roots

    def _purge_pip_cache_if_needed(self) -> None:
        for cache_root in self._pip_cache_roots():
            if not cache_root.exists():
                continue
            for relative_name in ("http-v2", "http", "wheels", "selfcheck"):
                candidate = cache_root / relative_name
                try:
                    if candidate.is_dir():
                        shutil.rmtree(candidate, ignore_errors=True)
                    else:
                        candidate.unlink(missing_ok=True)
                except Exception as exc:
                    logging.warning("清理 pip 缓存失败: %s (%s)", candidate, exc)

    def _has_runtime_available(self) -> bool:
        if self._uses_bundled_runtime():
            importlib.invalidate_caches()
            return importlib.util.find_spec("torch") is not None
        if importlib.util.find_spec("torch") is not None:
            return True
        self._inject_runtime_site_packages()
        return importlib.util.find_spec("torch") is not None

    def _resources_available(self, selected_features: Optional[Iterable[str]]) -> bool:
        features = self._normalize_features(selected_features)
        plan = resolve_download_plan(features)
        return all(
            self._resource_item_available(item)
            for item in plan
            if item.get("required") or selected_features
        )

    def _resource_item_available(self, item: dict) -> bool:
        path = (
            resolve_resource_destination_dir(self._project_root, item)
            / item["filename"]
        )
        try:
            return verify_resource(item, path)
        except Exception:
            return False

    def _detect_cuda_capable(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False
