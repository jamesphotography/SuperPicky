"""
SuperPicky 配置管理模块 / SuperPicky configuration management module.

本文件负责静态常量、路径约定、轻量运行时覆盖入口与共享懒加载注册器。
This file owns static constants, path conventions, lightweight runtime overrides, and the shared lazy registry.

维护分层 / Maintenance layering:
- `config.py`：公共读取入口与基础配置 / shared read entry points and foundational configuration.
- `advanced_config.py`：高级持久化配置的默认值、读写、迁移与 UI 对接 / defaults, persistence, migration, and UI integration for advanced persistent settings.
- 用户配置文件默认位于 `get_app_config_dir() / "advanced_config.json"` / The user config file defaults to `get_app_config_dir() / "advanced_config.json"`.
"""

import json
import importlib
import logging
import os
import platform
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Torch is intentionally imported lazily.
# macOS frozen builds bundle Torch inside the app, and importing it at
# module-load time makes it easier to bind to a wrong partial path before the
# frozen runtime is fully settled.
torch = None


class _FallbackDevice:
    def __init__(self, device_type: str):
        self.type = device_type

    def __str__(self) -> str:
        return self.type


def _get_torch_module():
    """Lazily (re)load torch so lightweight init can install it at runtime."""
    global torch
    if torch is not None:
        return torch
    try:
        torch = importlib.import_module("torch")
    except Exception:
        torch = None
    return torch


def get_app_install_dir() -> Path:
    """
    返回应用安装根目录 / Return the application install root.

    Windows Lite 打包场景下，运行时、模型和数据库必须固定落在该目录内。
    In Windows Lite builds, runtime files, models, and databases must stay under this directory.
    """
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin" and executable.parent.name == "MacOS":
            return executable.parents[2]
        return executable.parent
    return Path(__file__).resolve().parent


