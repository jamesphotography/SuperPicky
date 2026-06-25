#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SuperPicky macOS 构建脚本 / SuperPicky macOS build script.

仅支持 full 构建，并可选执行 Developer ID 签名。
Builds the full macOS bundle with optional Developer ID signing.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from packaging.requirements import Requirement


ROOT_DIR = Path(__file__).resolve().parent
APP_NAME = "SuperPicky"
BUILD_INFO_FILE = ROOT_DIR / "core" / "build_info.py"
DOWNLOAD_MODELS_SCRIPT = ROOT_DIR / "scripts" / "download_models.py"
FULL_SPEC_FILE = ROOT_DIR / "SuperPicky_full.spec"
REQUIREMENTS_MAC_FILE = ROOT_DIR / "requirements_mac.txt"
ENTITLEMENTS_FILE = ROOT_DIR / "entitlements.plist"
DMG_README_FILE = ROOT_DIR / "resources" / "DMG_README.txt"
LICENSE_FILE = ROOT_DIR / "LICENSE"

# PKG 安装器相关常量与模板目录 / PKG installer constants and template dir.
# pkg 路径在提供 Developer ID Installer 证书（--installer-p12）时启用，产出
# 「pkg 装进 DMG」的安装器（自动安装 Lightroom 插件 / 预设 / 权限），否则
# 回退到旧的「.app 拖拽进 Applications」DMG。
BUNDLE_ID = "com.jamesphotography.superpicky"
PACKAGING_MAC_DIR = ROOT_DIR / "packaging" / "mac"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildPaths:
    """
    构建路径集合 / Build path collection.
    """

    label: str
    work_dir: Path
    dist_dir: Path
    app_dir: Path
    dmg_path: Path


@dataclass(frozen=True)
class BuildConfig:
    """
    构建配置 / Build configuration.
    """

    build_type: str
    arch: str
    copy_dir: Path | None
    debug: bool
    app_version: str
    commit_hash: str
    sign_p12: Path | None
    sign_p12_password_env: str
    sign_identity: str | None
    # Developer ID Installer 证书（productsign 签 .pkg 用，和签 .app 的
    # Developer ID Application 是两张不同证书）。提供时启用 pkg 安装器路径。
    installer_p12: Path | None
    installer_p12_password_env: str
    installer_identity: str | None
    release_channel: str
    notarize: bool
    apple_id: str | None
    apple_password_env: str
    team_id: str | None
    notary_keychain_profile: str | None


@dataclass
class SigningContext:
    """
    签名上下文 / Signing context.
    """

    keychain_path: Path
    keychain_password: str
    imported_p12_path: Path
    identity: str
    # Developer ID Installer identity（仅在导入了 installer .p12 时存在）。
    # 供 productsign 给 .pkg 签名；为 None 时跳过 pkg 签名。
    installer_identity: str | None = None


def configure_logging(debug: bool) -> None:
    """
    配置 UTF-8 日志输出 / Configure UTF-8 logging output.
    """

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")  # pyright: ignore[reportAttributeAccessIssue]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="strict")  # pyright: ignore[reportAttributeAccessIssue]

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(handler)


def log_step(title: str) -> None:
    """
    记录步骤标题 / Log a step title.
    """

    logger.info("[========================================]")
    logger.info(title)
    logger.info("[========================================]")


def log_verbose(message: str, *args) -> None:
    """
    仅在调试模式输出详细日志 / Emit verbose logs only in debug mode.
    """

    logger.debug(message, *args)


def detect_host_arch() -> str:
    """
    规范化当前主机架构 / Normalize the current host architecture.
    """

    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def optional_text(value: str | None) -> str | None:
    """
    规范化可选字符串 / Normalize optional text values.
    """

    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数 / Parse command-line arguments.
    """

    parser = argparse.ArgumentParser(description="SuperPicky macOS 构建脚本")
    parser.add_argument("--build-type", choices=["full"], required=True, help="构建类型：仅支持 full")
    parser.add_argument(
        "--arch",
        choices=["arm64", "x86_64"],
        default=detect_host_arch(),
        help="目标架构，默认使用当前主机架构",
    )
    parser.add_argument("--version", help="覆盖构建版本号，例如 4.2.5")
    parser.add_argument("--copy-dir", help="复制最终产物的目标目录")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    parser.add_argument("--sign-p12", help="Developer ID 证书 .p12 文件路径")
    parser.add_argument(
        "--sign-p12-password-env",
        default="MACOS_CERTIFICATE_PWD",
        help="读取 .p12 密码的环境变量名（默认: MACOS_CERTIFICATE_PWD）",
    )
    parser.add_argument("--sign-identity", help="可选，显式指定 Developer ID Application identity")
    parser.add_argument(
        "--installer-p12",
        help="Developer ID Installer 证书 .p12 路径；提供后启用 pkg 安装器路径",
    )
    parser.add_argument(
        "--installer-p12-password-env",
        default="MACOS_INSTALLER_CERTIFICATE_PWD",
        help="读取 Installer .p12 密码的环境变量名（默认: MACOS_INSTALLER_CERTIFICATE_PWD）",
    )
    parser.add_argument(
        "--installer-identity",
        help="可选，显式指定 Developer ID Installer identity（productsign 用）",
    )
    parser.add_argument("--notarize", action="store_true", help="提交 Apple 公证并自动 staple DMG")
    parser.add_argument("--apple-id", help="Apple notarization 使用的 Apple ID")
    parser.add_argument("--team-id", help="Apple notarization 使用的 Team ID")
    parser.add_argument(
        "--apple-password-env",
        default="APPLE_APP_PASSWORD",
        help="读取 notarization 密码的环境变量名（默认: APPLE_APP_PASSWORD）",
    )
    parser.add_argument("--notary-keychain-profile", help="可选，使用 notarytool keychain profile 进行认证")
    return parser.parse_args()


def run_command(
    command: Sequence[str],
    *,
    cwd: Path = ROOT_DIR,
    check: bool = True,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
    label: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    运行外部命令 / Run an external command.

    timeout: 可选，单位秒。超时时 subprocess 会杀掉子进程并抛 TimeoutExpired，
             供调用方实现「卡死即超时重试」（如 productsign 偶发挂在时间戳网络调用）。
    timeout: optional seconds; on expiry the child is killed and TimeoutExpired is
             raised so callers can retry on hangs (e.g. productsign stalling on the
             Apple timestamp service).
    """

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("执行命令: %s", " ".join(command))

    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        capture_output=capture_output,
        env=env,
        timeout=timeout,
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
    """
    删除文件或目录 / Remove a file or directory.
    """

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    """
    复制目录 / Copy a directory tree.
    """

    if not src.exists():
        raise FileNotFoundError(f"复制源目录不存在: {src}")
    remove_path(dst)
    shutil.copytree(src, dst, symlinks=True)


