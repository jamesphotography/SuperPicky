#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SuperPicky Windows 构建脚本。

支持三种模式：
- cpu: 使用 .venv + requirements.txt 构建 CPU 版本
- cuda: 使用 .venv-cuda + requirements_cuda.txt 构建 CUDA 版本
- cuda-patch: 先构建 CPU，再准备 .venv-cuda 并构建 CUDA，最后导出 CUDA 差异补丁目录
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


APP_NAME = "SuperPicky"
ROOT_DIR = Path(__file__).resolve().parent
INNO_DIR = ROOT_DIR / "inno"
BUILD_INFO_FILE = ROOT_DIR / "core" / "build_info.py"
DOWNLOAD_MODELS_SCRIPT = ROOT_DIR / "scripts" / "download_models.py"
SPEC_FILE = ROOT_DIR / "SuperPicky_win64.spec"
LITE_SPEC_FILE = ROOT_DIR / "SuperPicky_lite_win.spec"
CPU_VENV_DIR = ROOT_DIR / ".venv"
CUDA_VENV_DIR = ROOT_DIR / ".venv-cuda"
DEFAULT_PATCH_OUTPUT_ROOT = ROOT_DIR / "output"
STANDARD_INNO_TEMPLATE = INNO_DIR / "SuperPicky.iss"
LITE_INNO_TEMPLATE = INNO_DIR / "SuperPicky-lite.iss"
CUDA_INNO_TEMPLATE = INNO_DIR / "SuperPicky-cuda.iss"
PATCH_INNO_TEMPLATE = INNO_DIR / "SuperPicky_CUDA_Patch.iss"
INNO_LANGUAGE_FILE = INNO_DIR / "ChineseSimplified.isl"
CPU_REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
CUDA_REQUIREMENTS_FILE = ROOT_DIR / "requirements_cuda.txt"
PATCH_MANIFEST_RELATIVE_PATH = Path("_internal") / "cuda_patch_manifest.txt"
CPU_INSTALLER_STAGING_DIRNAME = "installer_cpu"
CUDA_INSTALLER_STAGING_DIRNAME = "installer_cuda"
LITE_INSTALLER_STAGING_DIRNAME = "installer_lite"
CUDA_PATCH_PORTABLE_DIRNAME = "cuda_patch"
CUDA_PATCH_INSTALLER_STAGING_DIRNAME = "cuda_patch_installer"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildPaths:
    label: str
    work_dir: Path
    dist_dir: Path
    bundle_dir: Path


@dataclass(frozen=True)
class BuildConfig:
    build_type: str
    copy_dir: Path | None
    no_zip: bool
    debug: bool
    app_version: str
    commit_hash: str


def configure_logging(debug: bool) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict") # pyright: ignore[reportAttributeAccessIssue]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="strict") # pyright: ignore[reportAttributeAccessIssue]

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(handler)


def log_step(title: str) -> None:
    logger.info("[========================================]")
    logger.info(title)
    logger.info("[========================================]")


def log_verbose(message: str, *args) -> None:
    logger.debug(message, *args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SuperPicky Windows 构建脚本")
    parser.add_argument(
        "--build-type",
        choices=["cpu", "cuda", "cuda-patch", "lite"],
        default="lite",
        help="构建类型：cpu, cuda, cuda-patch, lite (默认: lite)",
    )
    parser.add_argument("--version", help="覆盖基础版本号，例如 4.2.0")
    parser.add_argument("--copy-dir", help="复制最终产物的目标目录")
    parser.add_argument("--no-zip", action="store_true", help="跳过创建便携压缩包")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser.parse_args()


def run_command(
    command: Sequence[str],
    *,
    cwd: Path = ROOT_DIR,
    check: bool = True,
    capture_output: bool = False,
    label: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("执行命令: %s", " ".join(command))

    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        capture_output=capture_output,
    )

    if check and result.returncode != 0:
        if capture_output:
            if result.stdout:
                logger.error(result.stdout.strip())
            if result.stderr:
                logger.error(result.stderr.strip())
        raise RuntimeError(f"{label or '命令执行'}失败，返回码: {result.returncode}")

    return result


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"复制源目录不存在: {src}")
    remove_path(dst)
    shutil.copytree(src, dst)


def copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"复制源文件不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def get_build_paths(label: str) -> BuildPaths:
    dist_dir = ROOT_DIR / f"dist_{label}"
    return BuildPaths(
        label=label,
        work_dir=ROOT_DIR / f"build_dist_{label}",
        dist_dir=dist_dir,
        bundle_dir=dist_dir / APP_NAME,
    )


def load_required_models() -> list[dict[str, str]]:
    fallback = [
        {"filename": "model20240824.pth", "dest_dir": "models"},
        {"filename": "superFlier_efficientnet.pth", "dest_dir": "models"},
        {"filename": "cub200_keypoint_resnet50_slim.pth", "dest_dir": "models"},
        {"filename": "avonet.db", "dest_dir": "birdid/data"},
        {"filename": "cfanet_iaa_ava_res50-3cd62bb3.pth", "dest_dir": "models"},
        {"filename": "yolo11l-seg.pt", "dest_dir": "models"},
    ]

    if not DOWNLOAD_MODELS_SCRIPT.exists():
        logger.warning("未找到 download_models.py，使用默认模型列表")
        return fallback

    try:
        module_ast = ast.parse(DOWNLOAD_MODELS_SCRIPT.read_text(encoding="utf-8"))
        models = None
        for node in module_ast.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "MODELS_TO_DOWNLOAD":
                        models = ast.literal_eval(node.value)
                        break
            if models is not None:
                break
        if models is None:
            raise RuntimeError("download_models.py 中未找到 MODELS_TO_DOWNLOAD")
        log_verbose("[成功] 已从 download_models.py 加载模型列表")
        return [{"filename": item["filename"], "dest_dir": item["dest_dir"]} for item in models]
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        logger.warning("无法从 download_models.py 加载模型列表，使用默认列表: %s", exc)
        return fallback


REQUIRED_MODELS = load_required_models()


def find_missing_models() -> list[Path]:
    missing = []
    for model in REQUIRED_MODELS:
        model_path = ROOT_DIR / model["dest_dir"] / model["filename"]
        if not model_path.exists():
            missing.append(model_path)
    return missing


def ensure_models(python_exe: Path) -> None:
    log_step("步骤 0: 检查并下载模型文件")
    missing = find_missing_models()
    if not missing:
        log_verbose("[成功] 所有模型文件已就绪")
        return

    logger.warning("缺失 %d 个模型文件，开始下载", len(missing))
    run_command(
        [str(python_exe), str(DOWNLOAD_MODELS_SCRIPT)],
        label="模型下载",
    )

    missing = find_missing_models()
    if missing:
        for path in missing:
            logger.error("仍然缺失: %s", path)
        raise RuntimeError("模型下载后仍有缺失")

    log_verbose("[成功] 所有模型文件已就绪")


def read_app_version() -> str:
    content = (ROOT_DIR / "constants.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*["\']([0-9A-Za-z._-]+)["\']', content)
    return match.group(1) if match else "0.0.0"


