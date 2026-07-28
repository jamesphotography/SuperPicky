#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExifTool管理器
用于设置照片评分和锐度值到EXIF/IPTC元数据
"""

import os
import subprocess
import sys
import tempfile
import shutil
from typing import Optional, List, Dict
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import RATING_FOLDER_NAMES, SIDECAR_RAW_EXTENSIONS
import time
import threading
import queue

import atexit


def merge_keyword_lists(existing: List[str], additions: List[str]) -> Optional[List[str]]:
    """
    合并关键字列表(merge-add 语义):保留 existing 全量与顺序,追加
    additions 中缺失项(输入自身去重);无新增返回 None(调用方跳过写入)。

    Merge keyword lists (merge-add): keep all of `existing` in order,
    append items from `additions` not yet present (deduping the input);
    return None when nothing new needs writing.

    参数 / Parameters:
        existing (List[str]): 文件中已有关键字 / keywords already on file.
        additions (List[str]): 需确保存在的关键字 / keywords to ensure.

    返回 / Returns:
        Optional[List[str]]: 合并后完整列表;None=无需写入。
    """
    merged = list(existing)
    seen = set(existing)
    for kw in additions:
        kw = (kw or "").strip()
        if kw and kw not in seen:
            merged.append(kw)
            seen.add(kw)
    return merged if len(merged) != len(existing) else None


class _ExifToolProcess:
    """
    单个 exiftool -stay_open 常驻进程的封装（进程句柄 + stdout 读取线程 + 锁）。

    读写分离：ExifToolManager 持有 read/write 两个实例，读操作（read_metadata/
    extract_binary/对焦点读取等）与写操作（batch_set_metadata 等）各走一个进程，
    互不阻塞。此前读写共用一个进程和一把锁，批量写 64 个文件会持锁约 3 秒，
    导致主流程的对焦点/ISO 读取被卡住（表现为每约 30 张照片停顿 3 秒）。

    A wrapper around one exiftool -stay_open persistent process (process handle
    + stdout reader thread + lock). ExifToolManager keeps two instances
    (read/write) so read calls are never queued behind a long batch write,
    which previously stalled the main pipeline ~3s every ~30 photos because
    both paths shared one process and one lock.
    """

    def __init__(self, exiftool_path: str, cwd: str, role: str):
        """
        参数 / Parameters:
            exiftool_path (str): exiftool 可执行文件路径 / exiftool executable path
            cwd (str): 进程工作目录（Windows 需在 exiftool 目录下运行）/ working dir
            role (str): 'read' 或 'write'，仅用于日志标识 / log label only
        """
        self.exiftool_path = exiftool_path
        self._cwd = cwd
        self.role = role
        # RLock：批量写方法需要在持锁期间调用 start()/read_until_ready()
        # RLock: batch writers call start()/read_until_ready() while holding it
        self.lock = threading.RLock()
        self.process = None
        self._stdout_queue = None
        self._reader_thread = None

    @staticmethod
    def _read_stdout_to_queue(out_pipe, q):
        """后台线程读取 stdout / Background thread draining stdout into a queue"""
        try:
            for line in iter(out_pipe.readline, b''):
                q.put(line)
        except:
            pass
        finally:
            try:
                out_pipe.close()
            except:
                pass

    def is_alive(self) -> bool:
        """进程是否存活 / Whether the process is running"""
        return self.process is not None and self.process.poll() is None

    def start(self):
        """启动常驻 ExifTool 进程（幂等）/ Start the persistent process (idempotent)"""
        with self.lock:
            if self.is_alive():
                return

            try:
                # 启动命令（不在此处使用 -fast/-ignoreMinorErrors，避免 ARW 写入后 Image Edge Viewer 无法打开）
                # 旧版 SuperPickyOsk 写入时未使用这两项，ARW 在 Sony 软件中可正常查看
                cmd = [
                    self.exiftool_path,
                    '-stay_open', 'True',
                    '-@', '-',
                    '-common_args',
                    '-charset', 'utf8',
                    '-overwrite_original_in_place',  # 保留文件 Birth Time（inode 不变）
                ]

                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0

                # 将 stderr 合并到 stdout，避免 stderr 缓冲区塞满导致死锁
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self._cwd,
                    creationflags=creationflags
                )

                # 启动读取线程
                self._stdout_queue = queue.Queue()
                self._reader_thread = threading.Thread(
                    target=self._read_stdout_to_queue,
                    args=(self.process.stdout, self._stdout_queue),
                    daemon=True
                )
                self._reader_thread.start()

                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 ExifTool persistent process started ({self.role}, PID: {self.process.pid}, threaded read)")
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ❌ Failed to start ExifTool process ({self.role}): {e}")
                self.process = None

    def stop(self):
        """停止常驻进程 / Stop the persistent process"""
        with self.lock:
            if not self.process:
                return

            process = self.process
            reader_thread = self._reader_thread
            pid = process.pid
            try:
                if process.poll() is None and process.stdin:
                    process.stdin.write(b'-stay_open\nFalse\n')
                    process.stdin.flush()
                    process.wait(timeout=2)
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ ExifTool process ({self.role}, PID: {pid}) stopped gracefully")
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ⚠️  ExifTool process ({self.role}, PID: {pid}) stop failed: {e}")
            finally:
                if process.poll() is None:
                    try:
                        process.kill()
                        process.wait(timeout=2)
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ ExifTool process ({self.role}, PID: {pid}) killed")
                    except Exception as e:
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ⚠️  ExifTool process ({self.role}, PID: {pid}) kill failed: {e}")
                for pipe_name in ('stdin', 'stdout', 'stderr'):
                    pipe = getattr(process, pipe_name, None)
                    if pipe is None:
                        continue
                    try:
                        pipe.close()
                    except Exception:
                        pass
                if reader_thread is not None and reader_thread.is_alive():
                    try:
                        reader_thread.join(timeout=2)
                    except Exception:
                        pass
                self.process = None
                self._stdout_queue = None
                self._reader_thread = None

    def read_until_ready(self, timeout=10.0) -> bytes:
        """从队列读取直到 {ready}，支持超时 / Read stdout lines until {ready} or timeout"""
        if not self._stdout_queue:
            return b""

        output = b""
        start_time = time.time()

        while True:
            # 计算剩余时间
            elapsed = time.time() - start_time
            remaining = timeout - elapsed

            if remaining <= 0:
                raise TimeoutError(f"ExifTool timeout ({timeout}s)")

            try:
                line = self._stdout_queue.get(timeout=remaining)
                output += line
                if b'{ready}' in line:
                    return output
            except queue.Empty:
                raise TimeoutError(f"ExifTool timeout ({timeout}s)")

    def send_command(self, args: List[str], timeout=30.0) -> bytes:
        """
        发送一条命令（单个 -execute）到常驻进程并返回输出。
        出错时打印日志、停止进程（下次调用自动重启）并抛出异常。

        Send one command (single -execute) to the persistent process and return
        its output. On failure: log, stop the process (auto-restart on next
        call), and re-raise.
        """
        with self.lock:
            self.start()
            if not self.process:
                raise RuntimeError(f"ExifTool process not available ({self.role})")

            try:
                # 确保命令以 -execute 结尾
                if '-execute' not in args:
                    cmd_str = '\n'.join(args) + '\n-execute\n'
                else:
                    cmd_str = '\n'.join(args) + '\n'

                self.process.stdin.write(cmd_str.encode('utf-8'))
                self.process.stdin.flush()

                # 读取直到 {ready}
                return self.read_until_ready(timeout)
            except (TimeoutError, Exception) as e:
                # 记录错误并停止进程，下次会自动重新启动
                print(f"❌ ExifTool persistent error ({self.role}): {e}")
                self.stop()
                raise


class ExifToolManager:
    """ExifTool管理器 - 使用本地打包的exiftool"""

    def __init__(self):
        """初始化ExifTool管理器"""
        # 获取exiftool路径（支持PyInstaller打包）
        self.exiftool_path = self._get_exiftool_path()
        # Windows 下 exiftool.exe 需在其所在目录运行才能找到 exiftool_files 中的 DLL/perl
        self._exiftool_cwd = os.path.dirname(os.path.abspath(self.exiftool_path))

        # 验证exiftool可用性
        if not self._verify_exiftool():
            raise RuntimeError(f"ExifTool不可用: {self.exiftool_path}")

        print(f"✅ ExifTool loaded: {self.exiftool_path}")
        
        # V4.5: 读写分离的两个常驻进程。读操作（对焦点/ISO/GPS/预览提取）走
        # read 进程，写操作（星级/题注批量写入）走 write 进程，读延迟不再受
        # 批量写持锁时长影响（原共用进程时每 ~30 张照片主流程停顿 ~3 秒）。
        # V4.5: Two persistent processes, one for reads (focus point / ISO /
        # GPS / preview extraction) and one for writes (rating/caption
        # batches), so read latency no longer depends on batch-write duration
        # (sharing one process used to stall the pipeline ~3s every ~30 photos).
        self._read_proc = _ExifToolProcess(self.exiftool_path, self._exiftool_cwd, 'read')
        self._write_proc = _ExifToolProcess(self.exiftool_path, self._exiftool_cwd, 'write')
        self._session_lock = threading.Lock()
        self._persistent_session_count = 0
        self._shutdown_lock = threading.Lock()
        self._is_shutdown = False
        
        # 注册退出清理
        atexit.register(self.shutdown)

    def _get_exiftool_path(self) -> str:
        """获取exiftool可执行文件路径"""
        # V3.9.4: 处理 Windows 平台的可执行文件后缀
        is_windows = sys.platform.startswith('win')
        exe_name = 'exiftool.exe' if is_windows else 'exiftool'

        if hasattr(sys, '_MEIPASS'):
            # PyInstaller打包后的路径
            base_path = sys._MEIPASS
            print(f"🔍 PyInstaller environment detected")
            print(f"   base_path (sys._MEIPASS): {base_path}")

            # 使用新的目录结构：exiftools_mac 或 exiftools_win
            if is_windows:
                exiftool_dir = 'exiftools_win'
            else:
                exiftool_dir = 'exiftools_mac'
            
            exiftool_path = os.path.join(base_path, exiftool_dir, exe_name)
            abs_path = os.path.abspath(exiftool_path)

            print(f"   Checking {exe_name}...")
            print(f"   Path: {abs_path}")
            print(f"   Exists: {os.path.exists(abs_path)}")
            
            if os.path.exists(abs_path):
                print(f"   ✅ Found {exe_name}")
                return abs_path
            else:
                # Try path without extension (fallback)
                fallback_path = os.path.join(base_path, exiftool_dir, 'exiftool')
                if os.path.exists(fallback_path):
                    print(f"   ✅ Found exiftool (fallback)")
                    return fallback_path
                
                print(f"   ⚠️  {exe_name} not found")
                return abs_path
        else:
            # 开发环境路径
            # V3.9.3: 优先使用系统 exiftool（解决 ARM64/Intel 不兼容问题）
            import shutil
            system_exiftool = shutil.which('exiftool')
            if system_exiftool:
                print(f"🔍 Using system ExifTool: {system_exiftool}")
                return system_exiftool
            
            # 回退到项目目录下的 exiftool
            # 优先使用 main.py 注入的真实 app 根目录（补丁覆盖层兼容）
            project_parent = getattr(sys, '_SUPERPICKY_APP_ROOT',
                                     os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            project_root = os.path.join(project_parent, 'tools')
            print(f"🔍 Development environment detected")
            print(f"   project_root: {project_root}")
            print(f"   project_parent: {project_parent}")
            print(f"   is_windows: {is_windows}")
            print(f"   exe_name: {exe_name}")
            
            # 使用新的目录结构
            if is_windows:
                exiftool_dir = 'exiftools_win'
                # 尝试在项目根目录（父目录）中查找
                exiftool_path = os.path.join(project_parent, exiftool_dir, exe_name)
                print(f"   Windows path: {exiftool_path}")
                print(f"   Exists: {os.path.exists(exiftool_path)}")
            else:
                exiftool_dir = 'exiftools_mac'
                exiftool_path = os.path.join(project_parent, exiftool_dir, exe_name)
                print(f"   macOS path: {exiftool_path}")
                print(f"   Exists: {os.path.exists(exiftool_path)}")
            
            if os.path.exists(exiftool_path):
                print(f"   ✅ Found {exe_name} at {exiftool_path}")
                return exiftool_path
            
            # 如果新路径不存在，尝试旧路径（兼容性）
            if is_windows:
                win_path = os.path.join(project_parent, 'exiftool.exe')
                print(f"   Trying old Windows path: {win_path}")
                print(f"   Exists: {os.path.exists(win_path)}")
                if os.path.exists(win_path):
                    return win_path
            
            fallback_path = os.path.join(project_parent, 'exiftool')
            print(f"   Final fallback path: {fallback_path}")
            print(f"   Exists: {os.path.exists(fallback_path)}")
            return fallback_path


    def _verify_exiftool(self) -> bool:
        """验证exiftool是否可用"""
        print(f"\n🧪 Verifying ExifTool...")
        print(f"   Path: {self.exiftool_path}")
        print(f"   Test command: {self.exiftool_path} -ver")

        try:
            # V3.9.4: 在 Windows 上隐藏控制台窗口
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            result = subprocess.run(
                [self.exiftool_path, '-ver'],
                capture_output=True,
                text=False,  # 使用 bytes 模式，避免自动解码
                timeout=5,
                creationflags=creationflags,
                cwd=self._exiftool_cwd  # Windows: 使 exiftool.exe 能找到 exiftool_files 中的 DLL
            )
            print(f"   Return code: {result.returncode}")
            
            # 解码输出
            stdout_bytes = result.stdout
            stderr_bytes = result.stderr
            
            # 尝试多种编码解码
            decoded_stdout = None
            decoded_stderr = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    if stdout_bytes and decoded_stdout is None:
                        decoded_stdout = stdout_bytes.decode(encoding)
                    if stderr_bytes and decoded_stderr is None:
                        decoded_stderr = stderr_bytes.decode(encoding)
                except UnicodeDecodeError:
                    continue
            
            if decoded_stdout is None and stdout_bytes:
                decoded_stdout = stdout_bytes.decode('latin-1')
            if decoded_stderr is None and stderr_bytes:
                decoded_stderr = stderr_bytes.decode('latin-1')
            
            print(f"   stdout: {decoded_stdout.strip() if decoded_stdout else ''}")
            if decoded_stderr:
                print(f"   stderr: {decoded_stderr.strip()}")

            if result.returncode == 0:
                print(f"   ✅ ExifTool verified")
                return True
            else:
                print(f"   ❌ ExifTool returned non-zero exit code")
                return False

        except subprocess.TimeoutExpired:
            print(f"   ❌ ExifTool timeout (5s)")
            return False
        except Exception as e:
            print(f"   ❌ ExifTool error: {type(e).__name__}: {e}")
            return False

    def _start_process(self):
        """启动读/写两个常驻 ExifTool 进程 / Start both persistent processes"""
        self._read_proc.start()
        self._write_proc.start()

    def _stop_process(self):
        """停止读/写两个常驻进程 / Stop both persistent processes"""
        self._read_proc.stop()
        self._write_proc.stop()

    def shutdown(self):
        """关闭ExifTool管理器，停止所有相关进程"""
        global exiftool_manager
        with self._shutdown_lock:
            if self._is_shutdown:
                return
            self._is_shutdown = True
            with self._session_lock:
                self._persistent_session_count = 0

            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 ExifToolManager shutting down...")
            self._stop_process()
            if exiftool_manager is self:
                exiftool_manager = None
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ ExifToolManager shutdown completed")

    def __del__(self):
        """析构函数，确保进程被关闭"""
        try:
            self.shutdown()
        except Exception:
            pass

    def open_persistent_session(self, owner: str = "") -> None:
        """开启显式常驻会话，确保处理阶段内 ExifTool 进程保持存活。"""
        should_start = False
        with self._session_lock:
            self._persistent_session_count += 1
            should_start = self._persistent_session_count == 1

        if not should_start:
            return

        self._start_process()
        if not (self._read_proc.is_alive() and self._write_proc.is_alive()):
            with self._session_lock:
                self._persistent_session_count = max(0, self._persistent_session_count - 1)
            owner_suffix = f" ({owner})" if owner else ""
            raise RuntimeError(f"ExifTool persistent session start failed{owner_suffix}")

    def close_persistent_session(self, owner: str = "") -> None:
        """关闭显式常驻会话；最后一个会话结束时停止 ExifTool 进程。"""
        should_stop = False
        with self._session_lock:
            if self._persistent_session_count <= 0:
                return
            self._persistent_session_count -= 1
            should_stop = self._persistent_session_count == 0

        if should_stop:
            self._stop_process()

    def _send_command(self, args: List[str], timeout=30.0) -> bytes:
        """
        发送只读命令到 read 常驻进程并返回输出 (V4.5: 读写分离)。
        仅供读取类方法（read_metadata/extract_binary/_read_arw_structure）使用，
        写入类方法请走 _send_to_process（write 进程）。

        Send a read-only command to the persistent read process (V4.5 split).
        For read-style methods only; write-style methods must use
        _send_to_process (write process).
        """
        return self._read_proc.send_command(args, timeout)

    def _send_to_process(self, args: List[str], timeout=30.0) -> bool:
        """发送写入命令到 write 常驻进程并等待结果，返回成功标志"""
        try:
            output = self._write_proc.send_command(args, timeout)
            decoded = output.decode('utf-8', errors='replace')
            # 通过 exiftool 的实际更新计数判断成功，避免路径名含"Error"或
            # 同时出现 Warning+Error 时的误判
            if '1 image files updated' in decoded or '1 image files created' in decoded:
                return True
            if '0 image files updated' in decoded or '0 image files created' in decoded:
                return False
            # 回退：exiftool 未输出计数行时（如 XMP sidecar 创建），检查是否有 Error
            if 'Error' in decoded:
                return False
            return True
        except Exception:
            return False

    def _get_arw_write_mode(self, file_path: Optional[str] = None) -> str:
        """获取 ARW 写入策略；若传入 file_path 且为 ARW 则强制返回 sidecar（只写 XMP）。"""
        try:
            from advanced_config import get_advanced_config
            cfg = get_advanced_config()
            mode = cfg.get_arw_write_mode_for_file(file_path)
        except Exception:
            mode = "auto"
        mode = str(mode).strip().lower()
        if mode not in {"sidecar", "embedded", "inplace", "auto"}:
            mode = "auto"
        return mode

    def _get_metadata_write_mode(self) -> str:
        """获取全局元数据写入模式: embedded | sidecar | none"""
        try:
            from advanced_config import get_advanced_config
            cfg = get_advanced_config()
            mode = cfg.get_metadata_write_mode()
        except Exception:
            mode = "embedded"
        mode = str(mode).strip().lower()
        if mode not in {"embedded", "sidecar", "none"}:
            mode = "embedded"
        return mode

    def _read_arw_structure(self, file_path: str) -> Optional[Dict[str, any]]:
        """读取 ARW 关键结构标签，用于检测文件布局变化 (V4.0.6: 使用常驻进程)"""
        tags = [
            'PreviewImageStart',
            'ThumbnailOffset',
            'JpgFromRawStart',
            'StripOffsets',
            'HiddenDataOffset',
            'SR2SubIFDOffset',
            'FileSize'
        ]
        args = ['-json'] + [f'-{t}' for t in tags] + [file_path]
        try:
            output_bytes = self._send_command(args, timeout=10)
            if not output_bytes.strip():
                return None
            
            import json
            # 解码并提取 JSON 内容 (去掉 {ready})
            decoded = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    raw_decoded = output_bytes.decode(encoding)
                    # 提取 JSON 数组部分 [...]
                    import re
                    match = re.search(r'\[.*\]', raw_decoded, re.DOTALL)
                    if match:
                        decoded = match.group(0)
                        break
                except UnicodeDecodeError:
                    continue
            
            if decoded is None:
                return None
                
            data = json.loads(decoded)
            if not data:
                return None
            info = data[0]
            return {t: info.get(t) for t in tags}
        except Exception as e:
            # print(f"⚠️ _read_arw_structure failed: {e}")
            return None

    @staticmethod
    def _is_sidecar_raw(file_path: str) -> bool:
        """
        判断是否为「元数据强制走 XMP 侧车」的专有 RAW 格式（DNG 除外）。

        原先仅 ARW 强制侧车；现扩展到全部专有 RAW：不重写 RAW 本体既快
        （~190ms/张 → ~10ms/张）又安全，且符合 LR/C1 的侧车优先惯例。

        Whether this file is a proprietary RAW whose metadata must be
        written to an XMP sidecar (DNG excluded). Previously only ARW was
        forced; now all proprietary RAW formats are — skipping the body
        rewrite is faster (~190ms → ~10ms per photo), safer, and matches
        the LR/C1 sidecar-first convention.
        """
        return Path(file_path).suffix.lower() in SIDECAR_RAW_EXTENSIONS

    # exiftool 对无组前缀标签按内置优先级分组：以下裸标签名会落到 IPTC IIM。
    # 写这些标签需要 UTF-8 字符集声明（见 set_metadata），未列出的冷门 IPTC
    # 标签请显式写组前缀（如 'IPTC:FixtureIdentifier'）。
    # exiftool assigns bare tag names to groups by built-in priority; these
    # bare names resolve to IPTC IIM and need the UTF-8 charset declaration
    # (see set_metadata). For uncommon IPTC tags not listed here, pass an
    # explicit group prefix (e.g. 'IPTC:FixtureIdentifier').
    _IPTC_BARE_TAGS = {
        'caption-abstract', 'writer-editor', 'headline', 'keywords',
        'object-name', 'city', 'sub-location', 'province-state',
        'country-primarylocationname', 'country-primarylocationcode',
        'by-line', 'by-linetitle', 'credit', 'source', 'copyrightnotice',
        'specialinstructions', 'category', 'supplementalcategories',
    }

    @classmethod
    def _is_iptc_tag(cls, tag_name: str) -> bool:
        """
        判断标签是否写入 IPTC IIM（需要 UTF-8 字符集声明）。

        Whether the tag targets IPTC IIM (and therefore needs the UTF-8
        charset declaration when written).
        """
        group, sep, bare = tag_name.partition(':')
        if sep:
            return group.strip().lower().startswith('iptc')
        return tag_name.strip().lower() in cls._IPTC_BARE_TAGS

    def _write_metadata_subprocess(self, item: Dict[str, any], in_place: bool = False) -> bool:
        """写入元数据 (V4.0.6: 使用常驻进程)"""
        file_path = item.get('file')
        if not file_path or not os.path.exists(file_path):
            return False

        args = []
        if item.get('rating') is not None:
            args.append(f'-Rating={item["rating"]}')
        if item.get('pick') is not None:
            args.append(f'-XMP:Pick={item["pick"]}')
        if item.get('sharpness') is not None:
            args.append(f'-XMP:City={item["sharpness"]:06.2f}')
        if item.get('nima_score') is not None:
            args.append(f'-XMP:State={item["nima_score"]:05.2f}')
        if item.get('label') is not None:
            args.append(f'-XMP:Label={item["label"]}')
        if item.get('focus_status') is not None:
            args.append(f'-XMP:Country={item["focus_status"]}')
        # V4.2.7: IUCN 红色名录等级。借用 XMP-iptcCore:IntellectualGenre（"智识题材"
        # IPTC NewsCodes 体裁字段），同样冷门、LR/C1 主面板不显示、ExifTool 原生可写。
        # V4.2.7: IUCN Red List category in XMP-iptcCore:IntellectualGenre — another
        # rarely-used IPTC field invisible in LR/C1 main panels, writable natively.
        if item.get('iucn_category'):
            args.append(f'-XMP-iptcCore:IntellectualGenre={item["iucn_category"]}')
        # V4.2.7: GBIF 全球罕见度 0-100。借用 XMP-iptcExt:Event（IPTC Extension
        # 事件字段，LR/C1 主面板不显示，几乎无人手动写入）。
        # V4.2.7: GBIF global rarity 0-100. Uses XMP-iptcExt:Event — IPTC Extension
        # "Event" field, invisible in LR/C1 main panels, rarely used in the wild.
        if item.get('gbif_rarity_100') is not None:
            args.append(f'-XMP-iptcExt:Event={item["gbif_rarity_100"]:.2f}')
        # iRateBird 鸟种颜值 0-100。借用 XMP-iptcExt:AdditionalModelInformation
        # （IPTC Extension 附加模特信息字段，LR/C1 主面板不显示，几乎无人手动写入），
        # 与罕见度 Event 同命名空间的冷门扁平文本字段。
        # iRateBird species beauty 0-100 in XMP-iptcExt:AdditionalModelInformation —
        # an obscure flat IPTC Extension text field, invisible in LR/C1 main panels,
        # same namespace family as the rarity Event borrowing.
        if item.get('aesthetic_index') is not None:
            args.append(f'-XMP-iptcExt:AdditionalModelInformation={item["aesthetic_index"]:.2f}')
        temp_files: List[str] = []

        # 鸟名关键字(merge-add,Paul P1-1) / species keywords (merge-add)
        args.extend(self._keywords_args(item, file_path, temp_files))

        title = item.get('title')
        if title is not None:
            try:
                fd, title_tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_title_')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(str(title))
                temp_files.append(title_tmp_path)
                args.append(f'-XMP:Title<={title_tmp_path}')
            except Exception:
                args.append(f'-XMP:Title={title}')

        caption = item.get('caption')
        if caption is not None:
            try:
                fd, caption_tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_caption_')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(caption)
                temp_files.append(caption_tmp_path)
                args.append(f'-XMP:Description<={caption_tmp_path}')
            except Exception:
                args.append(f'-XMP:Description={caption}')

        # V4.3.0: 统一用 -overwrite_original_in_place（原地写，不创建 temp、不 rename）。
        # 旧的裸 -overwrite_original 在 ExFAT 外置盘/存储卡上 rename() 失败时会导致
        # 原文件被删后无法恢复（b6d4d77 当年只修了 EXIF reset，此写入路径残留隐患）。
        # _in_place 对所有格式安全，且保留文件 inode / Birth Time。in_place 形参保留以兼容调用。
        # V4.3.0: Always use -overwrite_original_in_place to avoid the ExFAT rename-failure
        # data loss that bare -overwrite_original can cause on memory cards.
        args.append('-overwrite_original_in_place')
        args.append(file_path)

        try:
            return self._send_to_process(args, timeout=60)
        except Exception as e:
            print(f"❌ ExifTool error: {e}")
            return False
        finally:
            for temp_path in temp_files:
                if not os.path.exists(temp_path):
                    continue
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _read_subject_list(self, file_path: str) -> List[str]:
        """
        读取文件现有 XMP-dc:Subject 关键字列表(经常驻读进程)。
        文件不存在/无标签/读取失败均返回空列表。

        Read the current XMP-dc:Subject list via the resident read
        process; returns [] when missing/absent/unreadable.
        """
        if not file_path or not os.path.exists(file_path):
            return []
        try:
            data = self.read_metadata(file_path, extra_args=['-XMP-dc:Subject']) or {}
        except Exception:
            return []
        subj = data.get('Subject')
        if subj is None:
            return []
        return [subj] if isinstance(subj, str) else [str(s) for s in subj]

    def _keywords_args(self, item: Dict[str, any], read_target: str,
                       temp_files: List[str]) -> List[str]:
        """
        为 item['keywords'](若有)生成 merge-add 写入参数。

        读 read_target 现有关键字 → merge_keyword_lists 合并 → 无新增返回 [];
        有新增则把完整列表以 ";;" 连接写入 UTF-8 临时文件,返回
        ['-sep', ';;', '-XMP-dc:Subject<=<tmp>'](临时文件挂入 temp_files
        由调用方统一清理)。整表赋值经临时文件,符合中文 UTF-8 铁律;
        exiftool 不支持 '+=<file' 追加(2026-07-12 实验验证),故用
        读-合并-整表回写实现幂等追加。

        Build merge-add write args for item['keywords']: read the current
        list from read_target, merge, and when something new is needed
        write the full ";;"-joined list through a UTF-8 temp file
        (-sep ';;' -XMP-dc:Subject<=tmp). exiftool has no '+=<file'
        append form (verified 2026-07-12), hence read-merge-rewrite.
        """
        keywords = item.get('keywords')
        if not keywords:
            return []
        merged = merge_keyword_lists(self._read_subject_list(read_target), keywords)
        if merged is None:
            return []
        try:
            fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_keywords_')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(';;'.join(merged))
            temp_files.append(tmp_path)
            return ['-sep', ';;', f'-XMP-dc:Subject<={tmp_path}']
        except Exception as e:
            print(f"⚠️ Keywords temp file failed: {e}, skip keywords write")
            return []

    def _write_metadata_xmp_sidecar(self, item: Dict[str, any]) -> bool:
        """写入 XMP 侧车文件 (V4.0.6: 使用常驻进程)"""
        file_path = item.get('file')
        if not file_path:
            return False
        xmp_path = os.path.splitext(file_path)[0] + '.xmp'
        self._cleanup_sidecar_tmp(xmp_path)

        args = [] # 使用常驻进程的参数模式

        if item.get('rating') is not None:
            args.append(f'-XMP:Rating={item["rating"]}')
        if item.get('pick') is not None:
            args.append(f'-XMP:Pick={item["pick"]}')
        if item.get('sharpness') is not None:
            args.append(f'-XMP:City={item["sharpness"]:06.2f}')
        if item.get('nima_score') is not None:
            args.append(f'-XMP:State={item["nima_score"]:05.2f}')
        if item.get('label') is not None:
            args.append(f'-XMP:Label={item["label"]}')
        if item.get('focus_status') is not None:
            args.append(f'-XMP:Country={item["focus_status"]}')
        # V4.2.7: IUCN 等级同样写入侧车 / IUCN category mirrored into sidecar
        if item.get('iucn_category'):
            args.append(f'-XMP-iptcCore:IntellectualGenre={item["iucn_category"]}')
        # V4.2.7: GBIF 罕见度同样写入侧车 / GBIF rarity mirrored into sidecar
        if item.get('gbif_rarity_100') is not None:
            args.append(f'-XMP-iptcExt:Event={item["gbif_rarity_100"]:.2f}')
        # iRateBird 鸟种颜值 0-100，同样写入侧车。借用 XMP-iptcExt:AdditionalModelInformation
        # （IPTC Extension 附加模特信息字段，LR/C1 主面板不显示，几乎无人手动写入），
        # 与罕见度 Event 同命名空间的冷门扁平文本字段。
        # iRateBird species beauty 0-100 mirrored into sidecar, using
        # XMP-iptcExt:AdditionalModelInformation — an obscure flat IPTC Extension
        # text field, invisible in LR/C1 main panels, same namespace family as
        # the rarity Event borrowing.
        if item.get('aesthetic_index') is not None:
            args.append(f'-XMP-iptcExt:AdditionalModelInformation={item["aesthetic_index"]:.2f}')
        temp_files: List[str] = []

        # 鸟名关键字:侧车存在读侧车,否则读文件本体(LR 惯例侧车优先)
        # Species keywords: read the sidecar when present, else the file
        # itself (LR gives sidecars precedence for proprietary RAW).
        kw_read_target = xmp_path if os.path.exists(xmp_path) else file_path
        args.extend(self._keywords_args(item, kw_read_target, temp_files))

        # UTF-8 temp file for Title/Caption
        title = item.get('title')
        if title is not None:
            try:
                fd, title_tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_title_')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(str(title))
                temp_files.append(title_tmp_path)
                args.append(f'-XMP:Title<={title_tmp_path}')
            except Exception as e:
                args.append(f'-XMP:Title={title}')

        caption = item.get('caption')
        if caption is not None:
            try:
                fd, caption_tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_caption_')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(caption)
                temp_files.append(caption_tmp_path)
                args.append(f'-XMP:Description<={caption_tmp_path}')
            except Exception as e:
                args.append(f'-XMP:Description={caption}')

        args.extend(['-overwrite_original_in_place', xmp_path])  # V4.3.0: ExFAT 安全，避免 rename 删文件

        try:
            success = self._send_to_process(args, timeout=30)
            return success
        except Exception as e:
            print(f"❌ XMP sidecar write error: {e}")
            return False
        finally:
            for temp_path in temp_files:
                if not os.path.exists(temp_path):
                    continue
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    @staticmethod
    def _cleanup_sidecar_tmp(xmp_path: str) -> None:
        """
        清理侧车残留的 exiftool 临时文件。

        exiftool 异常退出会遗留 {xmp}_exiftool_tmp，之后对该侧车的所有写入
        都报 "Temporary file already exists" 静默失败（cleanup_temp_files
        只按原文件路径清理，覆盖不到侧车路径）。侧车 tmp 是纯 scratch 文件，
        无论侧车本体是否存在都应删除。

        Remove a leftover {xmp}_exiftool_tmp scratch file. When exiftool
        exits abnormally it leaves this behind, and every later write to
        that sidecar fails with "Temporary file already exists"
        (cleanup_temp_files only covers the original file's path, not the
        sidecar's). Safe to delete regardless of whether the sidecar exists.
        """
        tmp_path = f"{xmp_path}_exiftool_tmp"
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                print(f"🧹 Cleaned up residual sidecar temp file: {tmp_path}")
            except OSError as e:
                print(f"⚠️ Failed to clean sidecar temp file: {tmp_path} - {e}")

    def _reset_xmp_sidecar(self, file_path: str) -> bool:
        """清理 XMP 侧车中的评分相关字段 (V4.0.6: 使用常驻进程)"""
        xmp_path = os.path.splitext(file_path)[0] + '.xmp'
        self._cleanup_sidecar_tmp(xmp_path)
        if not os.path.exists(xmp_path):
            return True

        args = [
            '-XMP:Rating=',
            '-XMP:Pick=',
            '-XMP:Label=',
            '-XMP:City=',
            '-XMP:State=',
            '-XMP:Country=',
            '-XMP:Description=',
            '-XMP:Title=',
            '-XMP-iptcCore:IntellectualGenre=',
            '-XMP-iptcExt:Event=',
            '-overwrite_original_in_place',  # V4.3.0: ExFAT 安全，避免 rename 删文件
            xmp_path
        ]
        try:
            return self._send_to_process(args, timeout=30)
        except Exception as e:
            print(f"❌ XMP sidecar reset error: {e}")
            return False

    def _write_metadata_arw(self, item: Dict[str, any]) -> bool:
        """ARW 写入策略（embedded / inplace / sidecar / auto）；ARW 格式强制走 sidecar（XMP）"""
        file_path = item.get('file')
        mode = self._get_arw_write_mode(file_path)
        if not file_path or not os.path.exists(file_path):
            return False

        if mode == 'sidecar':
            return self._write_metadata_xmp_sidecar(item)
        if mode == 'embedded':
            return self._write_metadata_subprocess(item, in_place=False)
        if mode == 'inplace':
            return self._write_metadata_subprocess(item, in_place=True)

        # auto: 尝试 in-place 写入，若检测到结构变化则回退 sidecar
        original_struct = self._read_arw_structure(file_path)
        if original_struct is None:
            return self._write_metadata_xmp_sidecar(item)

        # 在与原文件相同目录创建临时文件，确保 os.replace 是同目录 rename（保留 Birth Time）
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=Path(file_path).suffix, dir=os.path.dirname(file_path))
        os.close(tmp_fd)
        try:
            shutil.copy2(file_path, tmp_path)
            tmp_item = dict(item)
            tmp_item['file'] = tmp_path
            ok = self._write_metadata_subprocess(tmp_item, in_place=True)
            if not ok:
                return self._write_metadata_xmp_sidecar(item)

            new_struct = self._read_arw_structure(tmp_path)
            if new_struct is None or new_struct != original_struct:
                return self._write_metadata_xmp_sidecar(item)

            os.replace(tmp_path, file_path)
            return True
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def set_rating_and_pick(
        self,
        file_path: str,
        rating: int,
        pick: int = 0,
        sharpness: float = None,
        nima_score: float = None
    ) -> bool:
        """
        设置照片评分和旗标 (Lightroom标准)

        Args:
            file_path: 文件路径
            rating: 评分 (-1=拒绝, 0=无评分, 1-5=星级)
            pick: 旗标 (-1=排除旗标, 0=无旗标, 1=精选旗标)
            sharpness: 锐度值（可选，写入IPTC:City字段，用于Lightroom排序）
            nima_score: NIMA美学评分（可选，写入IPTC:Province-State字段）
            # V3.2: 移除 brisque_score 参数

        Returns:
            是否成功
        """
        if not os.path.exists(file_path):
            print(f"❌ File not found: {file_path}")
            return False

        # 专有 RAW 强制走 XMP 侧车路由 / proprietary RAW routes to XMP sidecar
        if self._is_sidecar_raw(file_path):
            item = {
                'file': file_path,
                'rating': rating,
                'pick': pick,
                'sharpness': sharpness,
                'nima_score': nima_score
            }
            return self._write_metadata_arw(item)

        # V4.0.5: 使用常驻进程处理单文件更新
        args = []
        
        # Rating
        args.append(f'-Rating={rating}')
        
        # Pick
        args.append(f'-XMP:Pick={pick}')
        
        # Sharpness -> XMP:City
        if sharpness is not None:
            args.append(f'-XMP:City={sharpness:06.2f}')
            
        # NIMA -> XMP:State
        if nima_score is not None:
            args.append(f'-XMP:State={nima_score:05.2f}')
        
        # 文件路径
        args.append(file_path)
        
        # 选项（in_place 保留文件 Birth Time，不创建新 inode）
        args.append('-overwrite_original_in_place')

        try:
            # 发送命令
            success = self._send_to_process(args)
            
            if success:
                filename = os.path.basename(file_path)
                pick_desc = {-1: "rejected", 0: "none", 1: "picked"}.get(pick, str(pick))
                sharpness_info = f", Sharp={sharpness:06.2f}" if sharpness is not None else ""
                nima_info = f", NIMA={nima_score:05.2f}" if nima_score is not None else ""
                # print(f"✅ EXIF updated: {filename} (Rating={rating}, Pick={pick_desc}{sharpness_info}{nima_info})")
            
            return success

        except Exception as e:
            print(f"❌ Error setting rating/pick: {e}")
            return False

    def set_metadata(self, file_path: str, tags: Dict[str, str]) -> bool:
        """
        通用单文件元数据写入（V4.5，走 write 常驻进程）。

        供 LR 插件服务器（birdid_server 的 /exif/write-title、/exif/write-caption）
        和 birdid_cli 的 --write-exif 使用；此前两处调用的本方法并不存在（历史
        重构中遗失），会抛 AttributeError 被上层 except 吞掉，功能一直失效。

        写入策略与批量写路径保持一致：
        - 全局 metadata_write_mode == "none" → 跳过写入并返回 True（用户配置意图）；
        - ARW 或全局 sidecar 模式 → 只写 XMP 侧车，不动 RAW 本体；
        - 所有值一律经 UTF-8 临时文件重定向（-TAG<=tmp.txt），满足仓库
          「ExifTool 中文元数据必须走 UTF-8 临时文件」的硬性约定，
          同时避免值中的换行破坏 -@ 参数流；
        - 涉及 IPTC 标签（如 Caption-Abstract）时自动附加
          -charset iptc=utf8 与 -CodedCharacterSet=UTF8，避免中文按
          Latin-1 存储/误读（IPTC IIM 默认字符集陷阱）。

        参数 / Parameters:
            file_path (str): 目标文件路径 / target file path
            tags (Dict[str, str]): 标签名→值，如 {'Title': '黑尾塍鹬 (Black-tailed Godwit)'}。
                标签名可带组前缀（如 'XMP:Title'）；侧车模式下无组前缀自动补 XMP:。

        返回 / Returns:
            bool: 写入成功（或按配置跳过）为 True，失败为 False（不抛异常）。

        Generic single-file metadata write routed to the persistent write
        process. Used by the LR-plugin server endpoints and birdid_cli; this
        method was previously missing (lost in a refactor) so those callers
        always failed with a swallowed AttributeError. Policy matches the
        batch path: honor metadata_write_mode "none", force XMP sidecar for
        ARW / sidecar mode, and write every value via a UTF-8 temp file.
        """
        if not file_path or not os.path.exists(file_path) or not tags:
            return False
        # 同 read_metadata：常驻进程的 cwd 是 exiftool 二进制目录，相对路径
        # 会在此处的 exists 检查通过却在 exiftool 侧解析失败。
        # As in read_metadata: the persistent process runs with the exiftool
        # binary's cwd, so a relative path passes the check above yet fails to
        # resolve on the exiftool side.
        file_path = os.path.abspath(file_path)

        global_mode = self._get_metadata_write_mode()
        if global_mode == "none":
            print(f"[ExifTool] metadata_write_mode=none, 跳过 set_metadata: {os.path.basename(file_path)}")
            return True

        # 专有 RAW 一律只写 XMP 侧车；全局 sidecar 模式下所有格式也走侧车
        # Proprietary RAW always goes to the XMP sidecar; global sidecar mode does too
        use_sidecar = self._is_sidecar_raw(file_path) or global_mode == "sidecar"

        # 侧车只支持 XMP 组：IPTC 题注映射到 XMP:Description，无组前缀的标签补 XMP:
        # Sidecars are XMP-only: map the IPTC caption tag to XMP:Description
        # and add the XMP: group to bare tag names
        sidecar_alias = {'caption-abstract': 'XMP:Description'}

        target_path = os.path.splitext(file_path)[0] + '.xmp' if use_sidecar else file_path
        if use_sidecar:
            self._cleanup_sidecar_tmp(target_path)

        args: List[str] = []
        temp_files: List[str] = []
        writes_iptc = False
        try:
            for tag, value in tags.items():
                if value is None:
                    continue
                tag_name = str(tag).strip()
                if use_sidecar:
                    tag_name = sidecar_alias.get(tag_name.lower(), tag_name)
                    if ':' not in tag_name:
                        tag_name = f'XMP:{tag_name}'
                elif self._is_iptc_tag(tag_name):
                    writes_iptc = True
                fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_tag_')
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(str(value))
                temp_files.append(tmp_path)
                args.append(f'-{tag_name}<={tmp_path}')

            if not args:
                return False

            # IPTC IIM 默认字符集是 Latin-1：写 IPTC 标签必须声明 UTF-8 并写入
            # CodedCharacterSet 标记，否则中文按 Latin-1 存储变 '?'，或存成
            # UTF-8 字节但被第三方软件（LR 等）按 Latin-1 误读（已实测验证）。
            # XMP 原生 UTF-8，无此问题。
            # IPTC IIM defaults to Latin-1: when writing IPTC tags we must both
            # declare UTF-8 and write the CodedCharacterSet marker, otherwise
            # Chinese becomes '?' (or unmarked UTF-8 bytes that third-party
            # readers such as LR decode as Latin-1). Verified empirically.
            # XMP is natively UTF-8 and unaffected.
            if writes_iptc:
                args = ['-charset', 'iptc=utf8', '-CodedCharacterSet=UTF8'] + args

            # -overwrite_original_in_place 与其余写入路径一致（ExFAT 安全，保留 inode）
            # Same flag as every other write path (ExFAT-safe, keeps the inode)
            args.extend(['-overwrite_original_in_place', target_path])
            return self._send_to_process(args, timeout=30)
        except Exception as e:
            print(f"❌ set_metadata error [{os.path.basename(file_path)}]: {e}")
            return False
        finally:
            for tmp_path in temp_files:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    def batch_set_metadata(
        self,
        files_metadata: List[Dict[str, any]]
    ) -> Dict[str, int]:
        """
        批量设置元数据（使用-execute分隔符，支持不同文件不同参数）

        Args:
            files_metadata: 文件元数据列表
                [
                    {'file': 'path1.NEF', 'rating': 3, 'pick': 1, 'sharpness': 95.3, 'nima_score': 7.5, 'label': 'Green', 'focus_status': '精准'},
                    {'file': 'path2.NEF', 'rating': 2, 'pick': 0, 'sharpness': 78.5, 'nima_score': 6.8, 'focus_status': '偏移'},
                    {'file': 'path3.NEF', 'rating': -1, 'pick': -1, 'sharpness': 45.2, 'nima_score': 5.2},
                ]
                # V3.4: 添加 label 参数（颜色标签，如 'Green' 用于飞鸟）
                # V3.9: 添加 focus_status 参数（对焦状态）

        Returns:
            统计结果 {'success': 成功数, 'failed': 失败数}
        """
        stats = {'success': 0, 'failed': 0}

        # 全局写入模式检查
        global_mode = self._get_metadata_write_mode()
        if global_mode == "none":
            print("[ExifTool] metadata_write_mode=none, 跳过所有元数据写入")
            return stats
        if global_mode == "sidecar":
            print(f"[ExifTool] metadata_write_mode=sidecar, 所有文件统一写 XMP 侧车 ({len(files_metadata)} 条)")
            for item in files_metadata:
                if self._write_metadata_xmp_sidecar(item):
                    stats['success'] += 1
                else:
                    stats['failed'] += 1
            return stats

        caption_temp_files: List[str] = []  # 用于写入 caption/title 的临时 UTF-8 文件，执行后删除
        num_with_caption = sum(1 for it in files_metadata if it.get('caption'))

        # 前置日志：批量写入前先给出反馈，避免大批量时看起来像卡住
        print(
            f"[ExifTool] preparing batch_set_metadata: {len(files_metadata)} 条, "
            f"其中 {num_with_caption} 条带 caption"
        )

        # V4.0.3: 预先清理可能存在的残留 _exiftool_tmp 文件，防止 ExifTool 报错
        # "Error: Temporary file already exists"
        files_to_process = [item['file'] for item in files_metadata]
        self.cleanup_temp_files(files_to_process)

        # 诊断：本次调用有多少条带 caption（若无则不会出现 [ExifTool Caption] 详细日志）
        print(f"[ExifTool] batch_set_metadata: {len(files_metadata)} 条, 其中 {num_with_caption} 条带 caption")

        # 专有 RAW 逐条走侧车写入；其余（JPG/DNG/HEIF）走常驻进程批量写本体
        # Proprietary RAW items go to per-item sidecar writes; the rest
        # (JPG/DNG/HEIF) use the persistent-process embedded batch path.
        arw_items = [it for it in files_metadata if self._is_sidecar_raw(it.get('file', ''))]
        other_items = [it for it in files_metadata if it not in arw_items]

        for item in arw_items:
            if not os.path.exists(item.get('file', '')):
                stats['failed'] += 1
                continue
            if self._write_metadata_arw(item):
                stats['success'] += 1
            else:
                stats['failed'] += 1

        # V4.0.5: 非 ARW 使用常驻进程处理提升速度
        # 构建参数列表 (每行一个参数)
        args_list = []
        other_missing = 0
        
        for item in other_items:
            file_path = item['file']
            if not os.path.exists(file_path):
                other_missing += 1
                continue
                
            # Rating
            if item.get('rating') is not None:
                args_list.append(f'-Rating={item["rating"]}')
            
            # Pick
            if item.get('pick') is not None:
                args_list.append(f'-XMP:Pick={item["pick"]}')
            
            # Sharpness -> XMP:City
            if item.get('sharpness') is not None:
                args_list.append(f'-XMP:City={item["sharpness"]:06.2f}')
                
            # NIMA -> XMP:State
            if item.get('nima_score') is not None:
                args_list.append(f'-XMP:State={item["nima_score"]:05.2f}')
            
            # Label
            if item.get('label') is not None:
                args_list.append(f'-XMP:Label={item["label"]}')
                
            # Focus Status -> XMP:Country
            if item.get('focus_status') is not None:
                args_list.append(f'-XMP:Country={item["focus_status"]}')

            # V4.2.7: IUCN 红色名录等级。借用 XMP-iptcCore:IntellectualGenre（同上，
            # 与 _write_metadata_subprocess/_write_metadata_xmp_sidecar 保持一致）。
            # 补丁说明：本内联批量路径（默认 embedded 模式下非 ARW 文件的实际写入路径）
            # 此前遗漏了 iucn/gbif/aesthetic 三个字段，导致这些值从未写入默认模式下
            # 大多数照片（非 ARW）的 XMP，现补齐与另外两处写入路径一致。
            # V4.2.7: IUCN Red List category in XMP-iptcCore:IntellectualGenre —
            # kept consistent with _write_metadata_subprocess/_write_metadata_xmp_sidecar.
            # Patch note: this inlined batch path (the actual write path for non-ARW
            # files under the default "embedded" mode) previously omitted the
            # iucn/gbif/aesthetic fields entirely, so they never reached XMP for most
            # photos; now aligned with the other two write paths.
            if item.get('iucn_category'):
                args_list.append(f'-XMP-iptcCore:IntellectualGenre={item["iucn_category"]}')
            # V4.2.7: GBIF 全球罕见度 0-100 -> XMP-iptcExt:Event
            # V4.2.7: GBIF global rarity 0-100 -> XMP-iptcExt:Event
            if item.get('gbif_rarity_100') is not None:
                args_list.append(f'-XMP-iptcExt:Event={item["gbif_rarity_100"]:.2f}')
            # iRateBird 鸟种颜值 0-100 -> XMP-iptcExt:AdditionalModelInformation
            # iRateBird species beauty 0-100 -> XMP-iptcExt:AdditionalModelInformation
            if item.get('aesthetic_index') is not None:
                args_list.append(f'-XMP-iptcExt:AdditionalModelInformation={item["aesthetic_index"]:.2f}')

            # 鸟名关键字(merge-add,Paul P1-1) / species keywords (merge-add)
            args_list.extend(self._keywords_args(item, file_path, caption_temp_files))

            # Title（使用临时 UTF-8 文件，与 Caption 保持一致，避免非 ASCII 编码风险）
            if item.get('title') is not None:
                try:
                    fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_title_')
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(item['title'])
                    caption_temp_files.append(tmp_path)
                    args_list.append(f'-XMP:Title<={tmp_path}')
                except Exception as e:
                    print(f"⚠️ Title temp file failed: {e}, fallback to inline")
                    args_list.append(f'-XMP:Title={item["title"]}')
                
            # Caption (使用临时 UTF-8 文件，避免换行破坏 -@ 参数流)
            caption = item.get('caption')
            if caption is not None:
                try:
                    fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_caption_')
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(caption)
                    caption_temp_files.append(tmp_path)
                    args_list.append(f'-XMP:Description<={tmp_path}')
                except Exception as e:
                    print(f"⚠️ Caption temp file failed: {e}, fallback to inline")
                    args_list.append(f'-XMP:Description={caption}')

            # 文件路径
            args_list.append(file_path)
            
            # 每个文件执行一次 (相当于 -execute)
            args_list.append('-execute')
        
        stats['failed'] += other_missing

        if not args_list:
            return stats

        # 从列表末尾移除多余的 -execute (因为 _send_to_process 会自动添加最后的 -execute)
        # 不，_send_to_process 添加的是针对这一批次指令的结束符
        # ExifTool -stay_open 模式下，每个 -execute 对应一次处理
        # 我们可以把这一大批指令一次性发过去
        
        # 修正：我们需要把 args_list 连接起来，最后再由 _send_to_process 发送
        # 但是 _send_to_process 目前设计是发一次 -execute
        
        # 让我们修改一下策略：
        # ExifTool 文档说：Send a series of commands ... terminated by -execute
        # 如果我们发送多个文件操作，每个后面跟 -execute，exiftool 会依次处理
        # 最后我们需要等待所有处理完成。
        
        # 简化策略验证：每个文件操作都单独送入 _send_to_process 太慢了吗？
        # 不，还是批量送入比较好。
        
        # 让我们把 _send_to_process 改名为 _send_raw_command 更贴切
        
        num_executes = 0
        total_timeout = 30.0
        # V4.5: 批量写只占用 write 进程；read 进程保持空闲，主流程的
        # 对焦点/ISO/GPS 读取不再被整批写入持锁阻塞。
        # V4.5: The batch only occupies the write process; the read process
        # stays free so focus/ISO/GPS reads are no longer blocked for the
        # whole batch duration.
        wp = self._write_proc
        try:
            with wp.lock:
                wp.start()
                if not wp.process:
                    raise Exception("Process not started")

                cmd_str = '\n'.join(args_list) + '\n' # 注意这里不加 -execute，因为 args_list 里已经包含了 N 个 -execute

                # 写入大量数据
                wp.process.stdin.write(cmd_str.encode('utf-8'))
                wp.process.stdin.flush()

                # 读取输出：我们需要读取 N 次 {ready}
                num_executes = args_list.count('-execute')
                # 按文件数线性放大超时
                total_timeout = max(30.0, num_executes * 5.0)
                start_time = time.time()

                error_count = 0
                for _ in range(num_executes):
                    elapsed = time.time() - start_time
                    remaining = total_timeout - elapsed
                    if remaining <= 0:
                        raise TimeoutError(f"Batch timeout after {total_timeout}s")

                    # 读取一次 {ready}
                    output = wp.read_until_ready(timeout=remaining)

                    # 简单的错误检测 (累积)
                    decoded = output.decode('utf-8', errors='replace')
                    if "Error" in decoded and "Warning" not in decoded:
                        error_count += 1

                stats['success'] += num_executes - error_count
                stats['failed'] += error_count

        except TimeoutError:
            print(f"❌ Batch ExifTool timeout (>{total_timeout}s)")
            wp.stop()
            stats['failed'] += num_executes
        except Exception as e:
            print(f"❌ Batch persistent error: {e}")
            wp.stop()
            stats['failed'] += num_executes
        finally:
            for tmp_path in caption_temp_files:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception as e:
                    print(f"⚠️ Caption temp file cleanup failed: {tmp_path} - {e}")

        # RAF/ORF 补侧车已冗余：专有 RAW（含 RAF/ORF）现统一走侧车路由，
        # 上面的 arw_items 分支已写过侧车，再调用会重复写同一批文件。
        # The RAF/ORF sidecar backfill is now redundant: proprietary RAW
        # (incl. RAF/ORF) routes through the sidecar branch above, so calling
        # _create_xmp_sidecars_for_raf here would double-write those files.

        return stats
        
    def cleanup_temp_files(self, file_paths: List[str]):
        """
        清理由于 ExifTool 异常退出可能残留的 _exiftool_tmp 文件
        只有当原文件存在且大小时才删除临时文件
        """
        for path in file_paths:
            tmp_path = f"{path}_exiftool_tmp"
            if os.path.exists(tmp_path):
                # 只有当原文件存在时才删除临时文件
                if os.path.exists(path):
                    try:
                        os.remove(tmp_path)
                        print(f"🧹 Cleaned up residual temp file: {tmp_path}")
                    except OSError as e:
                        print(f"⚠️ Failed to clean temp file: {tmp_path} - {e}")
                else:
                    print(f"⚠️ Original file missing, keeping temp file: {tmp_path}")
    
    def _create_xmp_sidecars_for_raf(self, files_metadata: List[Dict[str, any]]):
        """
        V3.9.2: 为 RAF/ORF 等需要侧车文件的格式创建 XMP 文件
        
        Lightroom 可以读取嵌入在大多数 RAW 格式中的 XMP，
        但 Fujifilm RAF 需要单独的 .xmp 侧车文件
        """
        needs_sidecar_extensions = {'.raf', '.orf'}  # Fujifilm, Olympus
        
        for item in files_metadata:
            file_path = item.get('file', '')
            if not file_path:
                continue
            
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in needs_sidecar_extensions:
                continue
            
            # 构建 XMP 侧车文件路径
            xmp_path = os.path.splitext(file_path)[0] + '.xmp'
            
            try:
                # 使用常驻进程从 RAW 文件提取 XMP 到侧车文件 (V4.0.6: 使用常驻进程)
                args = [
                    '-o', xmp_path,
                    '-TagsFromFile', file_path,
                    '-XMP:all<XMP:all'
                ]
                self._send_to_process(args, timeout=30)
            except Exception:
                pass  # 侧车文件创建失败不影响主流程

    def read_metadata(self, file_path: str, extra_args: List[str] = None) -> Optional[Dict]:
        """
        读取文件的元数据 (V4.0.7: 使用常驻进程，支持 extra_args)

        路径必须转成绝对路径后再交给常驻进程：该进程的 cwd 被设为 exiftool
        二进制所在目录（Windows 需在此找 DLL，见 __init__ 中 _exiftool_cwd），
        与 Python 进程的 cwd 不同。相对路径会在 os.path.exists 这一关通过
        （用 Python 的 cwd 判断），却在 exiftool 侧找不到文件而静默返回空 —
        CLI 传相对目录时曾因此让整条 GPS/地理过滤链路失效。

        The path must be absolute before reaching the persistent process: its
        cwd is the exiftool binary's directory (needed on Windows to locate the
        DLLs — see _exiftool_cwd in __init__), which differs from the Python
        process cwd. A relative path passes the os.path.exists check here (which
        uses Python's cwd) but silently resolves to nothing on the exiftool
        side — this once disabled the whole GPS / geo-filter chain whenever the
        CLI was given a relative directory.
        """
        if not os.path.exists(file_path):
            return None
        file_path = os.path.abspath(file_path)

        # 如果没有指定额外的参数，则默认读取几个常用评分标签
        if extra_args is None:
            args = [
                '-Rating',
                '-XMP:Pick',
                '-XMP:Label',
                '-IPTC:City',
                '-IPTC:Country-PrimaryLocationName',
                '-IPTC:Province-State',
                '-json',
                file_path
            ]
        else:
            args = ['-json'] + extra_args + [file_path]

        try:
            output_bytes = self._send_command(args, timeout=15)
            if not output_bytes.strip():
                return None
            
            import json
            # 解码并提取 JSON 内容
            decoded = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    raw_decoded = output_bytes.decode(encoding)
                    import re
                    match = re.search(r'\[.*\]', raw_decoded, re.DOTALL)
                    if match:
                        decoded = match.group(0)
                        break
                except UnicodeDecodeError:
                    continue
            
            if decoded is None:
                return None
                
            data = json.loads(decoded)
            return data[0] if data else None

        except Exception as e:
            # print(f"❌ Read metadata failed: {e}")
            return None

    def extract_binary(self, file_path: str, tag: str) -> Optional[bytes]:
        """
        利用常驻进程提取二进制数据 (V4.0.7)
        例如: tag='-JpgFromRaw' 或 tag='-PreviewImage'
        """
        if not os.path.exists(file_path):
            return None

        # 需要 -b 模式，且提取二进制时不带 JSON/execute 等混淆
        # 注意：_send_command 会自动追加 -execute，这在 -b 模式下可能导致输出混乱
        # 但 exiftool 持久模式必须靠 -execute 分隔。
        # ExifTool -b 模式下，-execute 会输出 "{ready}\n" 字符串，这可以被 _read_until_ready 正确处理
        args = ['-b', tag, file_path]
        try:
            # _send_command 返回的是直到 {ready} 的原始字节
            raw_output = self._send_command(args, timeout=20)
            if not raw_output:
                return None
            
            # 二进制模式下，输出末尾会多出 "\n{ready}\n" (由 _send_command/exiftool 产生)
            # 我们需要移除这个尾巴。
            ready_marker = b"\n{ready}\n"
            if raw_output.endswith(ready_marker):
                return raw_output[:-len(ready_marker)]
            
            # 有时可能只有 {ready} 没有前置换行
            ready_marker_short = b"{ready}\n"
            if raw_output.endswith(ready_marker_short):
                return raw_output[:-len(ready_marker_short)]
                
            return raw_output
        except Exception as e:
            print(f"❌ Error extracting binary: {e}")
            return None

    def copy_exif(self, src_path: str, dst_path: str) -> bool:
        """
        用 -TagsFromFile 把 src 的 EXIF 复制到 dst(裁剪导出用)。
        跳过会与裁剪结果冲突的尺寸/方向标签,避免导出图被错误旋转或标错尺寸。

        Copy EXIF from src onto dst via -TagsFromFile (for crop export); skip
        orientation/size tags that would conflict with the cropped output.

        参数 / Parameters:
            src_path (str): 原图路径。
            dst_path (str): 导出图路径(将被写入 EXIF)。

        返回 / Returns:
            bool: 成功为 True,失败为 False(不抛异常)。
        """
        import subprocess
        if not (os.path.exists(src_path) and os.path.exists(dst_path)):
            return False
        try:
            subprocess.run(
                [self.exiftool_path, "-overwrite_original", "-TagsFromFile", src_path,
                 "-all:all", "-x", "Orientation", "-x", "ImageWidth", "-x", "ImageHeight",
                 dst_path],
                check=True, capture_output=True, cwd=self._exiftool_cwd,
            )
            return True
        except Exception as e:
            print(f"⚠️ copy_exif failed: {e}")
            return False

    def reset_metadata(self, file_path: str) -> bool:
        """重置照片的评分和旗标 (V4.0.6: 使用常驻进程)"""
        if not os.path.exists(file_path):
            print(f"❌ File not found: {file_path}")
            return False

        # 专有 RAW 强制只清 XMP 侧车，不修改 RAW 本体
        if self._is_sidecar_raw(file_path):
            return self._reset_xmp_sidecar(file_path)

        # 删除Rating、Pick、City、Country和Province-State字段
        args = [
            '-Rating=',
            '-XMP:Pick=',
            '-XMP:Label=',
            '-XMP:City=',
            '-XMP:State=',
            '-XMP:Country=',
            '-XMP:Title=',
            '-IPTC:City=',
            '-IPTC:Country-PrimaryLocationName=',
            '-IPTC:Province-State=',
            '-overwrite_original_in_place',
            file_path
        ]

        try:
            success = self._send_to_process(args, timeout=30)
            if success:
                filename = os.path.basename(file_path)
                # print(f"✅ EXIF reset: {filename}")
            return success
        except Exception as e:
            print(f"❌ ExifTool error: {e}")
            return False

    def batch_reset_metadata(self, file_paths: List[str], batch_size: int = 50, log_callback=None, i18n=None) -> Dict[str, int]:
        """
        批量重置元数据（强制清除所有EXIF评分字段）

        Args:
            file_paths: 文件路径列表
            batch_size: 每批处理的文件数量（默认50，避免命令行过长）
            log_callback: 日志回调函数（可选，用于UI显示）
            i18n: I18n instance for internationalization (optional)

        Returns:
            统计结果 {'success': 成功数, 'failed': 失败数}
        """
        def log(msg):
            """统一日志输出"""
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        stats = {'success': 0, 'failed': 0}
        total = len(file_paths)

        if i18n:
            log(i18n.t("logs.batch_reset_start", total=total))
        else:
            log(f"📦 Starting EXIF reset for {total} files...")
            log(f"   Clearing all rating fields\n")

        # 分批处理
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_files = file_paths[batch_start:batch_end]

            # 过滤不存在的文件
            valid_files = [f for f in batch_files if os.path.exists(f)]
            stats['failed'] += len(batch_files) - len(valid_files)

            if not valid_files:
                continue

            # V4.0.3: 预先清理可能存在的残留 _exiftool_tmp 文件
            self.cleanup_temp_files(valid_files)

            # 专有 RAW 强制只清理 XMP 侧车，不修改 RAW 本体
            arw_files = [f for f in valid_files if self._is_sidecar_raw(f)]
            if arw_files:
                for f in arw_files:
                    if self._reset_xmp_sidecar(f):
                        stats['success'] += 1
                    else:
                        stats['failed'] += 1
                valid_files = [f for f in valid_files if f not in arw_files]
                if not valid_files:
                    continue

            # 构建单次批量 execute：所有文件共用一个 -execute，避免 N 次往返。
            # 标志位列出一次，所有文件路径追加在后，最后跟 -execute。
            # -overwrite_original_in_place 已在 _start_process 的 -common_args 中；
            # 此处不重复，减少参数量。
            # Build a single -execute for all files to avoid N round-trips.
            # Flags are listed once; all file paths follow; one -execute at the end.
            batch_args = [
                '-Rating=',
                '-XMP:Pick=',
                '-XMP:Label=',
                '-XMP:City=',
                '-XMP:State=',
                '-XMP:Country=',
                '-XMP:Description=',
                '-XMP:Title=',
                '-IPTC:City=',
                '-IPTC:Country-PrimaryLocationName=',
                '-IPTC:Province-State=',
            ]
            batch_args.extend(valid_files)
            batch_args.append('-execute')

            try:
                # V4.5: 批量重置走 write 进程，与批量写入同一路由
                # V4.5: Batch reset uses the write process, same route as batch writes
                wp = self._write_proc
                with wp.lock:
                    wp.start()
                    if not wp.process:
                        raise RuntimeError("Process not started")

                    cmd_str = '\n'.join(batch_args) + '\n'
                    wp.process.stdin.write(cmd_str.encode('utf-8'))
                    wp.process.stdin.flush()

                    # 单次 -execute，读一次 {ready} / Single -execute, one {ready} read.
                    output = wp.read_until_ready(timeout=60)
                    decoded = output.decode('utf-8', errors='replace')

                # 锁已释放，再做日志和统计，不阻塞其他 ExifTool 调用。
                # Lock released; compute stats and log without holding it.
                import re as _re
                m = _re.search(r'(\d+)\s+image files?\s+updated', decoded)
                updated_count = int(m.group(1)) if m else 0
                error_lines = [
                    ln for ln in decoded.splitlines()
                    if 'Error' in ln and 'Warning' not in ln and ln.strip()
                ]
                error_count = len(error_lines)
                # 未能解析更新数（文件本已无元数据可清除）时按全成功算。
                # If updated count cannot be parsed, treat all files as success.
                batch_success = updated_count if (updated_count > 0 or error_count > 0) else len(valid_files)

                stats['success'] += batch_success
                stats['failed'] += error_count

                if i18n:
                    log(i18n.t("logs.batch_progress", start=batch_start+1, end=batch_end, success=batch_success, skipped=0))
                else:
                    log(f"  ✅ 批次 {batch_start+1}-{batch_end}: {batch_success} 个文件已处理")

            except Exception as e:
                stats['failed'] += len(valid_files)
                self._write_proc.stop()
                if i18n:
                    log(f"  ❌ {i18n.t('logs.batch_error', start=batch_start+1, end=batch_end, error=str(e))}")
                else:
                    log(f"  ❌ 批次 {batch_start+1}-{batch_end} 错误: {e}")

        # V4.0.3: 清理潜在残留的临时文件
        self.cleanup_temp_files(file_paths)

        if i18n:
            log(f"\n{i18n.t('logs.batch_complete', success=stats['success'], skipped=0, failed=stats['failed'])}")
        else:
            log(f"\n✅ 批量重置完成: {stats['success']} 成功, {stats['failed']} 失败")
        return stats

    def restore_files_from_manifest(self, dir_path: str, log_callback=None, i18n=None) -> Dict[str, int]:
        """
        V3.3: 根据 manifest 将文件恢复到原始位置
        V3.3.1: 增强版 - 也处理不在 manifest 中的文件
        V4.0: 支持多层目录恢复（鸟种子目录、连拍子目录）
        
        Args:
            dir_path: str, 原始目录路径
            log_callback: callable, 日志回调函数
            i18n: I18n instance for internationalization (optional)
        
        Returns:
            dict: {'restored': int, 'failed': int, 'not_found': int}
        """
        import json
        import shutil
        
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        def t(key, **kwargs):
            """Get translation or fallback to key"""
            if i18n:
                return i18n.t(key, **kwargs)
            return key  # Fallback
        
        stats = {'restored': 0, 'failed': 0, 'not_found': 0}
        manifest_path = os.path.join(dir_path, ".superpicky_manifest.json")
        folders_to_check = set()
        
        # 第一步：从 manifest 恢复文件（如果存在）
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                
                files = manifest.get('files', [])
                if files:
                    log(t("logs.manifest_restoring", count=len(files)))
                    
                    for file_info in files:
                        filename = file_info['filename']
                        folder = file_info['folder']

                        # 跳过 macOS AppleDouble 元数据文件（._xxx），由系统自动管理
                        if filename.startswith('._'):
                            continue

                        src_path = os.path.join(dir_path, folder, filename)
                        dst_path = os.path.join(dir_path, filename)

                        # V4.0: 记录所有涉及的目录（包括多层）
                        folders_to_check.add(os.path.join(dir_path, folder))
                        # 添加父目录（如 3星_优选/红嘴蓝鹊 → 也需要检查 3星_优选）
                        parts = folder.split(os.sep)
                        if len(parts) > 1:
                            folders_to_check.add(os.path.join(dir_path, parts[0]))

                        if not os.path.exists(src_path):
                            stats['not_found'] += 1
                            continue

                        if os.path.exists(dst_path):
                            stats['failed'] += 1
                            log(t("logs.restore_skipped_exists", filename=filename))
                            continue
                        
                        try:
                            shutil.move(src_path, dst_path)
                            stats['restored'] += 1
                        except Exception as e:
                            stats['failed'] += 1
                            log(t("logs.restore_failed", filename=filename, error=e))
                
                # V4.0: 删除临时转换的 JPEG 文件
                temp_jpegs = manifest.get('temp_jpegs', [])
                if temp_jpegs:
                    log(t("logs.temp_jpeg_cleanup", count=len(temp_jpegs)))
                    deleted_temp = 0
                    abs_dir_path = os.path.abspath(dir_path)
                    for jpeg_filename in temp_jpegs:
                        if not isinstance(jpeg_filename, str):
                            continue

                        normalized_name = os.path.normpath(jpeg_filename)
                        if (
                            os.path.isabs(normalized_name)
                            or os.path.splitdrive(normalized_name)[0]
                            or normalized_name == '..'
                            or normalized_name.startswith(f'..{os.sep}')
                        ):
                            log(t("logs.temp_jpeg_delete_failed", filename=jpeg_filename, error="invalid_path"))
                            continue
                        # 临时 JPEG 可能在根目录或子目录中
                        # jpeg_path = os.path.join(dir_path, jpeg_filename)
                        jpeg_path = os.path.abspath(os.path.join(abs_dir_path, normalized_name))
                        if os.path.commonpath([abs_dir_path, jpeg_path]) != abs_dir_path:
                            log(t("logs.temp_jpeg_delete_failed", filename=jpeg_filename, error="invalid_path"))
                            continue
                        
                        if os.path.exists(jpeg_path):
                            try:
                                os.remove(jpeg_path)
                                deleted_temp += 1
                            except Exception as e:
                                log(t("logs.temp_jpeg_delete_failed", filename=jpeg_filename, error=e))
                    if deleted_temp > 0:
                        log(t("logs.temp_jpeg_deleted", count=deleted_temp))
                
                # 删除 manifest 文件
                try:
                    os.remove(manifest_path)
                    log(t("logs.manifest_deleted"))
                except Exception as e:
                    log(t("logs.manifest_delete_failed", error=e))
                    
            except Exception as e:
                log(t("logs.manifest_read_failed", error=e))
        else:
            log(t("logs.manifest_not_found"))
        
        # 第二步：递归扫描评分子目录，恢复任何剩余文件（V4.0: 支持多层）
        log(t("logs.scan_subdirs"))
        
        # V3.3: 添加旧版目录到扫描列表（兼容旧版本）
        legacy_folders = ["2星_良好_锐度", "2星_良好_美学"]
        all_folders = list(RATING_FOLDER_NAMES.values()) + legacy_folders
        
        def restore_from_folder(folder_path: str, relative_path: str = ""):
            """递归恢复文件夹中的文件"""
            nonlocal stats
            
            if not os.path.exists(folder_path):
                return
            
            for entry in os.listdir(folder_path):
                entry_path = os.path.join(folder_path, entry)

                if os.path.isdir(entry_path):
                    # V4.0: 递归处理子目录（鸟种目录、连拍目录）
                    folders_to_check.add(entry_path)
                    restore_from_folder(entry_path, os.path.join(relative_path, entry) if relative_path else entry)
                else:
                    # 跳过 macOS AppleDouble 元数据文件（._xxx），由系统自动管理
                    if entry.startswith('._'):
                        continue

                    # 移动文件回主目录
                    dst_path = os.path.join(dir_path, entry)

                    if os.path.exists(dst_path):
                        log(t("logs.restore_skipped_exists", filename=entry))
                        continue
                    
                    try:
                        shutil.move(entry_path, dst_path)
                        stats['restored'] += 1
                        display_path = os.path.join(relative_path, entry) if relative_path else entry
                        log(t("logs.restore_success", folder=os.path.basename(folder_path), filename=entry))
                    except Exception as e:
                        stats['failed'] += 1
                        log(t("logs.restore_failed", filename=entry, error=e))
        
        for folder_name in set(all_folders):  # 使用 set 去重
            folder_path = os.path.join(dir_path, folder_name)
            folders_to_check.add(folder_path)
            restore_from_folder(folder_path, folder_name)
        
        # 第三步：删除空的分类文件夹（从最深层开始删除）
        # V4.0: 按路径深度排序，确保子目录先于父目录删除
        sorted_folders = sorted(folders_to_check, key=lambda x: x.count(os.sep), reverse=True)
        for folder_path in sorted_folders:
            if os.path.exists(folder_path):
                try:
                    if not os.listdir(folder_path):
                        os.rmdir(folder_path)
                        folder_name = os.path.relpath(folder_path, dir_path)
                        log(t("logs.empty_folder_deleted", folder=folder_name))
                except Exception as e:
                    log(t("logs.folder_delete_failed", error=e))
        
        log(t("logs.restore_complete", count=stats['restored']))
        if stats['not_found'] > 0:
            log(t("logs.restore_not_found", count=stats['not_found']))
        if stats['failed'] > 0:
            log(t("logs.restore_failed_count", count=stats['failed']))
        
        return stats


# 全局实例
exiftool_manager = None


def get_exiftool_manager() -> ExifToolManager:
    """获取ExifTool管理器单例"""
    global exiftool_manager
    if exiftool_manager is None:
        exiftool_manager = ExifToolManager()
    return exiftool_manager


# 便捷函数
def set_photo_metadata(file_path: str, rating: int, pick: int = 0, sharpness: float = None,
                      nima_score: float = None) -> bool:
    """设置照片元数据的便捷函数 (V3.2: 移除brisque_score)"""
    manager = get_exiftool_manager()
    return manager.set_rating_and_pick(file_path, rating, pick, sharpness, nima_score)


if __name__ == "__main__":
    # 测试代码
    print("=== ExifTool管理器测试 ===\n")

    # 初始化管理器
    manager = ExifToolManager()

    print("✅ ExifTool管理器初始化完成")

    # 如果提供了测试文件路径，执行实际测试
    test_files = [
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6782.NEF",
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6783.NEF",
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6784.NEF"
    ]

    # 检查测试文件是否存在
    available_files = [f for f in test_files if os.path.exists(f)]

    if available_files:
        print(f"\n🧪 发现 {len(available_files)} 个测试文件，执行实际测试...")

        # 0️⃣ 先重置所有测试文件
        print("\n0️⃣ 重置测试文件元数据:")
        reset_stats = manager.batch_reset_metadata(available_files)
        print(f"   结果: {reset_stats}\n")

        # 单个文件测试 - 优秀照片
        print("\n1️⃣ 单个文件测试 - 优秀照片 (3星 + 精选旗标):")
        success = manager.set_rating_and_pick(
            available_files[0],
            rating=3,
            pick=1
        )
        print(f"   结果: {'✅ 成功' if success else '❌ 失败'}")

        # 批量测试
        if len(available_files) >= 2:
            print("\n2️⃣ 批量处理测试:")
            batch_data = [
                {'file': available_files[0], 'rating': 3, 'pick': 1},
                {'file': available_files[1], 'rating': 2, 'pick': 0},
            ]
            if len(available_files) >= 3:
                batch_data.append(
                    {'file': available_files[2], 'rating': -1, 'pick': -1}
                )

            stats = manager.batch_set_metadata(batch_data)
            print(f"   结果: {stats}")

        # 读取元数据验证
        print("\n3️⃣ 读取元数据验证:")
        for i, file_path in enumerate(available_files, 1):
            metadata = manager.read_metadata(file_path)
            filename = os.path.basename(file_path)
            if metadata:
                print(f"   {filename}:")
                print(f"      Rating: {metadata.get('Rating', 'N/A')}")
                print(f"      Pick: {metadata.get('Pick', 'N/A')}")
                print(f"      Label: {metadata.get('Label', 'N/A')}")
    else:
        print("\n⚠️  未找到测试文件，跳过实际测试")