def copy_file(src: Path, dst: Path) -> None:
    """
    复制文件 / Copy a file.
    """

    if not src.exists():
        raise FileNotFoundError(f"复制源文件不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def read_app_version() -> str:
    """
    从 constants.py 读取版本号 / Read version from constants.py.
    """

    content = (ROOT_DIR / "constants.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*["\']([0-9A-Za-z._-]+)["\']', content)
    return match.group(1) if match else "0.0.0"


def get_commit_hash() -> str:
    """
    获取当前提交哈希 / Get the current commit hash.
    """

    try:
        result = run_command(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            label="获取提交哈希",
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        content = BUILD_INFO_FILE.read_text(encoding="utf-8")
        match = re.search(r'COMMIT_HASH\s*=\s*"([^"]*)"', content)
        return match.group(1) if match else "unknown"


def parse_release_channel() -> str:
    """
    根据 RELEASE_TAG 判断发布渠道 / Infer release channel from RELEASE_TAG.
    """

    release_tag = os.environ.get("RELEASE_TAG", "")
    if release_tag and "-rc" in release_tag.lower():
        return "nightly"
    return "official"


def inject_build_info(commit_hash: str, release_channel: str) -> Path | None:
    """
    注入构建信息并返回备份路径 / Inject build info and return the backup path.
    """

    log_step("步骤 1: 注入构建元数据")
    if not BUILD_INFO_FILE.exists():
        logger.warning("未找到 build_info.py，跳过注入")
        return None

    backup_path = BUILD_INFO_FILE.with_suffix(".py.backup")
    shutil.copy2(BUILD_INFO_FILE, backup_path)

    content = BUILD_INFO_FILE.read_text(encoding="utf-8-sig")
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
    """
    恢复构建信息文件 / Restore the build info file.
    """

    if backup_path and backup_path.exists():
        shutil.move(str(backup_path), str(BUILD_INFO_FILE))


def spec_file_for(build_type: str) -> Path:
    """
    返回构建类型对应的 spec 文件 / Return the spec file for a build type.
    """

    return FULL_SPEC_FILE


def app_name_for(build_type: str) -> str:
    """
    返回构建类型对应的应用名 / Return the app name for a build type.
    """

    return APP_NAME


def artifact_name_for(build_type: str) -> str:
    """
    返回发布产物名称前缀 / Return the artifact name prefix for releases.
    """

    return APP_NAME


def display_name_for(build_type: str) -> str:
    """
    返回面向用户的展示名称 / Return the user-facing display name.
    """

    return APP_NAME


def get_build_paths(build_type: str, arch: str, app_version: str, commit_hash: str) -> BuildPaths:
    """
    生成构建路径 / Build output paths.
    """

    label = f"{build_type}_{arch}"
    app_name = app_name_for(build_type)
    artifact_name = artifact_name_for(build_type)
    dist_dir = ROOT_DIR / f"dist_{label}"
    dmg_name = f"{artifact_name}_v{app_version}_{arch}_{commit_hash}.dmg"
    return BuildPaths(
        label=label,
        work_dir=ROOT_DIR / f"build_dist_{label}",
        dist_dir=dist_dir,
        app_dir=dist_dir / f"{app_name}.app",
        dmg_path=dist_dir / dmg_name,
    )


def ensure_macos_host() -> None:
    """
    确保当前系统为 macOS / Ensure the current host is macOS.
    """

    if sys.platform != "darwin":
        raise RuntimeError("build_release_mac.py 只能在 macOS 上运行")


def ensure_arch_matches(target_arch: str) -> None:
    """
    确保目标架构与当前机器匹配 / Ensure target architecture matches the host.
    """

    normalized = detect_host_arch()
    if normalized != target_arch:
        raise RuntimeError(
            f"当前机器架构为 {normalized}，不能直接构建 {target_arch}。"
            "请在对应架构的 macOS 环境中运行此脚本。"
        )


def _iter_requirement_lines(requirements_file: Path) -> Iterable[tuple[Path, str]]:
    """
    递归展开 requirements 文件 / Recursively expand requirements files.
    """

    for raw_line in requirements_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            nested_path = (requirements_file.parent / line[3:].strip()).resolve()
            yield from _iter_requirement_lines(nested_path)
            continue
        if line.startswith("--requirement "):
            nested_path = (requirements_file.parent / line.split(None, 1)[1].strip()).resolve()
            yield from _iter_requirement_lines(nested_path)
            continue
        if line.startswith("-"):
            logger.debug("跳过未处理的 requirements 条目: %s", line)
            continue
        yield requirements_file, line


def validate_python_environment() -> None:
    """
    检查当前 Python 环境是否满足 requirements_mac.txt / Validate the current Python environment.
    """

    log_step("步骤 2: 检查 Python 构建环境")

    missing_packages: list[str] = []
    version_conflicts: list[str] = []

    for source_file, requirement_text in _iter_requirement_lines(REQUIREMENTS_MAC_FILE):
        requirement = Requirement(requirement_text)
        try:
            installed_version = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            missing_packages.append(f"{requirement.name} ({source_file.name})")
            continue
        if requirement.specifier and installed_version not in requirement.specifier:
            version_conflicts.append(
                f"{requirement.name}=={installed_version} 不满足 {requirement.specifier} ({source_file.name})"
            )

    if missing_packages or version_conflicts:
        details = "\n".join([*missing_packages, *version_conflicts])
        raise RuntimeError(
            "当前 Python 环境未满足 requirements_mac.txt。\n"
            "请先执行 `python -m pip install -r requirements_mac.txt`。\n"
            f"{details}"
        )

    run_command([sys.executable, "-c", "import PyInstaller; print(PyInstaller.__version__)"], label="PyInstaller 检查")
    log_verbose("[成功] 当前 Python 环境满足 macOS 构建要求")


def load_required_models() -> list[dict[str, str]]:
    """
    从 download_models.py 解析模型清单 / Parse the required model list from download_models.py.
    """

    fallback = [
        {"filename": "model20240824.pth", "dest_dir": "models"},
        {"filename": "superFlier_efficientnet.pth", "dest_dir": "models"},
        {"filename": "cub200_keypoint_resnet50_slim.pth", "dest_dir": "models"},
        {"filename": "avonet.db", "dest_dir": "birdid/data"},
        {"filename": "cfanet_iaa_ava_res50-3cd62bb3.pth", "dest_dir": "models"},
        {"filename": "yolo11l-seg.pt", "dest_dir": "models"},
        {"filename": "svdlut.pth", "dest_dir": "models"},
        {"filename": "scunet_color_real.pth", "dest_dir": "models"},
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
        return [{"filename": item["filename"], "dest_dir": item["dest_dir"]} for item in models]
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        logger.warning("无法解析 download_models.py，使用默认模型列表: %s", exc)
        return fallback


REQUIRED_MODELS = load_required_models()


def ensure_models() -> None:
    """
    检查 full 构建所需模型并在缺失时下载 / Ensure required models for the full build.
    """

    log_step("步骤 3: 检查并下载模型文件")
    missing_paths = [
        ROOT_DIR / model["dest_dir"] / model["filename"]
        for model in REQUIRED_MODELS
        if not (ROOT_DIR / model["dest_dir"] / model["filename"]).exists()
    ]

    if not missing_paths:
        log_verbose("[成功] 所有模型文件已就绪")
        return

    logger.warning("检测到 %d 个缺失模型，开始下载", len(missing_paths))
    run_command([sys.executable, str(DOWNLOAD_MODELS_SCRIPT)], label="模型下载")

    remaining = [path for path in missing_paths if not path.exists()]
    if remaining:
        missing_text = "\n".join(str(path) for path in remaining)
        raise RuntimeError(f"模型下载后仍有缺失:\n{missing_text}")
    log_verbose("[成功] 所有模型文件已就绪")


def clean_build_outputs(paths: BuildPaths) -> None:
    """
    清理构建目录 / Clean build outputs.
    """

    log_step("步骤 4: 清理旧的构建目录")
    remove_path(paths.work_dir)
    remove_path(paths.dist_dir)
    log_verbose("[成功] 已清理 %s 和 %s", paths.work_dir, paths.dist_dir)


def build_environment(config: BuildConfig) -> dict[str, str]:
    """
    生成 PyInstaller 环境变量 / Build PyInstaller environment variables.
    """

    env = os.environ.copy()
    env["SUPERPICKY_TARGET_ARCH"] = config.arch
    env["SUPERPICKY_APP_VERSION"] = config.app_version
    env["SUPERPICKY_CODESIGN_IDENTITY"] = ""
    env["SUPERPICKY_ENTITLEMENTS_FILE"] = ""
    return env


def build_bundle(config: BuildConfig, paths: BuildPaths) -> None:
    """
    执行 PyInstaller 构建 / Run the PyInstaller build.
    """

    log_step("步骤 5: 执行 PyInstaller 构建")
    spec_file = spec_file_for(config.build_type)
    pyinstaller_command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_file),
        "--clean",
        "--noconfirm",
        f"--workpath={paths.work_dir}",
        f"--distpath={paths.dist_dir}",
    ]
    logger.info("启动 PyInstaller 构建：开始")
    logger.info("PyInstaller 参数：%s", " ".join(str(item) for item in pyinstaller_command[2:]))
    run_command(
        pyinstaller_command,
        capture_output=not logger.isEnabledFor(logging.DEBUG),
        env=build_environment(config),
        label="PyInstaller 构建",
    )

    if not paths.app_dir.exists():
        raise FileNotFoundError(f"构建完成后未找到 .app: {paths.app_dir}")
    logger.info("PyInstaller 构建成功！")
    logger.info("构建产物位置：%s", paths.app_dir)
    log_verbose("[成功] 已完成 %s 构建: %s", config.build_type, paths.app_dir)


def organize_app_bundle_resources(app_dir: Path) -> None:
    """
    将资源移至 .app 的 Resources 目录 / Move resources into the app bundle Resources directory.
    """

    log_step("步骤 6: 整理 .app 资源目录")
    macos_dir = app_dir / "Contents" / "MacOS"
    resources_dir = app_dir / "Contents" / "Resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    for resource_name in ("SuperBirdIDPlugin.lrplugin", "en.lproj", "zh-Hans.lproj"):
        source_path = macos_dir / resource_name
        destination_path = resources_dir / resource_name
        if source_path.exists():
            remove_path(destination_path)
            shutil.move(str(source_path), str(destination_path))
            log_verbose("[成功] 已移动资源到 Resources: %s", resource_name)


def create_dmg(config: BuildConfig, paths: BuildPaths) -> None:
    """
    生成 DMG 安装镜像 / Create a DMG installer image.
    """

    log_step("步骤 7: 生成 DMG")
    staging_dir = paths.dist_dir / "dmg_staging"
    remove_path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    staged_app = staging_dir / paths.app_dir.name
    copy_tree(paths.app_dir, staged_app)

    plugin_dir = paths.app_dir / "Contents" / "Resources" / "SuperBirdIDPlugin.lrplugin"
    if plugin_dir.exists():
        copy_tree(plugin_dir, staging_dir / plugin_dir.name)

    if DMG_README_FILE.exists():
        copy_file(DMG_README_FILE, staging_dir / "README.txt")

    applications_link = staging_dir / "Applications"
    if not applications_link.exists():
        os.symlink("/Applications", applications_link)

    paths.dmg_path.parent.mkdir(parents=True, exist_ok=True)
    remove_path(paths.dmg_path)
    run_command(
        [
            "hdiutil",
            "create",
            "-volname",
            f"{display_name_for(config.build_type)} {config.app_version}",
            "-srcfolder",
            str(staging_dir),
            "-ov",
            "-format",
            "UDZO",
            str(paths.dmg_path),
        ],
        label="生成 DMG",
    )

    remove_path(staging_dir)
    log_verbose("[成功] 已生成 DMG: %s", paths.dmg_path)


def _render_template(template_path: Path, replacements: dict[str, str]) -> str:
    """
    读取模板文件并做 __X__ 占位符替换 / Read a template and apply __X__ substitutions.
    """

    text = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def build_pkg(config: BuildConfig, paths: BuildPaths, signing_context: SigningContext | None) -> Path:
    """
    构建并签名 .pkg 安装器 / Build and sign the .pkg installer.

    流程：ditto 已签名 .app → pkgbuild 组件包（带 postinstall + 禁 relocation）→
    productbuild Distribution 包 → productsign（Developer ID Installer）→ 校验签名。
    postinstall 会在安装时自动装 Lightroom 插件 / 导出预设、设权限、清隔离标记。

    返回签名后的 .pkg 路径；未提供 Installer 证书时返回未签名 pkg（仅本地调试用）。

    Build the Distribution .pkg from the already-signed .app, then sign it with
    the Developer ID Installer identity. Returns the resulting .pkg path.
    """

    log_step("步骤 7a: 构建 PKG 安装器")
    version = config.app_version
    dist_dir = paths.dist_dir

    # 1) pkg_root/Applications/SuperPicky.app（ditto 保留签名与符号链接）
    pkg_root = dist_dir / "pkg_root"
    remove_path(pkg_root)
    (pkg_root / "Applications").mkdir(parents=True, exist_ok=True)
    staged_app = pkg_root / "Applications" / paths.app_dir.name
    run_command(["ditto", str(paths.app_dir), str(staged_app)], label="ditto .app 到 pkg_root")

    # 2) postinstall 脚本（渲染版本号 + 可执行）
    scripts_dir = dist_dir / "pkg_scripts"
    remove_path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    postinstall_path = scripts_dir / "postinstall"
    postinstall_path.write_text(
        _render_template(PACKAGING_MAC_DIR / "postinstall", {"__VERSION__": version}),
        encoding="utf-8",
    )
    postinstall_path.chmod(0o755)

    # 3) 组件 plist（静态，禁 relocation 防止装错位置）
    component_plist = dist_dir / "pkg_component.plist"
    copy_file(PACKAGING_MAC_DIR / "component.plist", component_plist)

    # 4) pkgbuild 组件包
    component_pkg = dist_dir / f"{APP_NAME}-component.pkg"
    remove_path(component_pkg)
    run_command(
        [
            "pkgbuild",
            "--root", str(pkg_root),
            "--scripts", str(scripts_dir),
            "--component-plist", str(component_plist),
            "--identifier", BUNDLE_ID,
            "--version", version,
            "--install-location", "/",
            str(component_pkg),
        ],
        label="pkgbuild 组件包",
    )

    # 5) Distribution 资源（welcome/conclusion/LICENSE）+ distribution.xml
    resources_dir = dist_dir / "pkg_resources"
    remove_path(resources_dir)
    resources_dir.mkdir(parents=True, exist_ok=True)
    (resources_dir / "welcome.html").write_text(
        _render_template(PACKAGING_MAC_DIR / "welcome.html.tmpl", {"__VERSION__": version}),
        encoding="utf-8",
    )
    (resources_dir / "conclusion.html").write_text(
        _render_template(PACKAGING_MAC_DIR / "conclusion.html.tmpl", {"__VERSION__": version}),
        encoding="utf-8",
    )
    if LICENSE_FILE.exists():
        copy_file(LICENSE_FILE, resources_dir / "LICENSE")

    distribution_xml = dist_dir / "distribution.xml"
    distribution_xml.write_text(
        _render_template(
            PACKAGING_MAC_DIR / "distribution.xml.tmpl",
            {
                "__BUNDLE_ID__": BUNDLE_ID,
                "__VERSION__": version,
                "__COMPONENT_PKG__": component_pkg.name,
            },
        ),
        encoding="utf-8",
    )

    # 6) productbuild 最终 pkg（与 dmg 同名前缀，便于识别架构/commit）
    pkg_path = dist_dir / f"{paths.dmg_path.stem}_Installer.pkg"
    remove_path(pkg_path)
    run_command(
        [
            "productbuild",
            "--distribution", str(distribution_xml),
            "--resources", str(resources_dir),
            "--package-path", str(dist_dir),
            str(pkg_path),
        ],
        label="productbuild Distribution 包",
    )

    # 7) 签名 PKG（Developer ID Installer）+ 校验
    # 优先用 rcodesign：直接用 .p12 原地签 .pkg，完全不碰 macOS 钥匙串，根治 CI 无头
    # 环境下 productsign 取 Installer 私钥被钥匙串授权阻塞的问题（曾导致 CI 干等数小时；
    # 同配方本机可签、CI 复现不出，属 runner 环境特有）。未装 rcodesign 时回退 productsign。
    # Prefer rcodesign (signs the .pkg directly from the .p12, no keychain) to avoid the
    # headless-CI productsign keychain-prompt hang; fall back to productsign locally.
    installer_identity = signing_context.installer_identity if signing_context else None
    if installer_identity:
        rcodesign_bin = shutil.which("rcodesign")
        installer_p12 = config.installer_p12
        installer_pw = (os.environ.get(config.installer_p12_password_env, "")
                        if config.installer_p12_password_env else "")
        if rcodesign_bin and installer_p12 and installer_p12.exists() and installer_pw:
            log_step("步骤 7a-sign: rcodesign 用 .p12 直签 PKG（绕开钥匙串）")
            pw_file = dist_dir / ".installer_p12_pw"
            try:
                pw_file.write_text(installer_pw, encoding="utf-8")
                run_command(
                    [rcodesign_bin, "sign", "--p12-file", str(installer_p12),
                     "--p12-password-file", str(pw_file), str(pkg_path)],
                    label="rcodesign 签名 PKG", timeout=300,
                )
            finally:
                remove_path(pw_file)
            run_command(["pkgutil", "--check-signature", str(pkg_path)], label="校验 PKG 签名")
            logger.info("[成功] PKG 已用 rcodesign 签名: %s", pkg_path)
        else:
            # 回退：productsign 走 keychain（本地构建、未装 rcodesign 时）
            # Fallback: productsign via keychain (local builds without rcodesign).
            signed_pkg = dist_dir / f"{pkg_path.stem}-signed.pkg"
            remove_path(signed_pkg)
            sign_command = ["productsign", "--sign", installer_identity]
            if signing_context is not None and signing_context.keychain_path is not None:
                sign_command.extend(["--keychain", str(signing_context.keychain_path)])
            sign_command.extend([str(pkg_path), str(signed_pkg)])
            run_command(sign_command, label="productsign 签名 PKG", timeout=300)
            shutil.move(str(signed_pkg), str(pkg_path))
            run_command(["pkgutil", "--check-signature", str(pkg_path)], label="校验 PKG 签名")
            logger.info("[成功] PKG 已用 productsign 签名: %s", pkg_path)
    else:
        log_verbose("[信息] 未提供 Installer 证书，PKG 未签名: %s", pkg_path)

    # 清理中间产物（保留最终 pkg）
    remove_path(pkg_root)
    remove_path(scripts_dir)
    remove_path(resources_dir)
    remove_path(component_pkg)
    remove_path(component_plist)
    remove_path(distribution_xml)
    return pkg_path


def create_pkg_dmg(config: BuildConfig, paths: BuildPaths, pkg_path: Path) -> None:
    """
    把 .pkg 装进 DMG（仿本地脚本）/ Wrap the signed .pkg into a DMG.

    DMG 内含：pkg 安装器 + Lightroom 插件副本（供手动安装）+ 中英文说明（总说明 /
    插件手动安装 / 在线教程快捷方式）。不含 .app 与 Applications 软链——应用由 pkg 安装。

    Mirror the local packaging: the DMG carries the installer pkg plus a plugin
    copy and bilingual instructions for manual install.
    """

    log_step("步骤 7b: 生成 PKG-in-DMG")
    version = config.app_version
    staging_dir = paths.dist_dir / "dmg_staging"
    remove_path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    copy_file(pkg_path, staging_dir / pkg_path.name)

    plugin_dir = paths.app_dir / "Contents" / "Resources" / "SuperBirdIDPlugin.lrplugin"
    if plugin_dir.exists():
        copy_tree(plugin_dir, staging_dir / plugin_dir.name)

    (staging_dir / "README 安装说明.txt").write_text(
        _render_template(
            PACKAGING_MAC_DIR / "dmg_readme.txt.tmpl",
            {"__VERSION__": version, "__PKG_NAME__": pkg_path.name},
        ),
        encoding="utf-8",
    )
    (staging_dir / "Lightroom Plugin Manual Installation 插件手动安装.txt").write_text(
        _render_template(PACKAGING_MAC_DIR / "dmg_plugin_manual.txt.tmpl", {"__VERSION__": version}),
        encoding="utf-8",
    )
    copy_file(
        PACKAGING_MAC_DIR / "dmg_tutorial.webloc",
        staging_dir / "Online Tutorial 在线教程.webloc",
    )

    paths.dmg_path.parent.mkdir(parents=True, exist_ok=True)
    remove_path(paths.dmg_path)
    run_command(
        [
            "hdiutil",
            "create",
            "-volname",
            f"{display_name_for(config.build_type)} {version}",
            "-srcfolder",
            str(staging_dir),
            "-ov",
            "-format",
            "UDZO",
            str(paths.dmg_path),
        ],
        label="生成 PKG-in-DMG",
    )
    remove_path(staging_dir)
    log_verbose("[成功] 已生成 PKG-in-DMG: %s", paths.dmg_path)


def prepare_signing(config: BuildConfig) -> SigningContext | None:
    """
    如果提供 .p12，则导入临时 keychain；若仅提供 sign_identity，则直接使用系统 keychain。
    If a .p12 is provided, import it into a temporary keychain.
    If only sign_identity is provided, use the certificate already in the system keychain.
    """

    if config.sign_p12 is None:
        # 没有 .p12 但有 sign_identity：使用系统 keychain 中已有的证书直接签名。
        # No .p12 but sign_identity given: use the certificate already in the system keychain.
        if config.sign_identity is None:
            return None
        log_step("步骤 8: 使用系统 keychain 中的证书")
        system_keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
        log_verbose("[信息] 使用系统 keychain identity: %s", config.sign_identity)
        return SigningContext(
            keychain_path=system_keychain,
            keychain_password="",
            imported_p12_path=system_keychain,
            identity=config.sign_identity,
            installer_identity=config.installer_identity,
        )

    log_step("步骤 8: 导入签名证书")
    if not config.sign_p12.exists():
        raise FileNotFoundError(f"未找到签名证书文件: {config.sign_p12}")

    password = os.environ.get(config.sign_p12_password_env, "")
    if not password:
        raise RuntimeError(f"环境变量 {config.sign_p12_password_env} 未设置，无法导入 .p12")

    temp_dir = Path(tempfile.mkdtemp(prefix="superpicky_sign_", dir=str(ROOT_DIR)))
    imported_p12_path = temp_dir / config.sign_p12.name
    shutil.copy2(config.sign_p12, imported_p12_path)

    keychain_path = temp_dir / "build.keychain-db"
    keychain_password = secrets.token_hex(16)

    run_command(["security", "create-keychain", "-p", keychain_password, str(keychain_path)], label="创建临时 keychain")
    run_command(["security", "set-keychain-settings", "-lut", "21600", str(keychain_path)], label="配置 keychain")

    # 把临时 keychain 加入 user 域 search list，否则 codesign 在签名时找不到 identity。
    # macOS Security framework 不会主动搜索任意 keychain，`--keychain` 标志只限制查询范围、
    # 不会把目标 keychain 自动加入搜索列表，结果是 codesign 报 "The specified item could not
    # be found in the keychain." 即使 `find-identity` 在同一 keychain 里能找到证书。
    # Add the temp keychain to the user-domain search list so codesign can resolve the
    # signing identity. The macOS Security framework does not search arbitrary keychains
    # implicitly; the `--keychain` flag only narrows the lookup, it does not add the
    # keychain to the search list. Without this step codesign fails with "The specified
    # item could not be found in the keychain." even though `find-identity` can locate
    # the cert when given the keychain path explicitly.
    existing_keychains_output = run_command(
        ["security", "list-keychains", "-d", "user"],
        capture_output=True,
        label="读取 keychain 搜索列表",
    ).stdout
    preexisting_keychains = re.findall(r'"([^"]+)"', existing_keychains_output)
    run_command(
        ["security", "list-keychains", "-d", "user", "-s", str(keychain_path), *preexisting_keychains],
        label="将临时 keychain 加入搜索列表",
    )

    run_command(["security", "unlock-keychain", "-p", keychain_password, str(keychain_path)], label="解锁 keychain")
    run_command(
        [
            "security",
            "import",
            str(imported_p12_path),
            "-k",
            str(keychain_path),
            "-P",
            password,
            "-T",
            "/usr/bin/codesign",
        ],
        label="导入 .p12",
    )

    # 可选：导入 Developer ID Installer 证书（productsign 签 .pkg 用）。
    # 必须在 set-key-partition-list 之前导入，否则 installer 私钥不会被授予
    # 非交互访问权限，productsign 在 CI 上会卡在密码弹窗 / 失败。
    # Import the Developer ID Installer cert (for productsign) BEFORE
    # set-key-partition-list so its private key also gets non-interactive
    # access; otherwise productsign hangs on a password prompt in CI.
    installer_identity: str | None = None
    if config.installer_p12 is not None:
        if not config.installer_p12.exists():
            raise FileNotFoundError(f"未找到 Installer 证书文件: {config.installer_p12}")
        installer_password = os.environ.get(config.installer_p12_password_env, "")
        if not installer_password:
            raise RuntimeError(
                f"环境变量 {config.installer_p12_password_env} 未设置，无法导入 Installer .p12"
            )
        imported_installer_p12 = temp_dir / f"installer_{config.installer_p12.name}"
        shutil.copy2(config.installer_p12, imported_installer_p12)
        run_command(
            [
                "security",
                "import",
                str(imported_installer_p12),
                "-k",
                str(keychain_path),
                "-P",
                installer_password,
                "-T",
                "/usr/bin/productsign",
            ],
            label="导入 Installer .p12",
        )

    run_command(
        [
            "security",
            "set-key-partition-list",
            "-S",
            "apple-tool:,apple:",
            "-s",
            "-k",
            keychain_password,
            str(keychain_path),
        ],
        label="配置 keychain 访问权限",
    )

    identity = config.sign_identity or discover_signing_identity(keychain_path)
    log_verbose("[成功] 已加载签名 identity: %s", identity)
    if config.installer_p12 is not None:
        installer_identity = config.installer_identity or discover_installer_identity(keychain_path)
        log_verbose("[成功] 已加载 Installer identity: %s", installer_identity)
    return SigningContext(
        keychain_path=keychain_path,
        keychain_password=keychain_password,
        imported_p12_path=imported_p12_path,
        identity=identity,
        installer_identity=installer_identity,
    )


def discover_signing_identity(keychain_path: Path) -> str:
    """
    从 keychain 中解析 Developer ID Application identity / Resolve Developer ID Application identity from keychain.
    """

    result = run_command(
        ["security", "find-identity", "-v", "-p", "codesigning", str(keychain_path)],
        capture_output=True,
        label="解析签名 identity",
    )
    pattern = re.compile(r'"(Developer ID Application:[^"]+)"')
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            return match.group(1)
    raise RuntimeError("未在 .p12 对应 keychain 中找到 Developer ID Application identity")


def discover_installer_identity(keychain_path: Path) -> str:
    """
    从 keychain 中解析 Developer ID Installer identity / Resolve the installer identity.

    注意：Installer 证书不属于 codesigning 策略，必须用不带 `-p codesigning`
    的 `find-identity -v` 才能枚举到。
    Installer certs are not codesigning identities, so we enumerate all valid
    identities (no `-p codesigning`) and match the Installer prefix.
    """

    result = run_command(
        ["security", "find-identity", "-v", str(keychain_path)],
        capture_output=True,
        label="解析 Installer identity",
    )
    pattern = re.compile(r'"(Developer ID Installer:[^"]+)"')
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            return match.group(1)
    raise RuntimeError("未在 .p12 对应 keychain 中找到 Developer ID Installer identity")


def iter_signable_files(contents_dir: Path) -> list[Path]:
    """
    枚举需要优先签名的文件 / Enumerate files that should be signed first.
    """

    signable: list[Path] = []
    for path in contents_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".dylib", ".so"} or os.access(path, os.X_OK):
            signable.append(path)
    signable.sort(key=lambda item: len(item.parts), reverse=True)
    return signable