def get_commit_hash() -> str:
    try:
        result = run_command(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            label="获取提交哈希",
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        pass

    content = BUILD_INFO_FILE.read_text(encoding="utf-8")
    match = re.search(r'COMMIT_HASH\s*=\s*"([^"]*)"', content)
    return match.group(1) if match else "unknown"


def inject_build_info(commit_hash: str, release_channel: str = "official") -> Path | None:
    log_step("步骤 1: 注入构建元数据")
    if not BUILD_INFO_FILE.exists():
        logger.warning("未找到 build_info.py，跳过注入")
        return None

    backup_path = BUILD_INFO_FILE.with_suffix(".py.backup")
    shutil.copy2(BUILD_INFO_FILE, backup_path)

    content = BUILD_INFO_FILE.read_text(encoding="utf-8")
    updated = re.sub(
        r'COMMIT_HASH\s*=\s*".*"',
        f'COMMIT_HASH = "{commit_hash}"',
        content,
        count=1,
    )
    updated = re.sub(
        r'RELEASE_CHANNEL\s*=\s*".*"',
        f'RELEASE_CHANNEL = "{release_channel}"',
        updated,
        count=1,
    )
    BUILD_INFO_FILE.write_text(updated, encoding="utf-8")
    log_verbose("[成功] 已写入 COMMIT_HASH=%s RELEASE_CHANNEL=%s", commit_hash, release_channel)
    return backup_path


def restore_build_info(backup_path: Path | None) -> None:
    if backup_path and backup_path.exists():
        shutil.move(str(backup_path), str(BUILD_INFO_FILE))


def spec_file_for(build_type: str) -> Path:
    if build_type == "lite":
        return LITE_SPEC_FILE
    return SPEC_FILE


def ensure_spec_file(build_type: str) -> None:
    spec_file = spec_file_for(build_type)
    if not spec_file.exists():
        raise FileNotFoundError(f"缺少 spec 文件: {spec_file}")
    log_verbose("[信息] %s 构建将使用 spec: %s", build_type, spec_file.name)


def check_python_environment(python_exe: Path, label: str) -> None:
    log_verbose("[信息] 检查 Python 环境 (%s): %s", label, python_exe)
    run_command([str(python_exe), "-c", "import sys; print(sys.executable)"], label=f"{label} Python 检查")
    run_command([str(python_exe), "-c", "import PyInstaller"], label=f"{label} PyInstaller 检查")
    log_verbose("[成功] %s 环境可用", label)


def python_in_venv(venv_dir: Path) -> Path:
    return venv_dir / "Scripts" / "python.exe"


def ensure_virtual_environment(
    *,
    bootstrap_python: Path,
    venv_dir: Path,
    requirements_file: Path,
    label: str,
) -> Path:
    log_step(f"步骤 4: 准备 {label} 虚拟环境")
    venv_python = python_in_venv(venv_dir)

    if not venv_python.exists():
        logger.info("创建 %s 虚拟环境...", label)
        run_command([str(bootstrap_python), "-m", "venv", str(venv_dir)], label=f"创建 {label} 虚拟环境")

    run_command([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], label=f"升级 {label} 环境 pip")
    run_command(
        [str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)],
        label=f"安装 {label} 依赖",
    )

    check_python_environment(venv_python, label)
    return venv_python


def ensure_cpu_environment(bootstrap_python: Path) -> Path:
    return ensure_virtual_environment(
        bootstrap_python=bootstrap_python,
        venv_dir=CPU_VENV_DIR,
        requirements_file=CPU_REQUIREMENTS_FILE,
        label="CPU",
    )


def ensure_cuda_environment(bootstrap_python: Path) -> Path:
    return ensure_virtual_environment(
        bootstrap_python=bootstrap_python,
        venv_dir=CUDA_VENV_DIR,
        requirements_file=CUDA_REQUIREMENTS_FILE,
        label="CUDA",
    )


def clean_build_outputs() -> None:
    log_step("步骤 2: 清理旧的构建目录")
    for label in ("cpu", "cuda", "cuda_patch", "lite"):
        paths = get_build_paths(label)
        remove_path(paths.work_dir)
        remove_path(paths.dist_dir)
    remove_path(ROOT_DIR / "build_dist")
    remove_path(ROOT_DIR / "dist")
    log_verbose("[成功] 已清理构建目录")


