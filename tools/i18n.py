#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky 国际化 (i18n) 模块
支持多语言界面，使用JSON语言包
"""

import json
import os
import locale
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional

from config import get_lazy_registry


def _safe_print(message: str) -> None:
    """避免在非 UTF-8 控制台输出时抛出编码异常。"""
    try:
        print(message)
    except UnicodeEncodeError:
        stream = getattr(sys, "stdout", None)
        encoding = getattr(stream, "encoding", None) or locale.getpreferredencoding(False) or "utf-8"
        sanitized = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(sanitized)


class I18n:
    """国际化管理器"""

    def __init__(self, default_lang: str = None):
        """
        初始化国际化管理器

        Args:
            default_lang: 默认语言，如果为None则自动检测系统语言
        """
        if getattr(sys, 'frozen', False):
            # PyInstaller packaged mode: 基础 locales 来自冻结包
            base_dir = Path(sys._MEIPASS)
            # 热补丁 locales 覆盖层：code_updates/locales/ 优先于冻结包
            try:
                from config import get_patch_dir
                self._patch_locales_dir: Optional[Path] = get_patch_dir() / "locales"
            except Exception:
                self._patch_locales_dir = None
        else:
            self._patch_locales_dir = None
            # Development mode: handle patch overlay (code_updates/) correctly
            candidate = Path(__file__).parent.parent
            if not (candidate / "locales").exists():
                # Loaded from code_updates/, walk sys.path to find real project root
                for p in sys.path:
                    if p and (Path(p) / "locales").exists():
                        candidate = Path(p)
                        break
            base_dir = candidate

        self.locales_dir = base_dir / "locales"
        self.translations: Dict[str, Any] = {}
        self.current_lang = default_lang or self._detect_system_language()
        self.fallback_lang = "en_US"  # 找不到翻译时使用英文

        # 加载语言包
        self._load_translations()

    def _detect_system_language(self) -> str:
        """
        自动检测系统语言

        Returns:
            语言代码 (zh_CN, en_US等)
        """
        # 1. 尝试从环境变量获取
        lang = os.environ.get('LANG', '')
        if 'zh' in lang.lower():
            return 'zh_CN'
        elif lang.startswith('en'):
            return 'en_US'

        # 2. 尝试使用Python的locale模块
        try:
            system_locale, _ = locale.getlocale()
            if system_locale:
                if 'zh' in system_locale.lower() or 'chinese' in system_locale.lower():
                    return 'zh_CN'
                elif 'en' in system_locale.lower():
                    return 'en_US'
        except Exception:
            pass

        # 3. macOS特殊处理：使用defaults命令
        try:
            result = subprocess.run(
                ['defaults', 'read', '-g', 'AppleLanguages'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                # 输出格式类似: ( "en-US", "zh-Hans-CN" )
                # 提取第一个引号内的内容
                output = result.stdout
                import re
                match = re.search(r'"([^"]+)"', output)
                if match:
                    first_lang = match.group(1).lower()
                    if 'zh-hans' in first_lang or 'zh_cn' in first_lang:
                        return 'zh_CN'
                    elif 'zh-hant' in first_lang or 'zh_tw' in first_lang:
                        return 'zh_TW'
        except Exception:
            pass

        # 4. 默认使用英语 (If not Chinese, use English)
        return 'en_US'

    def _load_translations(self) -> None:
        """加载当前语言的翻译，热补丁 locales 覆盖冻结包 locales"""
        locale_file = self.locales_dir / f"{self.current_lang}.json"

        if not locale_file.exists():
            _safe_print(f"警告: 语言包 {self.current_lang}.json 不存在，使用默认语言")
            locale_file = self.locales_dir / f"{self.fallback_lang}.json"
            if not locale_file.exists():
                _safe_print(f"错误: Fallback语言包 {self.fallback_lang}.json 也不存在")
                self.translations = {}
                return

        try:
            with open(locale_file, 'r', encoding='utf-8') as f:
                self.translations = json.load(f)
            _safe_print(f"✅ Language pack loaded: {self.current_lang}")
        except Exception as e:
            _safe_print(f"❌ 加载语言包失败: {e}")
            self.translations = {}

        # 热补丁 locales 覆盖层（仅 frozen 模式）
        patch_dir = getattr(self, '_patch_locales_dir', None)
        if patch_dir:
            patch_file = patch_dir / f"{self.current_lang}.json"
            if not patch_file.exists():
                patch_file = patch_dir / f"{self.fallback_lang}.json"
            if patch_file.exists():
                try:
                    import copy
                    with open(patch_file, 'r', encoding='utf-8') as f:
                        patch_data = json.load(f)
                    # 深度合并：patch 的 key 覆盖基础翻译，基础翻译作为兜底
                    def _deep_merge(base: dict, overlay: dict) -> dict:
                        result = copy.deepcopy(base)
                        for k, v in overlay.items():
                            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                                result[k] = _deep_merge(result[k], v)
                            else:
                                result[k] = v
                        return result
                    self.translations = _deep_merge(self.translations, patch_data)
                    _safe_print(f"✅ Patch locale overlay applied: {patch_file.name}")
                except Exception as e:
                    _safe_print(f"⚠️ 补丁语言包覆盖失败: {e}")

    def t(self, key: str, **params) -> str:
        """
        翻译函数

        Args:
            key: 翻译key，支持嵌套 (例如: "app.title", "buttons.start")
            **params: 参数，用于格式化字符串 (例如: start=1, end=50)

        Returns:
            翻译后的文本

        Examples:
            >>> i18n.t("app.title")
            "慧眼选鸟 - AI智能照片筛选"

            >>> i18n.t("logs.batch_progress", start=1, end=50, success=45)
            "批次 1-50: 45 成功"
        """
        # 按点号分割key，支持嵌套
        keys = key.split('.')
        value = self.translations

        # 逐级查找
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                # 找不到翻译，返回key本身（便于调试）
                return key

        # 如果value是字符串，进行参数替换
        if isinstance(value, str):
            try:
                return value.format(**params) if params else value
            except KeyError as e:
                _safe_print(f"警告: 翻译参数缺失: {key}, 缺少参数: {e}")
                return value
        else:
            return str(value)

    def switch_language(self, lang: str) -> bool:
        """
        切换语言（需要重启应用生效）

        Args:
            lang: 语言代码 (zh_CN, en_US等)

        Returns:
            是否切换成功
        """
        locale_file = self.locales_dir / f"{lang}.json"
        if not locale_file.exists():
            _safe_print(f"错误: 语言包 {lang}.json 不存在")
            return False

        self.current_lang = lang
        self._load_translations()
        return True

    def get_available_languages(self) -> Dict[str, str]:
        """
        获取所有可用的语言

        Returns:
            {语言代码: 语言名称} 字典
        """
        languages = {}

        if not self.locales_dir.exists():
            return languages

        for file in self.locales_dir.glob("*.json"):
            lang_code = file.stem  # 文件名（不含扩展名）

            # 读取语言包中的语言名称
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    lang_name = data.get('_meta', {}).get('language_name', lang_code)
                    languages[lang_code] = lang_name
            except Exception:
                # 如果读取失败，使用默认名称
                lang_names = {
                    'zh_CN': '简体中文',
                    'en_US': 'English',
                    'zh_TW': '繁體中文'
                }
                languages[lang_code] = lang_names.get(lang_code, lang_code)

        return languages


def set_primary_language(lang: str) -> None:
    """
    设置 UI 主语言。主窗口初始化时调用，确保所有 get_i18n() 无参调用
    都返回与 UI 相同语言的实例，与创建顺序无关。
    """
    get_lazy_registry().set('_primary_lang', lang)


def apply_saved_language() -> None:
    """
    让命令行等非图形入口跟随「设置」里选的界面语言（与图形界面一致）；没选时按系统语言。

    此前命令行只看系统语言，与图形界面可能不一致。读配置失败时静默退回系统语言。

    Make non-GUI entry points (the CLIs) follow the language chosen in Settings,
    like the GUI does; falls back to the system language when none is set.
    """
    try:
        from advanced_config import get_advanced_config
        lang = get_advanced_config().language
    except Exception:
        lang = None
    if lang:
        get_i18n(lang)
        set_primary_language(lang)


def get_i18n(lang: str = None) -> I18n:
    """
    获取国际化实例（单例模式）

    Args:
        lang: 指定语言，如果为None则自动检测

    Returns:
        I18n实例
    """
    registry = get_lazy_registry()
    if lang is None:
        # 1. 优先返回主窗口显式声明的语言实例（最可靠）
        primary = registry.get('_primary_lang')
        if primary:
            key = f"i18n.instance::{primary}"
            existing = registry.get(key)
            if existing is not None:
                return existing
        # 2. 兜底：查找任意已存在的显式语言实例
        for candidate_lang in ("zh_CN", "en_US", "zh_TW"):
            candidate_key = f"i18n.instance::{candidate_lang}"
            existing = registry.get(candidate_key)
            if existing is not None:
                return existing
    key = f"i18n.instance::{lang or 'auto'}"
    return registry.get_or_create(key, lambda: I18n(default_lang=lang))


# 便捷函数
def t(key: str, **params) -> str:
    """
    翻译函数的便捷版本

    Args:
        key: 翻译key
        **params: 格式化参数

    Returns:
        翻译后的文本
    """
    return get_i18n().t(key, **params)