def codesign_path(
    path: Path,
    identity: str,
    *,
    entitlements: Path | None = None,
    keychain_path: Path | None = None,
    use_runtime: bool = False,
) -> None:
    """
    对指定路径执行 codesign，遇到 Apple timestamp 服务暂时不可用时自动重试（最多 3 次）。
    Sign a path with codesign, retrying up to 3 times on Apple timestamp service failures.
    """

    command = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        command.append("--timestamp")
        if use_runtime:
            command.extend(["--options", "runtime"])
    if entitlements is not None:
        command.extend(["--entitlements", str(entitlements)])
    if keychain_path is not None:
        command.extend(["--keychain", str(keychain_path)])
    command.append(str(path))

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(list(command), cwd=str(ROOT_DIR), text=True, capture_output=True)
        if result.returncode == 0:
            return
        stderr_lower = (result.stderr or "").lower()
        # Apple timestamp 服务瞬时不可用（常见于本地构建高并发签名时）可重试。
        # Apple's timestamp service occasionally returns transient errors during local builds.
        is_timestamp_error = "timestamp service is not available" in stderr_lower
        if is_timestamp_error and attempt < max_attempts:
            wait_secs = 15 * attempt
            logger.warning(
                "[签名重试 %d/%d] Apple timestamp 服务暂时不可用，%ds 后重试: %s",
                attempt, max_attempts - 1, wait_secs, path.name,
            )
            time.sleep(wait_secs)
            continue
        if result.stdout:
            logger.error(result.stdout.strip())
        if result.stderr:
            logger.error(result.stderr.strip())
        raise RuntimeError(f"签名 {path.name}失败，返回码: {result.returncode}")