def build_bundle(python_exe: Path, build_paths: BuildPaths, spec_file: Path) -> None:
    log_step(f"步骤 3: 构建 {build_paths.label.upper()} 版本")
    remove_path(build_paths.work_dir)
    remove_path(build_paths.dist_dir)

    pyinstaller_command = [
        str(python_exe),
        "-m",
        "PyInstaller",
        str(spec_file),
        "--clean",
        "--noconfirm",
        f"--workpath={build_paths.work_dir}",
        f"--distpath={build_paths.dist_dir}",
    ]
    logger.info("启动 PyInstaller 构建：开始")
    logger.info("PyInstaller 参数：%s", " ".join(str(item) for item in pyinstaller_command[2:]))
    run_command(
        pyinstaller_command,
        capture_output=not logger.isEnabledFor(logging.DEBUG),
        label=f"{build_paths.label} PyInstaller 构建",
    )

    exe_path = build_paths.bundle_dir / f"{APP_NAME}.exe"
    if not exe_path.exists():
        raise FileNotFoundError(f"构建完成后未找到可执行文件: {exe_path}")
    logger.info("PyInstaller 构建成功！")
    logger.info("构建产物位置：%s", exe_path)
    log_verbose("[成功] %s 构建完成", build_paths.label.upper())


def create_zip_archive(source_dir: Path, archive_path: Path) -> None:
    """
    使用标准库创建 ZIP 包 / Create ZIP archives with the Python standard library.
    """

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.unlink(missing_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_dir():
                continue
            archive.write(file_path, arcname=str(Path(source_dir.name) / file_path.relative_to(source_dir)))


def archive_name_for(label: str, app_version: str, commit_hash: str) -> str:
    return f"{APP_NAME}_Win64_{app_version}_{commit_hash}_{label}.zip"


def normalize_version(version: str) -> str:
    """确保版本号以 'v' 前缀开头。

    Ensure version string starts with 'v' prefix.

    参数 / Parameters:
        version (str): 原始版本号，例如 "4.2.0" 或 "v4.2.0"

    返回 / Return:
        str: 带 'v' 前缀的版本号，例如 "v4.2.0"
    """
    return version if version.startswith("v") else f"v{version}"


def update_inno_content(content: str, *, app_version: str, commit_hash: str) -> str:
    """替换 ISS 模板中的 #define 预处理器变量，注入版本号和提交哈希。

    Replace #define preprocessor variables in ISS template with version and commit hash.

    参数 / Parameters:
        content (str): ISS 模板原始内容
        app_version (str): 应用版本号（将自动添加 'v' 前缀）
        commit_hash (str): Git 提交哈希

    返回 / Return:
        str: 替换后的 ISS 内容
    """
    versioned = normalize_version(app_version)
    content = re.sub(
        r'(?m)^(#define\s+MyAppVersion\s+").*?(")\s*$',
        rf'\g<1>{versioned}\2',
        content,
    )
    content = re.sub(
        r'(?m)^(#define\s+MyAppCommitHash\s+").*?(")\s*$',
        rf'\g<1>{commit_hash}\2',
        content,
    )
    return content


def write_inno_script(
    template_path: Path,
    destination_path: Path,
    *,
    app_version: str,
    commit_hash: str,
) -> None:
    """读取 ISS 模板，注入版本号和哈希后写入目标路径。

    Read ISS template, inject version and hash, write to destination.

    参数 / Parameters:
        template_path (Path): ISS 模板文件路径
        destination_path (Path): 输出 ISS 文件路径
        app_version (str): 应用版本号
        commit_hash (str): Git 提交哈希
    """
    content = template_path.read_text(encoding="utf-8")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        update_inno_content(content, app_version=app_version, commit_hash=commit_hash),
        encoding="utf-8",
    )


def installer_staging_dir_name(label: str) -> str:
    if label == "cpu":
        return CPU_INSTALLER_STAGING_DIRNAME
    if label == "cuda":
        return CUDA_INSTALLER_STAGING_DIRNAME
    if label == "lite":
        return LITE_INSTALLER_STAGING_DIRNAME
    raise ValueError(f"不支持的标准安装包标签: {label}")


def inno_template_for(label: str) -> Path:
    """根据构建标签返回对应的 ISS 模板路径。

    Return the ISS template path for the given build label.

    参数 / Parameters:
        label (str): 构建标签，"lite" / "cuda" 或其他 (cpu/full)

    返回 / Return:
        Path: ISS 模板文件路径
    """
    if label == "lite":
        return LITE_INNO_TEMPLATE
    if label == "cuda":
        return CUDA_INNO_TEMPLATE
    return STANDARD_INNO_TEMPLATE


