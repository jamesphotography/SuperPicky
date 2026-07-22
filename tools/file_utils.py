"""
跨平台文件/目录隐藏工具
"""
import os
import stat
import subprocess
import sys
from typing import Optional


def sibling_jpeg(base_path: Optional[str]) -> Optional[str]:
    """
    返回与 base_path 同目录、同主名的 JPG/JPEG 边车路径(存在则返回,否则 None)。

    用途:SuperPicky 的 RAW+JPG 成对拍摄中,配对 JPG 与 RAW 始终位于同一目录、
    同主名,并随整理移动一起搬动。当数据库里的 temp_jpeg_path 因多轮整理/连拍
    重组而失同步时,可用可靠的 current_path/original_path 推导出真正的显示用 JPG,
    避免缩略图/预览丢失。大小写扩展名都尝试以兼容大小写敏感文件系统。

    Return the JPG/JPEG sidecar that sits next to base_path (same directory,
    same stem) if it exists, else None. In SuperPicky's RAW+JPG workflow the
    paired JPG always lives beside the RAW and moves with it, so it can be
    derived from the reliable current_path even when the DB temp_jpeg_path has
    drifted out of sync after organizing/burst-regrouping.

    参数 / Parameters:
        base_path (Optional[str]): 任意成员文件的绝对路径(RAW 或 JPG 均可)。

    返回 / Returns:
        Optional[str]: 存在的同名 JPG 路径 / the existing sibling JPG path.
    """
    if not base_path:
        return None
    stem = os.path.splitext(base_path)[0]
    for ext in ('.jpg', '.jpeg', '.JPG', '.JPEG'):
        candidate = stem + ext
        if os.path.exists(candidate):
            return candidate
    return None


def hide_path(path):
    """
    跨平台隐藏文件或目录
    
    Args:
        path: 要隐藏的文件或目录的绝对路径
        
    Returns:
        bool: 是否成功设置隐藏属性
    """
    if not os.path.exists(path):
        return False
    
    # Windows: 设置 Hidden 属性
    if sys.platform == 'win32':
        try:
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x02
            ret = ctypes.windll.kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_HIDDEN)
            return ret != 0
        except Exception as e:
            # 如果 ctypes 失败，尝试使用 attrib 命令
            try:
                import subprocess
                result = subprocess.run(
                    ['attrib', '+H', path],
                    capture_output=True,
                    shell=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False
    
    # macOS/Linux: 文件名以 . 开头已经隐藏，无需额外操作
    return True


def ensure_hidden_directory(directory_path):
    """
    确保目录存在并设置为隐藏（仅 Windows 需要）
    
    Args:
        directory_path: 目录路径
        
    Returns:
        bool: 目录是否存在且已隐藏
    """
    # 创建目录（如果不存在）
    os.makedirs(directory_path, exist_ok=True)
    
    # 设置隐藏属性
    return hide_path(directory_path)


def clear_readonly_attribute(path):
    """
    跨平台移除文件或目录的只读属性。

    Windows:
    - 优先保留现有属性，仅移除 READONLY 位
    - 失败时回退到 attrib -R

    macOS/Linux:
    - 优先清除 immutable/locked 标志（如果平台支持）
    - 再补充用户写权限

    Args:
        path: 文件或目录路径

    Returns:
        bool: 是否已成功确保可写，或原本就可写
    """
    if not path or not os.path.exists(path):
        return False

    # Windows: 保留原有属性，仅清掉 READONLY 位
    if sys.platform == 'win32':
        try:
            import ctypes
            FILE_ATTRIBUTE_READONLY = 0x01
            INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

            attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
            if attrs != INVALID_FILE_ATTRIBUTES:
                if not (attrs & FILE_ATTRIBUTE_READONLY):
                    return True
                ret = ctypes.windll.kernel32.SetFileAttributesW(
                    path,
                    attrs & ~FILE_ATTRIBUTE_READONLY
                )
                if ret != 0:
                    return True
        except Exception:
            pass

        try:
            result = subprocess.run(
                ['attrib', '-R', path],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    # macOS: 先尝试清理锁定标志，避免 chmod 因 immutable 失败
    immutable_mask = 0
    for flag_name in ('UF_IMMUTABLE', 'SF_IMMUTABLE'):
        immutable_mask |= getattr(stat, flag_name, 0)

    if immutable_mask and hasattr(os, 'chflags'):
        try:
            path_stat = os.stat(path)
            current_flags = getattr(path_stat, 'st_flags', 0)
            if current_flags & immutable_mask:
                os.chflags(path, current_flags & ~immutable_mask)
        except Exception:
            pass

    try:
        current_mode = os.stat(path).st_mode
        desired_mode = current_mode | stat.S_IWUSR
        if desired_mode != current_mode:
            os.chmod(path, desired_mode)
        return os.access(path, os.W_OK)
    except Exception:
        return False


def unhide_path(path):
    """
    取消隐藏文件或目录（主要用于 Windows）
    
    Args:
        path: 要取消隐藏的文件或目录路径
        
    Returns:
        bool: 是否成功取消隐藏属性
    """
    if not os.path.exists(path):
        return False
    
    # Windows: 移除 Hidden 属性
    if sys.platform == 'win32':
        try:
            import ctypes
            FILE_ATTRIBUTE_NORMAL = 0x80
            ret = ctypes.windll.kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_NORMAL)
            return ret != 0
        except Exception:
            try:
                import subprocess
                result = subprocess.run(
                    ['attrib', '-H', path],
                    capture_output=True,
                    shell=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False
    
    # macOS/Linux: 无需操作
    return True