def sign_app_bundle(app_dir: Path, signing_context: SigningContext | None) -> None:
    """
    对 .app 执行签名并验证 / Sign and verify the app bundle.
    """

    log_step("步骤 9: 签名并验证 .app")
    identity = signing_context.identity if signing_context else "-"
    keychain_path = signing_context.keychain_path if signing_context else None
    entitlements = ENTITLEMENTS_FILE if signing_context and ENTITLEMENTS_FILE.exists() else None

    for nested_path in iter_signable_files(app_dir / "Contents"):
        codesign_path(nested_path, identity, keychain_path=keychain_path, use_runtime=True)

    codesign_path(app_dir, identity, entitlements=entitlements, keychain_path=keychain_path, use_runtime=True)
    verify_command = ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_dir)]
    run_command(verify_command, label="校验 .app 签名")
    log_verbose("[成功] .app 签名校验通过")


def sign_dmg(dmg_path: Path, signing_context: SigningContext | None) -> None:
    """
    如有证书则签名 DMG / Sign the DMG when a certificate is provided.
    """

    if signing_context is None:
        log_verbose("[信息] 未提供 .p12，跳过 DMG 签名")
        return

    log_step("步骤 10: 签名 DMG")
    codesign_path(dmg_path, signing_context.identity, keychain_path=signing_context.keychain_path, use_runtime=False)
    run_command(["codesign", "--verify", "--verbose=2", str(dmg_path)], label="校验 DMG 签名")
    log_verbose("[成功] DMG 签名校验通过")


