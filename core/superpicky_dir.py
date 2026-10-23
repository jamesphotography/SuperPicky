# -*- coding: utf-8 -*-
"""
删除处理目录里的 .superpicky 隐藏目录（重置用）/ Removing the .superpicky folder on reset.

#146：Windows 上开着的文件（report.db 及其 -wal / -shm）删不掉，shutil.rmtree 删到一半就停，
原先的兜底 `rm -rf` 在 Windows 上又不存在，于是留下 3 个报告文件，主界面还以为有结果。
这里改为纯 Python、跨平台的删除：遇到只读文件 / 目录先去掉只读再删；整体失败时短暂等待重试
（给刚关闭的数据库连接、杀毒软件、索引服务一点时间释放句柄）。调用方仍须先关闭 report.db 连接。

#146: on Windows, files still open (report.db, -wal, -shm) cannot be deleted, so
rmtree stopped halfway and the `rm -rf` fallback does not exist there. This
helper is pure Python and cross-platform: it clears read-only flags and retries
briefly. Callers must still close any report.db connection first.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from typing import Callable

SUPERPICKY_DIRNAME = ".superpicky"


def _clear_readonly_and_retry(func: Callable[[str], object], path: str, _exc: object) -> None:
    """
    rmtree 的出错回调：给出错的路径及其父目录加上写权限，再重做一次。

    参数:
    func: rmtree 刚才失败的那个操作（os.unlink / os.rmdir 等）
    path (str): 出错的路径
    _exc: 异常信息（不使用）

    rmtree error hook: make the path and its parent writable, then retry the
    failed operation once. A second failure propagates to the caller.
    """
    for target in (os.path.dirname(path), path):
        try:
            os.chmod(target, stat.S_IRWXU)
        except OSError:
            pass
    func(path)


def remove_superpicky_dir(directory: str, attempts: int = 3, wait_seconds: float = 0.5) -> bool:
    """
    删除 directory 下的 .superpicky 目录；不存在也算成功。

    参数:
    directory (str): 处理的照片目录
    attempts (int): 最多尝试几轮
    wait_seconds (float): 每轮失败后等待的秒数

    返回:
    bool: 删干净（或本来就没有）返回 True；几轮之后仍有残留返回 False

    Remove <directory>/.superpicky; a missing folder counts as success.
    Returns False if something is still left after all attempts.
    """
    target = os.path.join(directory, SUPERPICKY_DIRNAME)
    for attempt in range(attempts):
        if not os.path.exists(target):
            return True
        try:
            # Python 3.12 起 onerror 弃用、改名 onexc（回调参数相同用法）
            # onerror is deprecated since 3.12 in favour of onexc
            if sys.version_info >= (3, 12):
                shutil.rmtree(target, onexc=_clear_readonly_and_retry)
            else:
                shutil.rmtree(target, onerror=_clear_readonly_and_retry)
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(wait_seconds)
    return not os.path.exists(target)