def prepare_standard_installer_staging(source_bundle_dir: Path, staging_root: Path, config: BuildConfig, *, label: str) -> Path:
    """准备标准安装包的 staging 目录，包含构建产物、ISS 脚本和依赖资源。

    Prepare standard installer staging directory with build artifacts, ISS script and dependencies.

    参数 / Parameters:
        source_bundle_dir (Path): PyInstaller 构建产物目录
        staging_root (Path): staging 根目录
        config (BuildConfig): 构建配置
        label (str): 构建标签（"cpu", "cuda", "lite"）

    返回 / Return:
        Path: 生成的 ISS 脚本路径
    """
    staging_dir = staging_root / installer_staging_dir_name(label)
    copy_tree(source_bundle_dir, staging_dir)
    template = inno_template_for(label)
    iss_filename = template.name
    write_inno_script(
        template,
        staging_dir / iss_filename,
        app_version=config.app_version,
        commit_hash=config.commit_hash,
    )
    copy_file(INNO_LANGUAGE_FILE, staging_dir / INNO_LANGUAGE_FILE.name)
    copy_tree(ROOT_DIR / "img", staging_dir / "img")
    log_verbose("[成功] 已准备标准安装包脚本目录: %s", staging_dir)
    return staging_dir / iss_filename


def publish_standard_build(
    *,
    label: str,
    build_paths: BuildPaths,
    config: BuildConfig,
    final_root: Path | None,
) -> tuple[Path, Path | None, Path]:
    final_bundle_dir = build_paths.bundle_dir if final_root is None else final_root / APP_NAME
    artifact_root = build_paths.dist_dir if final_root is None else final_root

    if final_root is not None:
        final_root.mkdir(parents=True, exist_ok=True)
        if build_paths.bundle_dir.resolve() != final_bundle_dir.resolve():
            copy_tree(build_paths.bundle_dir, final_bundle_dir)

    zip_source_dir = final_bundle_dir
    installer_script_path = prepare_standard_installer_staging(zip_source_dir, artifact_root, config, label=label)

    if not config.no_zip:
        zip_path = artifact_root / archive_name_for(label, config.app_version, config.commit_hash)
        create_zip_archive(zip_source_dir, zip_path)
        log_verbose("[成功] 已创建 ZIP 压缩包: %s", zip_path)
    else:
        zip_path = None
        log_verbose("[信息] 跳过 ZIP 压缩包创建 (--no-zip)")

    log_verbose("[成功] 已准备目录: %s", final_bundle_dir)
    return final_bundle_dir, zip_path, installer_script_path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_relative_files(directory: Path) -> set[Path]:
    return {path.relative_to(directory) for path in directory.rglob("*") if path.is_file()}


def write_patch_manifest(patch_dir: Path, patch_files: Sequence[Path]) -> Path:
    manifest_path = patch_dir / PATCH_MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [str(relative_path).replace("/", "\\") for relative_path in sorted(patch_files)]
    manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return manifest_path


def prepare_patch_directory(cpu_bundle: Path, cuda_bundle: Path, config: BuildConfig) -> Path:
    log_step("步骤 6: 分析 CPU/CUDA 差异并导出补丁")
    patch_root = config.copy_dir or DEFAULT_PATCH_OUTPUT_ROOT
    patch_dir = patch_root / CUDA_PATCH_PORTABLE_DIRNAME
    remove_path(patch_dir)
    patch_dir.mkdir(parents=True, exist_ok=True)

    cpu_files = list_relative_files(cpu_bundle)
    cuda_files = list_relative_files(cuda_bundle)
    all_files = sorted(cpu_files | cuda_files)

    different_count = 0
    cuda_only_count = 0
    copied_patch_files: list[Path] = []

    for relative_path in all_files:
        cpu_file = cpu_bundle / relative_path
        cuda_file = cuda_bundle / relative_path
        if not cuda_file.exists():
            continue

        needs_copy = False
        if not cpu_file.exists():
            cuda_only_count += 1
            needs_copy = True
        elif sha256(cpu_file) != sha256(cuda_file):
            different_count += 1
            needs_copy = True

        if needs_copy:
            copied_patch_files.append(relative_path)
            destination = patch_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cuda_file, destination)

    manifest_path = write_patch_manifest(patch_dir, copied_patch_files)
    log_verbose("[成功] 已导出差异文件: %d 个不同文件, %d 个 CUDA 独有文件", different_count, cuda_only_count)
    log_verbose("[成功] 已写入补丁清单: %s", manifest_path)
    log_verbose("[成功] 补丁目录: %s", patch_dir)
    return patch_dir