def notary_auth_arguments(config: BuildConfig) -> list[str]:
    """
    构造 notarytool 认证参数 / Build notarytool authentication arguments.
    """

    if config.notary_keychain_profile:
        return ["--keychain-profile", config.notary_keychain_profile]

    if not config.apple_id:
        raise RuntimeError("启用 --notarize 时必须提供 Apple ID 或设置 APPLE_ID 环境变量")
    if not config.team_id:
        raise RuntimeError("启用 --notarize 时必须提供 Team ID 或设置 MACOS_TEAM_ID/TEAM_ID 环境变量")

    password = os.environ.get(config.apple_password_env, "").strip()
    if not password:
        raise RuntimeError(f"启用 --notarize 时必须设置环境变量 {config.apple_password_env}")

    return [
        "--apple-id",
        config.apple_id,
        "--password",
        password,
        "--team-id",
        config.team_id,
    ]


def notarize_dmg(dmg_path: Path, config: BuildConfig) -> None:
    """
    公证并装订 DMG / Notarize and staple the DMG.
    """

    if not config.notarize:
        log_verbose("[信息] 未启用 --notarize，跳过 Apple 公证")
        return

    log_step("步骤 11: Apple 公证并装订")
    auth_args = notary_auth_arguments(config)
    # --timeout 限制 --wait 的最长等待时长，避免 Apple 公证服务积压时 notarytool
    # 无限期挂起（曾观察到单次 submit 干等 2 小时仍不返回，最终被 job 6h 超时杀掉）。
    # --timeout caps how long --wait blocks, so a backed-up Apple notary service can't
    # hang notarytool indefinitely (observed a single submit waiting 2h+).
    submit_command = [
        "xcrun",
        "notarytool",
        "submit",
        str(dmg_path),
        *auth_args,
        "--wait",
        "--timeout",
        "30m",
        "--output-format",
        "json",
    ]
    # 公证提交可能因服务积压/网络在 --timeout 后失败；重试一次再放弃。
    # Submission may fail after --timeout due to backlog/network; retry once before giving up.
    max_attempts = 2
    result = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = run_command(
                submit_command, capture_output=True,
                label=f"Apple 公证（第 {attempt}/{max_attempts} 次）",
            )
            break
        except Exception as exc:  # noqa: BLE001 — 提交层失败统一重试
            log_verbose("[警告] 公证提交第 %d/%d 次失败: %s", attempt, max_attempts, exc)
            if attempt >= max_attempts:
                raise RuntimeError(
                    f"Apple 公证提交失败（已重试 {max_attempts} 次，疑似公证服务积压或超时）"
                ) from exc
            time.sleep(30)
    output = result.stdout.strip()
    if output:
        logger.info(output)

    status = ""
    request_id = ""
    if output:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).strip().lower()
            request_id = str(payload.get("id", "")).strip()
        elif "Accepted" in output:
            status = "accepted"

    if status != "accepted":
        if request_id:
            log_verbose("[信息] 公证失败，尝试读取详细日志: %s", request_id)
            log_result = run_command(
                ["xcrun", "notarytool", "log", request_id, *auth_args],
                capture_output=True,
                check=False,
                label="读取公证日志",
            )
            if log_result.stdout:
                logger.error(log_result.stdout.strip())
            if log_result.stderr:
                logger.error(log_result.stderr.strip())
        raise RuntimeError("Apple 公证失败")

    run_command(["xcrun", "stapler", "staple", str(dmg_path)], label="装订公证票据")
    run_command(["xcrun", "stapler", "validate", str(dmg_path)], label="验证公证票据")
    log_verbose("[成功] DMG 公证与装订完成")