def get_runtime_meipass() -> Optional[str]:
    """
    返回 PyInstaller 注入的 `_MEIPASS` 路径字符串。
    Return the `_MEIPASS` path string injected by PyInstaller.

    这是运行时动态属性，静态类型检查器并不知道它一定存在，
    所以所有调用方都应通过此函数统一访问，而不是直接读取 `sys._MEIPASS`。
    This is a runtime-only dynamic attribute that static type checkers do not
    know about, so callers should go through this helper instead of touching
    `sys._MEIPASS` directly.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        return meipass
    return None


def get_runtime_app_root() -> Optional[str]:
    """
    返回补丁覆盖层记录的真实应用根目录字符串。
    Return the real application root string recorded for the patch overlay.

    在线补丁覆盖层会优先导入用户目录中的模块，导致 `__file__` 可能指向
    `code_updates/`。这里统一读取主入口注入的真实根目录，避免各模块自行
    读取 `sys._SUPERPICKY_APP_ROOT` 触发静态告警。
    The patch overlay may cause `__file__` to point at `code_updates/`, so this
    helper reads the real app root injected by the main entrypoint and avoids
    direct `sys._SUPERPICKY_APP_ROOT` access across modules.
    """
    app_root = getattr(sys, "_SUPERPICKY_APP_ROOT", None)
    if isinstance(app_root, str) and app_root:
        return app_root
    return None


def set_runtime_app_root(app_root: str) -> str:
    """
    写入补丁覆盖层共享的真实应用根目录。
    Persist the real application root shared by the patch overlay.

    这里使用 `setattr` 写入运行时动态属性，既保留现有打包/补丁行为，
    也避免直接赋值 `sys._SUPERPICKY_APP_ROOT` 触发 Pylance 属性告警。
    This helper uses `setattr` to preserve the existing runtime contract while
    avoiding direct `sys._SUPERPICKY_APP_ROOT` assignments that trip Pylance.
    """
    setattr(sys, "_SUPERPICKY_APP_ROOT", app_root)
    return app_root


def get_bundled_resource_dir() -> Path:
    """返回静态打包资源根目录 / Return the root directory for bundled static resources."""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            executable = Path(sys.executable).resolve()
            if executable.parent.name == "MacOS":
                return executable.parents[1] / "Resources"
        meipass = get_runtime_meipass()
        if meipass is not None:
            return Path(meipass)
    app_root = get_runtime_app_root()
    if app_root is not None:
        return Path(app_root)
    return get_app_install_dir()


def get_app_internal_dir() -> Path:
    """
    返回应用内部运行目录 / Return the application internal runtime directory.

    Windows one-dir 打包产物使用安装目录下的 `_internal/`。
    Other environments fall back to the bundled resource directory.
    """
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        return get_app_install_dir() / "_internal"
    return get_bundled_resource_dir()


def get_install_scoped_resource_path(
    relative_path: str, *, packaged_relative_path: Optional[str] = None
) -> Path:
    """
    返回安装目录约束下的资源路径 / Return a resource path constrained to the install directory when required.

    Windows Lite 打包环境下，模型/数据库/运行时等可变资源必须位于安装目录。
    Other environments keep using the bundled resource layout.
    """
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        target_relative_path = packaged_relative_path or relative_path
        return get_app_internal_dir() / target_relative_path
    return get_bundled_resource_dir() / relative_path


def get_packaged_model_relative_path(relative_path: str) -> str:
    """返回 Windows Lite 打包环境下模型的内部相对路径 / Return the packaged relative path for models in Windows Lite builds."""
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("models/"):
        return "models/" + normalized.split("/", 1)[1]
    return normalized


def resource_path(relative_path: str) -> str:
    """
    返回打包资源路径，兼容开发环境与 PyInstaller / Return a packaged resource path compatible with development mode and PyInstaller.

    `relative_path` 是资源相对路径，例如 `models/yolo11l-seg.pt`。
    这里只用于内置资源定位，不能拿来定位用户配置或用户数据。
    This is only for bundled resource lookup and must not be used for user config or user data paths.
    """
    return str(get_bundled_resource_dir() / relative_path)


def get_app_config_dir(app_name: str = "SuperPicky") -> Path:
    """
    返回跨平台应用配置目录（存放 advanced_config.json、补丁等程序配置）。
    Return the cross-platform application config directory.

    ⚠️  与 get_app_data_dir() 完全不同的路径，请勿混用：
      macOS : ~/Library/Application Support/SuperPicky/
      Windows: ~/AppData/Local/SuperPicky/
      Linux  : ~/.config/SuperPicky/

    用途：advanced_config.json、code_updates/（补丁目录）等程序级配置。
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / app_name
    return Path.home() / ".config" / app_name


def get_app_data_dir(app_name: str = "SuperPicky") -> Path:
    """
    返回跨平台用户数据目录（存放 birdid 设置等用户产物）。
    Return the cross-platform user data directory.

    ⚠️  现已统一使用标准配置目录，与 get_app_config_dir() 返回相同路径：
      macOS : ~/Library/Application Support/SuperPicky/
      Windows: ~/AppData/Local/SuperPicky/
      Linux  : ~/.config/SuperPicky/

    用途：birdid_dock_settings.json 等用户可见的数据文件。
    切勿用于存放补丁或程序内部配置（应使用 get_app_config_dir()）。
    """
    return get_app_config_dir(app_name)


def get_patch_dir(app_name: str = "SuperPicky") -> Path:
    """返回在线补丁目录 / Return the online patch directory."""
    return get_app_config_dir(app_name) / "code_updates"


def get_birdid_settings_path(app_name: str = "SuperPicky") -> Path:
    """返回 BirdID Dock 设置文件路径 / Return the BirdID Dock settings file path."""
    return get_app_data_dir(app_name) / "birdid_dock_settings.json"


def get_birdname_settings_path(app_name: str = "SuperPicky") -> Path:
    """
    返回 BirdName IOC 设置文件路径 / Return the BirdName IOC settings file path.

    该文件属于全局用户配置，应统一收敛到标准配置目录下的 ioc/ 子目录。
    This file belongs to global user configuration and should live under the standard config directory's ioc/ subdirectory.
    """
    settings_dir = get_app_config_dir(app_name) / "ioc"
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir / "birdname_settings.ini"


