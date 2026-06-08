#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 更新检测器
检查 GitHub Releases 获取最新版本，支持 Mac/Windows 分平台下载
"""

import sys
import platform
import urllib.request
import json
import re
from typing import Optional, Tuple, Dict
from packaging import version


# 当前版本号（从 constants.py 统一获取）
from constants import APP_VERSION
from config import config
CURRENT_VERSION = APP_VERSION

# GitHub API 配置
GITHUB_REPO = "jamesphotography/SuperPicky"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_LIST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

# GitCode（中国大陆优先 fallback）
GITCODE_PROJECT_ID = "Jamesphotography%2FSuperPicky"
GITCODE_RELEASES_API = f"https://gitcode.com/api/v4/projects/{GITCODE_PROJECT_ID}/releases"

# 北京镜像服务器（最终兜底）
MIRROR_LATEST_URL = f"{config.endpoints.MIRROR_BASE_URL}/latest.json"

# 平台+架构对应的 Asset 文件名模式
# 三层匹配策略：精确架构 > 通用版本 > 任意版本
PLATFORM_ARCH_PATTERNS = {
    'darwin': {
        'arm64': ['_arm64', '-arm64', '_apple_silicon', '-apple_silicon', '_m1', '-m1', '_m2', '-m2'],
        'x86_64': ['_x64', '-x64', '_x86_64', '-x86_64', '_intel', '-intel'],
        'universal': ['_universal', '-universal', '_mac', '-mac', 'macos', '.dmg']
    },
    'win32': {
        'AMD64': ['_x64', '-x64', '_win64', '-win64'],
        'x86': ['_x86', '-x86', '_win32', '-win32'],
        'universal': ['_win', '-win', 'windows', '.exe', '.msi', '-setup']
    }
}


def get_version_channel(ver: str) -> str:
    """
    判定版本渠道

    规则：
    - 纯 X.Y.Z（三段数字）→ 'official'（正式版，必须提示更新）
    - 含 -RC（不区分大小写）    → 'nightly'（预发布，可选更新）
    - 其他（含 -beta、-hotfix 等后缀）→ 'dev'（开发版，不检查更新）

    Args:
        ver: 版本字符串，如 "4.2.5"、"4.2.5-RC3"、"4.2.5-beta"

    Returns:
        'official' | 'nightly' | 'dev'
    """
    ver = ver.strip().lstrip('vV')
    if re.fullmatch(r'\d+\.\d+\.\d+', ver):
        return 'official'
    if re.search(r'-rc', ver, re.IGNORECASE):
        return 'nightly'
    return 'dev'


def _mark_patch_check_skipped(update_info: Dict, reason: str) -> None:
    """在更新结果中标记补丁检查被安全跳过。"""
    update_info['patch_applied'] = False
    update_info['patch_skipped'] = True
    update_info['patch_message'] = reason


# === 4.3.0 起：应用内在线版本更新检测整体停用 ===
# 所有版本（mac dmg / Win Lite / Win Full CPU·CUDA）都不再在应用内检测或提示新版本。
# Full 版因 CUDA 安装包 >2GB 无法进 GitHub Release，升级一律走官网/网盘手动下载；
# Lite 的「首启下载模型/库」属于初始化（core/initialization_manager），与本开关无关、照常工作。
# Online version-update checking is fully disabled since 4.3.0; set back to False to re-enable.
ONLINE_UPDATE_CHECK_DISABLED = True


class UpdateChecker:
    """更新检测器"""

    def __init__(self, current_version: str = CURRENT_VERSION):
        self.current_version = current_version
        self._latest_info: Optional[Dict] = None

    @property
    def channel(self) -> str:
        """当前版本渠道：'official' | 'nightly' | 'dev'

        优先读取 build_info.RELEASE_CHANNEL（CI 打包时注入），
        确保 RC 包（APP_VERSION 为纯数字）能正确识别为 nightly 渠道。
        """
        try:
            from core.build_info import RELEASE_CHANNEL
            if RELEASE_CHANNEL in ('nightly', 'official'):
                return RELEASE_CHANNEL
        except Exception:
            pass
        return get_version_channel(self.current_version)

    def should_check_updates(self) -> bool:
        """是否需要检查更新（4.3.0 起整体停用；dev 渠道本就不检查）"""
        if ONLINE_UPDATE_CHECK_DISABLED:
            return False
        return self.channel != 'dev'

    def check_for_updates(self, timeout: int = 10, include_prerelease: bool = False) -> Tuple[bool, Optional[Dict]]:
        """
        检查是否有更新

        渠道规则：
        - official：只与正式 Release 比较
        - nightly：与最新 Release（含预发布）比较
        - dev：直接返回 False，不发起网络请求

        Args:
            timeout: 请求超时时间（秒）
            include_prerelease: 为 True 时拉取所有 releases（含预发布）列表

        Returns:
            (has_update, update_info) - update_info 包含:
                - version: 最新版本号
                - current_version: 当前版本号
                - channel: 当前版本渠道
                - download_url: 当前平台的下载链接
                - release_notes: 发布说明
                - release_url: GitHub Release 页面链接
        """
        # dev 渠道不检查更新
        if not self.should_check_updates():
            return False, {
                'version': self.current_version,
                'current_version': self.current_version,
                'channel': 'dev',
                'download_url': None,
                'release_notes': '',
                'release_url': GITHUB_RELEASES_URL,
            }

        # nightly 渠道自动包含预发布版本
        if self.channel == 'nightly':
            include_prerelease = True

        try:
            # macOS SSL证书问题修复
            import ssl
            import urllib.request

            # 创建自定义的SSL上下文，禁用证书验证
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # 选择 API 端点
            api_url = GITHUB_RELEASES_LIST_URL if include_prerelease else GITHUB_API_URL

            req = urllib.request.Request(
                api_url,
                headers={
                    'Accept': 'application/vnd.github.v3+json',
                    'User-Agent': f'SuperPicky/{self.current_version}'
                }
            )

            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as response:
                raw = json.loads(response.read().decode('utf-8'))

            # /releases 返回列表，/releases/latest 返回单个对象
            if include_prerelease:
                # 取发布时间最新的一条（列表已按 published_at 倒序）
                data = raw[0] if raw else {}
            else:
                data = raw
            
            self._latest_info = data
            
            # 解析版本号
            latest_version = data.get('tag_name', '').lstrip('vV')
            if not latest_version:
                # 无法获取版本，返回当前版本信息
                return False, {
                    'version': self.current_version,
                    'current_version': self.current_version,
                    'channel': self.channel,
                    'download_url': None,
                    'release_notes': '',
                    'release_url': GITHUB_RELEASES_URL,
                }

            # 比较版本
            try:
                has_update = version.parse(latest_version) > version.parse(self.current_version)
            except Exception:
                # 简单字符串比较作为回退
                has_update = latest_version != self.current_version

            # 获取当前平台的下载链接
            download_url = self._find_platform_download(data.get('assets', []))

            # 始终返回版本信息
            # 'assets' 字段供 UI 层 select_installer_asset 选择平台对应安装包；
            # 'assets' is consumed by the UI layer's installer selection logic.
            update_info = {
                'version': latest_version,
                'current_version': self.current_version,
                'channel': self.channel,
                'download_url': download_url,
                'assets': data.get('assets', []),
                'release_notes': data.get('body', ''),
                'release_url': data.get('html_url', GITHUB_RELEASES_URL),
                'published_at': data.get('published_at', ''),
                'patch_applied': False,
                'patch_version': None,
            }

            # 没有整包更新时，检查是否有补丁
            if not has_update:
                try:
                    from tools.patch_manager import (
                        check_and_apply_patch,
                        get_patch_runtime_block_reason,
                    )

                    blocked_reason = get_patch_runtime_block_reason()
                    if blocked_reason:
                        _mark_patch_check_skipped(update_info, blocked_reason)
                    else:
                        patched, msg = check_and_apply_patch(
                            data.get('assets', []),
                            self.current_version,
                        )
                        update_info['patch_applied'] = patched
                        update_info['patch_message'] = msg
                        if patched:
                            from tools.patch_manager import read_local_meta
                            meta = read_local_meta()
                            update_info['patch_version'] = meta.get('patch_version') if meta else None
                except Exception as e:
                    update_info['patch_message'] = f'补丁检查异常: {e}'

            return has_update, update_info
            
        except (urllib.error.URLError, json.JSONDecodeError, Exception) as e:
            print(f"⚠️ GitHub API 不可达 ({type(e).__name__}): {e}，尝试北京镜像...")
            result = self._check_from_mirror()
            if result[1] and 'error' not in result[1]:
                return result
            print("⚠️ 北京镜像不可达，尝试 GitCode...")
            return self._check_from_gitcode()

    def _check_from_gitcode(self) -> Tuple[bool, Optional[Dict]]:
        """
        从 GitCode releases API 获取版本信息（GitHub API 失败后的第一 fallback）。
        """
        try:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                GITCODE_RELEASES_API,
                headers={'User-Agent': f'SuperPicky/{self.current_version}'}
            )
            with urllib.request.urlopen(req, timeout=8, context=ssl_context) as resp:
                releases = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"⚠️ GitCode releases API 失败: {e}")
            return False, {'error': str(e), 'current_version': self.current_version}

        if not releases:
            return False, {'error': 'GitCode 无 release 数据', 'current_version': self.current_version}

        data = releases[0]  # 最新 release
        latest_version = data.get('tag_name', '').lstrip('vV')
        if not latest_version:
            return False, {'error': 'GitCode release 无版本号', 'current_version': self.current_version}

        try:
            has_update = version.parse(latest_version) > version.parse(self.current_version)
        except Exception:
            has_update = latest_version != self.current_version

        gitcode_links = data.get('assets', {}).get('links', [])

        update_info = {
            'version': latest_version,
            'current_version': self.current_version,
            'channel': self.channel,
            'download_url': None,
            'release_notes': data.get('description', ''),
            'release_url': GITHUB_RELEASES_URL,
            'published_at': data.get('released_at', ''),
            'patch_applied': False,
            'patch_version': None,
            'via_gitcode': True,
        }

        if not has_update:
            try:
                from tools.patch_manager import (
                    check_and_apply_patch_from_gitcode,
                    get_patch_runtime_block_reason,
                )

                blocked_reason = get_patch_runtime_block_reason()
                if blocked_reason:
                    _mark_patch_check_skipped(update_info, blocked_reason)
                else:
                    patched, msg = check_and_apply_patch_from_gitcode(gitcode_links, self.current_version)
                    update_info['patch_applied'] = patched
                    update_info['patch_message'] = msg
                    if patched:
                        from tools.patch_manager import read_local_meta
                        meta = read_local_meta()
                        update_info['patch_version'] = meta.get('patch_version') if meta else None
            except Exception as e:
                update_info['patch_message'] = f'GitCode 补丁检查异常: {e}'

        return has_update, update_info

    def _check_from_mirror(self) -> Tuple[bool, Optional[Dict]]:
        """
        从镜像服务器获取版本信息（GitHub API 不可达时的 fallback）。
        仅能获取版本号和补丁信息，不提供安装包下载链接。
        """
        try:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                MIRROR_LATEST_URL,
                headers={'User-Agent': f'SuperPicky/{self.current_version}'}
            )
            with urllib.request.urlopen(req, timeout=8, context=ssl_context) as resp:
                mirror_data = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"⚠️ 镜像服务器也不可达: {e}")
            return False, {'version': '检查失败', 'current_version': self.current_version, 'error': str(e)}

        latest_version = mirror_data.get('version', '').lstrip('vV')
        if not latest_version:
            return False, {'version': '检查失败', 'current_version': self.current_version, 'error': '镜像数据格式错误'}

        try:
            has_update = version.parse(latest_version) > version.parse(self.current_version)
        except Exception:
            has_update = latest_version != self.current_version

        update_info = {
            'version': latest_version,
            'current_version': self.current_version,
            'channel': self.channel,
            'download_url': None,  # 镜像不提供安装包，引导用户去 GitHub
            'release_notes': '',
            'release_url': GITHUB_RELEASES_URL,
            'published_at': mirror_data.get('published_at', ''),
            'patch_applied': False,
            'patch_version': None,
            'via_mirror': True,
        }

        if not has_update:
            try:
                from tools.patch_manager import (
                    check_and_apply_patch_from_mirror,
                    get_patch_runtime_block_reason,
                )

                blocked_reason = get_patch_runtime_block_reason()
                if blocked_reason:
                    _mark_patch_check_skipped(update_info, blocked_reason)
                else:
                    patched, msg = check_and_apply_patch_from_mirror(self.current_version)
                    update_info['patch_applied'] = patched
                    update_info['patch_message'] = msg
                    if patched:
                        from tools.patch_manager import read_local_meta
                        meta = read_local_meta()
                        update_info['patch_version'] = meta.get('patch_version') if meta else None
            except Exception as e:
                update_info['patch_message'] = f'镜像补丁检查异常: {e}'

        return has_update, update_info
    
    def _find_platform_download(self, assets: list) -> Optional[str]:
        """
        根据当前平台和架构查找对应的下载链接

        三层匹配策略：
        1. 精确架构匹配 - 优先查找 arm64/intel/x64 精确匹配
        2. 通用版本回退 - 查找 universal 版本
        3. 任意版本兜底 - 返回第一个平台相关的 DMG/EXE

        Args:
            assets: GitHub Release 的 assets 列表

        Returns:
            下载链接或 None
        """
        if not assets:
            return None

        # 确定当前平台和架构
        platform_key = 'darwin' if sys.platform == 'darwin' else 'win32'
        machine = platform.machine()  # arm64, x86_64, AMD64, x86 等

        arch_patterns = PLATFORM_ARCH_PATTERNS.get(platform_key, {})
        if not arch_patterns:
            return None

        # 获取当前架构的精确匹配模式
        exact_patterns = arch_patterns.get(machine, [])
        universal_patterns = arch_patterns.get('universal', [])

        # 第一层：精确架构匹配
        for asset in assets:
            name = asset.get('name', '').lower()
            download_url = asset.get('browser_download_url', '')

            for pattern in exact_patterns:
                if pattern.lower() in name:
                    return download_url

        # 第二层：通用版本回退
        for asset in assets:
            name = asset.get('name', '').lower()
            download_url = asset.get('browser_download_url', '')

            for pattern in universal_patterns:
                if pattern.lower() in name:
                    return download_url

        # 第三层：任意平台相关版本兜底
        # macOS: 返回第一个 .dmg 文件
        # Windows: 返回第一个 .exe 或 .msi 文件
        fallback_extensions = ['.dmg'] if platform_key == 'darwin' else ['.exe', '.msi']
        for asset in assets:
            name = asset.get('name', '').lower()
            download_url = asset.get('browser_download_url', '')

            for ext in fallback_extensions:
                if name.endswith(ext):
                    return download_url

        return None
    
    @staticmethod
    def get_platform_name() -> str:
        """获取当前平台名称（用于UI显示，包含架构信息）"""
        machine = platform.machine()

        if sys.platform == 'darwin':
            if machine == 'arm64':
                return 'macOS (Apple Silicon)'
            else:
                return 'macOS (Intel)'
        elif sys.platform.startswith('win'):
            if machine == 'AMD64':
                return 'Windows (64-bit)'
            else:
                return 'Windows (32-bit)'
        else:
            return f'Linux ({machine})'

    @staticmethod
    def get_platform_short_name() -> str:
        """获取平台简短标识（用于文件命名匹配）"""
        machine = platform.machine()

        if sys.platform == 'darwin':
            if machine == 'arm64':
                return 'mac_arm64'
            else:
                return 'mac_intel'
        elif sys.platform.startswith('win'):
            if machine == 'AMD64':
                return 'win64'
            else:
                return 'win32'
        else:
            return f'linux_{machine}'


def check_update_async(callback, current_version: str = CURRENT_VERSION):
    """
    异步检查更新（在后台线程执行）
    
    Args:
        callback: 回调函数，签名 callback(has_update: bool, update_info: Optional[Dict])
        current_version: 当前版本号
    """
    import threading
    
    def _check():
        checker = UpdateChecker(current_version)
        has_update, update_info = checker.check_for_updates()
        callback(has_update, update_info)
    
    thread = threading.Thread(target=_check, daemon=True)
    thread.start()


# 测试代码
if __name__ == "__main__":
    print("=== SuperPicky 更新检测器测试 ===\n")
    print(f"当前版本: {CURRENT_VERSION}")
    print(f"版本渠道: {get_version_channel(CURRENT_VERSION)}")
    print(f"当前平台: {UpdateChecker.get_platform_name()}")
    print(f"平台标识: {UpdateChecker.get_platform_short_name()}")
    print(f"CPU 架构: {platform.machine()}\n")

    checker = UpdateChecker()
    print(f"渠道: {checker.channel}  是否检查更新: {checker.should_check_updates()}\n")

    has_update, info = checker.check_for_updates()

    if not checker.should_check_updates():
        print("⏭ DEV 渠道，跳过更新检查")
    elif has_update:
        print(f"✅ 发现新版本: {info['version']}")
        print(f"📦 下载链接: {info['download_url']}")
        print(f"🔗 Release 页面: {info['release_url']}")
        print(f"\n📝 发布说明:\n{info['release_notes'][:500]}...")
    else:
        print("✅ 已是最新版本")