def publish_artifacts(paths: BuildPaths, config: BuildConfig) -> tuple[Path, Path]:
    """
    输出最终产物位置 / Publish final artifact locations.
    """

    if config.copy_dir is None:
        return paths.app_dir, paths.dmg_path

    config.copy_dir.mkdir(parents=True, exist_ok=True)
    destination_app = config.copy_dir / paths.app_dir.name
    destination_dmg = config.copy_dir / paths.dmg_path.name
    copy_tree(paths.app_dir, destination_app)
    copy_file(paths.dmg_path, destination_dmg)
    log_verbose("[成功] 已复制最终产物到: %s", config.copy_dir)
    return destination_app, destination_dmg


def cleanup_signing_context(signing_context: SigningContext | None) -> None:
    """
    清理临时 keychain 和证书文件；系统 keychain 不做任何操作。
    Clean up the temporary keychain and certificate file; skip if using the system keychain.
    """

    if signing_context is None:
        return

    system_keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    if signing_context.keychain_path == system_keychain:
        # 使用系统 keychain 时无需清理 / No cleanup needed when using the system keychain.
        return

    parent_dir = signing_context.keychain_path.parent
    run_command(["security", "delete-keychain", str(signing_context.keychain_path)], check=False)
    remove_path(parent_dir)