_override_cache: Optional[Dict[str, Any]] = None
_override_lock = threading.RLock()


def _load_override_file() -> Dict[str, Any]:
    """
    线程安全加载 advanced_config.json，并做进程内缓存。
    Load advanced_config.json in a thread-safe way and cache it per process.

    文件不存在或解析失败时返回空字典，让调用方回退默认值。
    Returns an empty dict when the file is missing or invalid so callers can fall back to defaults.
    """
    global _override_cache
    with _override_lock:
        if _override_cache is not None:
            return _override_cache

        cfg_path = get_app_config_dir() / "advanced_config.json"
        if not cfg_path.exists():
            _override_cache = {}
            return _override_cache

        try:
            _override_cache = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            _override_cache = {}
        return _override_cache if _override_cache is not None else {}


def _parse_bool(value: Optional[str], default: bool) -> bool:
    """
    把字符串值解析为布尔值 / Parse a string-like value into a boolean.

    支持 `1/true/yes/on` 和 `0/false/no/off`，否则返回默认值。
    Supports `1/true/yes/on` and `0/false/no/off`, otherwise returns the default.
    """
    if value is None:
        return default
    norm = str(value).strip().lower()
    if norm in {"1", "true", "yes", "on"}:
        return True
    if norm in {"0", "false", "no", "off"}:
        return False
    return default


def _env_or_override(name: str, override_key: Optional[str], default: Any) -> Any:
    """
    按 ENV > JSON > 默认值 的优先级读取覆盖值 / Read an override using the priority order ENV > JSON > default.

    这里不做类型转换，调用方自行转成 `int`、`float` 或 `str`。
    No type conversion is done here; callers should convert to `int`, `float`, or `str` themselves.
    """
    env_value = os.getenv(name)
    if env_value is not None and str(env_value).strip() != "":
        return env_value

    if override_key:
        loaded = _load_override_file()
        if override_key in loaded:
            return loaded.get(override_key)

    return default


@dataclass
class FileConfig:
    """
    文件处理相关静态配置 / Static configuration related to file handling.

    这些列表会被 RAW/JPG 分类逻辑直接消费。
    These lists are consumed directly by RAW/JPG classification logic.
    """

    RAW_EXTENSIONS: List[str] = field(
        default_factory=lambda: [
            ".nef",
            ".cr2",
            ".cr3",
            ".arw",
            ".raf",
            ".orf",
            ".rw2",
            ".pef",
            ".dng",
            ".3fr",
            ".iiq",
        ]
    )
    JPG_EXTENSIONS: List[str] = field(default_factory=lambda: [".jpg", ".jpeg"])


@dataclass
class DirectoryConfig:
    """
    输出目录与报告文件命名配置 / Naming configuration for output directories and report files.

    修改这些值会影响结果目录结构与报告文件名。
    Changing these values affects result folder layout and report filenames.
    """

    EXCELLENT_DIR: str = "优秀"
    STANDARD_DIR: str = "标准"
    NO_BIRDS_DIR: str = "没鸟"
    TEMP_DIR: str = "_temp"
    REDBOX_DIR: str = "Redbox"
    CROP_TEMP_DIR: str = ".crop_temp"
    OLD_ALGORITHM_EXCELLENT: str = "老算法优秀"
    NEW_ALGORITHM_EXCELLENT: str = "新算法优秀"
    BOTH_ALGORITHMS_EXCELLENT: str = "双算法优秀"
    ALGORITHM_DIFF_DIR: str = "算法差异"
    LOG_FILE: str = ".process_log.txt"
    REPORT_FILE: str = ".report.db"
    COMPARISON_REPORT_FILE: str = ".algorithm_comparison.csv"