def prepare_patch_installer_staging(portable_patch_dir: Path, config: BuildConfig) -> Path:
    staging_root = config.copy_dir or DEFAULT_PATCH_OUTPUT_ROOT
    staging_dir = staging_root / CUDA_PATCH_INSTALLER_STAGING_DIRNAME
    copy_tree(portable_patch_dir, staging_dir)
    copy_tree(ROOT_DIR / "img", staging_dir / "img")
    copy_file(INNO_LANGUAGE_FILE, staging_dir / INNO_LANGUAGE_FILE.name)
    write_inno_script(
        PATCH_INNO_TEMPLATE,
        staging_dir / PATCH_INNO_TEMPLATE.name,
        app_version=config.app_version,
        commit_hash=config.commit_hash,
    )
    log_verbose("[成功] 已准备 CUDA 补丁安装包脚本目录: %s", staging_dir)
    return staging_dir / PATCH_INNO_TEMPLATE.name


def ensure_inno_templates() -> None:
    """检查所有 Inno Setup 模板和依赖文件是否存在。

    Verify all Inno Setup templates and dependency files exist.
    """
    for path in (STANDARD_INNO_TEMPLATE, LITE_INNO_TEMPLATE, CUDA_INNO_TEMPLATE, PATCH_INNO_TEMPLATE, INNO_LANGUAGE_FILE):
        if not path.exists():
            raise FileNotFoundError(f"缺少 Inno 相关文件: {path}")


def resolve_final_root(build_type: str, copy_dir: Path | None) -> Path | None:
    if build_type == "cuda-patch":
        return copy_dir or DEFAULT_PATCH_OUTPUT_ROOT
    return copy_dir


def build_single_target(config: BuildConfig, label: str, python_exe: Path) -> tuple[BuildPaths, Path, Path | None, Path]:
    check_python_environment(python_exe, label.upper())
    build_paths = get_build_paths(label)
    build_bundle(python_exe, build_paths, spec_file_for(label if label == "lite" else config.build_type))
    final_root = resolve_final_root(config.build_type, config.copy_dir)
    final_bundle, zip_path, installer_script_path = publish_standard_build(
        label=label,
        build_paths=build_paths,
        config=config,
        final_root=final_root,
    )
    return build_paths, final_bundle, zip_path, installer_script_path


def run_cpu_or_cuda_build(config: BuildConfig) -> None:
    bootstrap_python = Path(sys.executable)
    if config.build_type == "cpu":
        build_python = ensure_cpu_environment(bootstrap_python)
    else:
        build_python = ensure_cuda_environment(bootstrap_python)

    ensure_models(build_python)
    clean_build_outputs()

    _, final_bundle, zip_path, installer_script_path = build_single_target(config, config.build_type, build_python)
    logger.info("[========================================]")
    logger.info("构建完成")
    logger.info("[========================================]")
    logger.info("可执行文件: %s", final_bundle / f"{APP_NAME}.exe")
    logger.info("压缩文件: %s", zip_path if zip_path else "(已跳过)")
    logger.info("安装包脚本: %s", installer_script_path)