def create_config(args: argparse.Namespace) -> BuildConfig:
    """
    根据参数创建构建配置 / Create build configuration from CLI args.
    """

    return BuildConfig(
        build_type=args.build_type,
        arch=args.arch,
        copy_dir=Path(args.copy_dir).resolve() if args.copy_dir else None,
        debug=args.debug,
        app_version=args.version or read_app_version(),
        commit_hash=get_commit_hash(),
        sign_p12=Path(args.sign_p12).resolve() if args.sign_p12 else None,
        sign_p12_password_env=args.sign_p12_password_env,
        sign_identity=optional_text(args.sign_identity),
        installer_p12=Path(args.installer_p12).resolve() if args.installer_p12 else None,
        installer_p12_password_env=args.installer_p12_password_env,
        installer_identity=optional_text(args.installer_identity),
        release_channel=parse_release_channel(),
        notarize=args.notarize,
        apple_id=optional_text(args.apple_id) or optional_text(os.environ.get("APPLE_ID")),
        apple_password_env=args.apple_password_env,
        team_id=(
            optional_text(args.team_id)
            or optional_text(os.environ.get("MACOS_TEAM_ID"))
            or optional_text(os.environ.get("TEAM_ID"))
        ),
        notary_keychain_profile=(
            optional_text(args.notary_keychain_profile)
            or optional_text(os.environ.get("NOTARY_KEYCHAIN_PROFILE"))
        ),
    )