@dataclass
class AIConfig:
    """
    AI 模型与推理相关静态配置 / Static configuration related to AI models and inference.

    这些值服务于模型定位与基础推理行为，不替代高级用户参数。
    These values support model lookup and baseline inference behavior and do not replace advanced user-facing parameters.
    """

    MODEL_FILE: str = "models/yolo11l-seg.pt"
    BIRD_CLASS_ID: int = 14
    TARGET_IMAGE_SIZE: int = 1024
    CENTER_THRESHOLD: float = 0.15
    SHARPNESS_NORMALIZATION: Optional[str] = None

    def get_model_path(self) -> str:
        """
        返回主模型的实际可访问路径 / Return the actual accessible path to the main model.

        调用方不应自行拼 PyInstaller 临时目录。
        Callers should not manually stitch together PyInstaller temporary paths.
        """
        return str(
            get_install_scoped_resource_path(
                self.MODEL_FILE,
                packaged_relative_path=get_packaged_model_relative_path(
                    self.MODEL_FILE
                ),
            )
        )


@dataclass
class UIConfig:
    """
    UI 展示层静态常量 / Static constants for the UI presentation layer.

    这里只放显示缩放和进度边界，不放业务阈值。
    This group is for display scaling and progress bounds, not business thresholds.
    """

    CONFIDENCE_SCALE: float = 100.0
    AREA_SCALE: float = 1000.0
    SHARPNESS_SCALE: int = 20
    PROGRESS_MIN: int = 0
    PROGRESS_MAX: int = 100
    BEEP_COUNT: int = 3


@dataclass
class CSVConfig:
    """
    CSV 报告结构配置 / CSV report structure configuration.

    `HEADERS` 定义导出列顺序，兼容性要求较高。
    `HEADERS` defines export-column order and carries relatively high compatibility requirements.
    """

    HEADERS: List[str] = field(
        default_factory=lambda: [
            "filename",
            "found_bird",
            "AI score",
            "bird_centre_x",
            "bird_centre_y",
            "bird_area",
            "s_bird_area",
            "laplacian_var",
            "sobel_var",
            "fft_high_freq",
            "contrast",
            "edge_density",
            "background_complexity",
            "motion_blur",
            "normalized_new",
            "composite_score",
            "result_new",
            "dominant_bool",
            "centred_bool",
            "sharp_bool",
            "class_id",
        ]
    )


@dataclass
class ServerConfig:
    """
    BirdID 服务默认配置 / Default configuration for the BirdID service.

    这些值可被 ENV 覆盖，并影响绑定地址、启动等待和健康检查节奏。
    These values can be overridden by ENV and affect bind address, startup wait, and health-check cadence.
    """

    HOST: str = "127.0.0.1"
    PORT: int = 5156
    HEALTH_TIMEOUT_SECONDS: float = 2.0
    STARTUP_WAIT_SECONDS: float = 10.0
    POLL_INTERVAL_SECONDS: float = 0.5

    @classmethod
    def load(cls) -> "ServerConfig":
        """
        按覆盖优先级构造 ServerConfig / Build ServerConfig using the override priority rules.

        类型转换统一在这里做，避免调用方重复解析 ENV。
        Type coercion is centralized here so callers do not repeat ENV parsing.
        """
        host = str(_env_or_override("SUPERPICKY_SERVER_HOST", None, cls.HOST))
        port = int(_env_or_override("SUPERPICKY_SERVER_PORT", None, cls.PORT))
        health_timeout = float(
            _env_or_override(
                "SUPERPICKY_SERVER_HEALTH_TIMEOUT", None, cls.HEALTH_TIMEOUT_SECONDS
            )
        )
        startup_wait = float(
            _env_or_override(
                "SUPERPICKY_SERVER_STARTUP_WAIT", None, cls.STARTUP_WAIT_SECONDS
            )
        )
        poll = float(
            _env_or_override(
                "SUPERPICKY_SERVER_POLL_INTERVAL", None, cls.POLL_INTERVAL_SECONDS
            )
        )
        return cls(
            HOST=host,
            PORT=port,
            HEALTH_TIMEOUT_SECONDS=health_timeout,
            STARTUP_WAIT_SECONDS=startup_wait,
            POLL_INTERVAL_SECONDS=poll,
        )