def run_lite_build(config: BuildConfig) -> None:
    bootstrap_python = Path(sys.executable)
    build_python = ensure_cpu_environment(bootstrap_python)

    clean_build_outputs()
    _, final_bundle, zip_path, installer_script_path = build_single_target(config, "lite", build_python)
    logger.info("[========================================]")
    logger.info("Lite 构建完成")
    logger.info("[========================================]")
    logger.info("可执行文件: %s", final_bundle / f"{APP_NAME}.exe")
    logger.info("压缩文件: %s", zip_path if zip_path else "(已跳过)")
    logger.info("安装包脚本: %s", installer_script_path)


def run_cuda_patch_build(config: BuildConfig) -> None:
    bootstrap_python = Path(sys.executable)
    cpu_python = ensure_cpu_environment(bootstrap_python)
    ensure_models(cpu_python)
    clean_build_outputs()

    cpu_paths, cpu_bundle, cpu_zip, cpu_installer_script = build_single_target(config, "cpu", cpu_python)

    cuda_python = ensure_cuda_environment(bootstrap_python)
    cuda_paths = get_build_paths("cuda")
    build_bundle(cuda_python, cuda_paths, spec_file_for("cuda"))

    patch_dir = prepare_patch_directory(cpu_paths.bundle_dir, cuda_paths.bundle_dir, config)
    patch_installer_script = prepare_patch_installer_staging(patch_dir, config)
    if not config.no_zip:
        patch_zip = (config.copy_dir or DEFAULT_PATCH_OUTPUT_ROOT) / archive_name_for(
            "cuda_patch",
            config.app_version,
            config.commit_hash,
        )
        create_zip_archive(patch_dir, patch_zip)
        log_verbose("[成功] 已创建 CUDA 补丁 ZIP 压缩包: %s", patch_zip)
    else:
        patch_zip = None
        log_verbose("[信息] 跳过 CUDA 补丁 ZIP 压缩包创建 (--no-zip)")

    log_step("步骤 7: 清理 CUDA 中间产物")
    remove_path(cuda_paths.work_dir)
    remove_path(cuda_paths.dist_dir)
    log_verbose("[成功] 已清理 CUDA 中间目录")

    logger.info("[========================================]")
    logger.info("CUDA Patch 构建完成")
    logger.info("[========================================]")
    logger.info("CPU 可执行文件: %s", cpu_bundle / f"{APP_NAME}.exe")
    logger.info("CPU 压缩文件: %s", cpu_zip if cpu_zip else "(已跳过)")
    logger.info("CPU 安装包脚本: %s", cpu_installer_script)
    logger.info("补丁目录: %s", patch_dir)
    logger.info("补丁压缩包: %s", patch_zip if patch_zip else "(已跳过)")
    logger.info("补丁安装包脚本: %s", patch_installer_script)


def create_config(args: argparse.Namespace) -> BuildConfig:
    """根据命令行参数创建构建配置。版本号自动添加 'v' 前缀。

    Create build config from CLI arguments. Version is auto-prefixed with 'v'.

    参数 / Parameters:
        args (argparse.Namespace): 解析后的命令行参数

    返回 / Return:
        BuildConfig: 构建配置对象
    """
    raw_version = args.version or read_app_version()
    return BuildConfig(
        build_type=args.build_type,
        copy_dir=Path(args.copy_dir).resolve() if args.copy_dir else None,
        no_zip=args.no_zip,
        debug=args.debug,
        app_version=normalize_version(raw_version),
        commit_hash=get_commit_hash(),
    )


def main() -> None:
    args = parse_args()
    configure_logging(args.debug)

    config = create_config(args)
    ensure_spec_file(config.build_type)
    ensure_inno_templates()

    release_tag = os.environ.get("RELEASE_TAG", "")
    release_channel = "nightly" if release_tag and "-rc" in release_tag.lower() else "official"
    backup_path = inject_build_info(config.commit_hash, release_channel)
    try:
        if config.build_type == "cuda-patch":
            run_cuda_patch_build(config)
        elif config.build_type == "lite":
            run_lite_build(config)
        else:
            run_cpu_or_cuda_build(config)
    finally:
        restore_build_info(backup_path)


if __name__ == "__main__":
    main()