def run_build(config: BuildConfig) -> None:
    """
    执行完整构建流程 / Run the complete build flow.
    """

    ensure_macos_host()
    ensure_arch_matches(config.arch)
    validate_python_environment()

    if config.build_type == "full":
        ensure_models()

    paths = get_build_paths(config.build_type, config.arch, config.app_version, config.commit_hash)
    clean_build_outputs(paths)
    build_bundle(config, paths)
    organize_app_bundle_resources(paths.app_dir)

    signing_context: SigningContext | None = None
    try:
        signing_context = prepare_signing(config)
        if config.notarize and signing_context is None and not config.sign_identity:
            raise RuntimeError("启用 --notarize 时必须提供 --sign-p12 或 --sign-identity 以完成正式签名")
        sign_app_bundle(paths.app_dir, signing_context)

        # 提供 Installer 证书时走「pkg 装进 DMG」安装器路径（自动装插件/预设/权限）；
        # 否则回退到旧的「.app 拖拽进 Applications」DMG。
        # With an Installer cert, produce the pkg-in-DMG installer; otherwise fall
        # back to the legacy drag-to-Applications DMG.
        use_pkg = config.installer_p12 is not None or config.installer_identity is not None
        if use_pkg:
            pkg_path = build_pkg(config, paths, signing_context)
            create_pkg_dmg(config, paths, pkg_path)
        else:
            create_dmg(config, paths)

        sign_dmg(paths.dmg_path, signing_context)
        notarize_dmg(paths.dmg_path, config)
        final_app, final_dmg = publish_artifacts(paths, config)
        logger.info("[========================================]")
        logger.info("构建完成")
        logger.info("[========================================]")
        logger.info("构建类型: %s", config.build_type)
        logger.info("目标架构: %s", config.arch)
        logger.info(".app: %s", final_app)
        logger.info(".dmg: %s", final_dmg)
    finally:
        cleanup_signing_context(signing_context)


def main() -> None:
    """
    程序入口 / Program entrypoint.
    """

    args = parse_args()
    configure_logging(args.debug)
    config = create_config(args)
    backup_path = inject_build_info(config.commit_hash, config.release_channel)
    try:
        run_build(config)
    finally:
        restore_build_info(backup_path)


if __name__ == "__main__":
    main()