@dataclass
class EndpointConfig:
    """
    远程服务端点默认配置 / Default configuration for remote service endpoints.

    这些 URL 会影响下载页、eBird 查询与 Nominatim 反查等网络行为。
    These URLs affect network behavior such as download pages, eBird queries, and Nominatim reverse lookups.
    """

    MIRROR_BASE_URL: str = "http://1.119.150.179:59080/superpicky"
    UPDATE_DOWNLOAD_PAGE: str = "https://superpicky.jamesphotography.com.au/#download"
    EBIRD_API_BASE: str = "https://api.ebird.org/v2"
    NOMINATIM_REVERSE_URL: str = "https://nominatim.openstreetmap.org/reverse"

    @classmethod
    def load(cls) -> "EndpointConfig":
        """
        按覆盖优先级构造 EndpointConfig / Build EndpointConfig using the override priority rules.

        统一入口便于未来继续扩展 ENV 覆盖。
        A unified entry point makes future ENV override expansion easier.
        """
        return cls(
            MIRROR_BASE_URL=str(
                _env_or_override(
                    "SUPERPICKY_MIRROR_BASE_URL", None, cls.MIRROR_BASE_URL
                )
            ),
            UPDATE_DOWNLOAD_PAGE=str(
                _env_or_override(
                    "SUPERPICKY_DOWNLOAD_PAGE", None, cls.UPDATE_DOWNLOAD_PAGE
                )
            ),
            EBIRD_API_BASE=str(
                _env_or_override("SUPERPICKY_EBIRD_API_BASE", None, cls.EBIRD_API_BASE)
            ),
            NOMINATIM_REVERSE_URL=str(
                _env_or_override(
                    "SUPERPICKY_NOMINATIM_REVERSE_URL", None, cls.NOMINATIM_REVERSE_URL
                )
            ),
        )


class Config:
    """
    主配置聚合类 / Main configuration aggregation class.

    这是项目中最常用的统一读取入口，用来整合不同层次的配置。
    This is the most common unified read entry point used to aggregate different configuration layers.
    """

    def __init__(self):
        """
        构造统一配置对象 / Construct the unified configuration object.

        初始化时建立静态常量分组，并加载服务与端点配置。
        Initialization builds static config groups and loads service and endpoint configuration.
        """
        self.file = FileConfig()
        self.directory = DirectoryConfig()
        self.ai = AIConfig()
        self.ui = UIConfig()
        self.csv = CSVConfig()
        self.server = ServerConfig.load()
        self.endpoints = EndpointConfig.load()

    def get_directory_names(self) -> Dict[str, str]:
        """
        返回常用输出目录名映射 / Return a mapping of commonly used output directory names.

        适合 UI 展示和流程内统一引用目录名。
        Useful for UI display and for consistent directory references inside processing flows.
        """
        return {
            "excellent": self.directory.EXCELLENT_DIR,
            "standard": self.directory.STANDARD_DIR,
            "no_birds": self.directory.NO_BIRDS_DIR,
            "temp": self.directory.TEMP_DIR,
            "redbox": self.directory.REDBOX_DIR,
            "crop_temp": self.directory.CROP_TEMP_DIR,
        }

    def is_raw_file(self, filename: str) -> bool:
        """
        判断文件名是否属于 RAW 扩展名集合 / Check whether a filename belongs to the RAW extension set.

        这里只按扩展名判断，不检查内容或 MIME。
        This only checks file extensions and does not inspect content or MIME type.
        """
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.file.RAW_EXTENSIONS

    def is_jpg_file(self, filename: str) -> bool:
        """
        判断文件名是否属于 JPG/JPEG 扩展名集合 / Check whether a filename belongs to the JPG/JPEG extension set.

        这是轻量判断入口，不做内容探测。
        This is a lightweight classification entry and does not inspect file contents.
        """
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.file.JPG_EXTENSIONS


_MISSING = object()


