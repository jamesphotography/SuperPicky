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
            # PyInstaller packaged mode
            base_dir = Path(sys._MEIPASS)
        else:
            # Normal development mode
            base_dir = Path(__file__).parent.parent

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
        """加载当前语言的翻译"""
        locale_file = self.locales_dir / f"{self.current_lang}.json"

        if not locale_file.exists():
            _safe_print(f"警告: 语言包 {self.current_lang}.json 不存在，使用默认语言")
            # 尝试加载fallback语言
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


# 全局实例
_i18n_instance: Optional[I18n] = None


def get_i18n(lang: str = None) -> I18n:
    """
    获取国际化实例（单例模式）

    Args:
        lang: 指定语言，如果为None则自动检测

    Returns:
        I18n实例
    """
    global _i18n_instance
    if _i18n_instance is None:
        _i18n_instance = I18n(default_lang=lang)
    return _i18n_instance


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