class LazyRegistry:
    """
    线程安全区加载注册器 / Thread-safe lazy registry.

    目标是避免重复初始化重量级对象，并提供统一的共享入口。
    Its goal is to avoid repeated heavy initialization and provide a unified sharing entry point.
    """

    def __init__(self):
        """初始化内部存储和锁 / Initialize the internal storage and lock."""
        self._values: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def get_or_create(self, key: str, factory: Callable[[], Any]) -> Any:
        """
        读取缓存对象，不存在时在锁内创建并缓存 / Read a cached object and create/cache it inside the lock if it is missing.

        采用无锁快速读取加锁内二次检查，避免并发重复创建。
        This uses a fast unlocked read plus a locked second check to avoid duplicate concurrent construction.
        """
        value = self._values.get(key, _MISSING)
        if value is not _MISSING:
            return value
        with self._lock:
            value = self._values.get(key, _MISSING)
            if value is _MISSING:
                value = factory()
                self._values[key] = value
            return value

    def get(self, key: str, default: Any = None) -> Any:
        """读取缓存值，不触发创建 / Read a cached value without triggering creation."""
        with self._lock:
            return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        显式设置缓存值 / Explicitly set a cached value.

        不要用它存放临时业务状态。
        Do not use this to stash temporary business state.
        """
        with self._lock:
            self._values[key] = value

    def clear(self, key: str) -> None:
        """
        清除单个缓存项 / Clear a single cached item.

        适合测试隔离或强制下次重建。
        Useful for test isolation or forcing the next access to rebuild the object.
        """
        with self._lock:
            self._values.pop(key, None)

    def clear_all(self) -> None:
        """
        清空所有缓存项 / Clear all cached items.

        若缓存对象持有外部资源，应先确保存在显式关闭流程。
        If cached objects hold external resources, make sure an explicit shutdown flow exists first.
        """
        with self._lock:
            self._values.clear()


_lazy_registry = LazyRegistry()


def get_lazy_registry() -> LazyRegistry:
    """
    返回全局懒加载注册器实例 / Return the global lazy-registry instance.

    调用方应通过此入口获取共享注册器，避免自行新建导致缓存割裂。
    Callers should use this entry point to get the shared registry and avoid cache fragmentation caused by creating their own registries.
    """
    return _lazy_registry


def get_best_device():
    """
    返回当前环境下最合适的 Torch 设备对象 / Return the most appropriate Torch device object for the current environment.

    顺序为：Apple Silicon 先 MPS 再 CPU，Intel Mac 直接 CPU，其他平台先 CUDA 再 CPU。
    The order is: Apple Silicon uses MPS then CPU, Intel Mac uses CPU directly,
    other platforms use CUDA then CPU.

    任意检测异常时保守回退到 CPU。
    On any detection failure, conservatively fall back to CPU.
    """
    try:
        torch_module = _get_torch_module()
        if torch_module is None:
            return _FallbackDevice("cpu")
        system = platform.system()
        if system == "Darwin":
            # 仅 Apple Silicon (arm64) 使用 MPS。Intel Mac 的 MPS 后端跑在老款
            # AMD 独显（如 Radeon Pro 580X / GCN4）上：FP16 比 FP32 慢约 38×，
            # 且 torchvision::nms 在 MPS 未实现需回退 CPU 重算，整体远慢于纯 CPU，
            # 因此 Intel Mac 直接走 CPU（FP32）。
            # Only Apple Silicon (arm64) uses MPS. On Intel Macs the MPS backend
            # runs on legacy AMD dGPUs (e.g. Radeon Pro 580X / GCN4) where FP16 is
            # ~38x slower than FP32 and torchvision::nms is unimplemented (forcing a
            # CPU re-run), making MPS far slower than plain CPU — so Intel Mac uses CPU.
            if (
                platform.machine() == "arm64"
                and torch_module.backends.mps.is_available()
            ):
                return torch_module.device("mps")
            return torch_module.device("cpu")

        if torch_module.cuda.is_available():
            return torch_module.device("cuda")
        return torch_module.device("cpu")
    except Exception:
        torch_module = _get_torch_module()
        return (
            torch_module.device("cpu")
            if torch_module is not None
            else _FallbackDevice("cpu")
        )


def migrate_old_data() -> bool:
    """
    迁移旧路径数据到新路径 / Migrate old path data to new path.

    检测 ~/Documents/SuperPicky_Data 目录是否存在数据，
    如果存在则迁移到 get_app_config_dir() 返回的标准配置目录。

    Returns:
        bool: 迁移是否成功（如果没有旧数据也返回 True）
    """
    try:
        old_data_dir = Path.home() / "Documents" / "SuperPicky_Data"
        new_data_dir = get_app_config_dir()

        if not old_data_dir.exists() or not old_data_dir.is_dir():
            return True

        files = list(old_data_dir.iterdir())
        if not files:
            return True

        logger.info(f"检测到旧数据目录: {old_data_dir}")
        logger.info(f"开始迁移到新目录: {new_data_dir}")

        new_data_dir.mkdir(parents=True, exist_ok=True)

        copied_files = []
        for file_path in files:
            try:
                dest_path = new_data_dir / file_path.name
                if file_path.is_file():
                    import shutil

                    shutil.copy2(file_path, dest_path)
                    copied_files.append(file_path.name)
                elif file_path.is_dir():
                    import shutil

                    shutil.copytree(file_path, dest_path, dirs_exist_ok=True)
                    copied_files.append(file_path.name)
            except Exception as e:
                logger.error(f"复制文件失败 {file_path.name}: {e}")
                return False

        logger.info(f"成功迁移 {len(copied_files)} 个文件/目录")

        for file_name in copied_files:
            try:
                old_path = old_data_dir / file_name
                if old_path.exists():
                    if old_path.is_file():
                        old_path.unlink()
                    elif old_path.is_dir():
                        import shutil

                        shutil.rmtree(old_path)
            except Exception as e:
                logger.warning(f"删除旧文件失败 {file_name}: {e}")

        try:
            if old_data_dir.exists() and old_data_dir.is_dir():
                import shutil

                shutil.rmtree(old_data_dir)
                logger.info(f"已删除旧数据目录: {old_data_dir}")
        except Exception as e:
            logger.warning(f"删除旧目录失败: {e}")

        logger.info("数据迁移完成")
        return True

    except Exception as e:
        logger.error(f"数据迁移失败: {e}")
        return False


def migrate_legacy_ioc_settings(app_name: str = "SuperPicky") -> bool:
    """
    迁移旧的用户主目录 IOC 设置到标准配置目录。
    Migrate legacy IOC settings from the user home directory to the standard config directory.

    仅处理 ~/.superpicky/ioc/birdname_settings.ini 这类全局配置残留，
    不涉及照片目录中的 .superpicky 工作文件。
    """
    try:
        import shutil

        old_settings_path = (
            Path.home() / ".superpicky" / "ioc" / "birdname_settings.ini"
        )
        new_settings_path = get_birdname_settings_path(app_name)

        if not old_settings_path.exists() or not old_settings_path.is_file():
            return True

        if new_settings_path.exists():
            logger.info(f"检测到新的 IOC 配置已存在，保留新路径: {new_settings_path}")
            return True

        shutil.copy2(old_settings_path, new_settings_path)
        logger.info(f"已迁移 IOC 配置: {old_settings_path} -> {new_settings_path}")

        try:
            old_settings_path.unlink()
        except Exception as e:
            logger.warning(f"删除旧 IOC 配置失败: {e}")
            return True

        old_ioc_dir = old_settings_path.parent
        old_superpicky_dir = old_ioc_dir.parent
        try:
            if (
                old_ioc_dir.exists()
                and old_ioc_dir.is_dir()
                and not any(old_ioc_dir.iterdir())
            ):
                old_ioc_dir.rmdir()
            if (
                old_superpicky_dir.exists()
                and old_superpicky_dir.is_dir()
                and not any(old_superpicky_dir.iterdir())
            ):
                old_superpicky_dir.rmdir()
        except Exception as e:
            logger.warning(f"清理旧 IOC 目录失败: {e}")

        return True
    except Exception as e:
        logger.error(f"IOC 配置迁移失败: {e}")
        return False


config = Config()
