#!/usr/bin/env python3
"""
SuperPicky BirdID 服务器管理器
管理 API 服务器的生命周期：启动、停止、状态检查
支持守护进程模式，使服务器可以独立于 GUI 运行

V4.0.0 修复：打包模式下使用线程方式启动，避免重复启动整个应用
"""

import os
import sys
import signal
import socket
import subprocess
import time
import json
import threading

# V4.2.1: I18n support
from tools.i18n import get_i18n

def get_t():
    """Get translator function"""
    try:
        # Try to get language from config file if possible, or default
        # For server manager, we might just default to system locale or english if config not loaded
        # But get_i18n handles defaults.
        return get_i18n().t
    except Exception:
        # Fallback if core module not found (e.g. running check script standalone without path)
        return lambda k, **kw: k

# PID 文件位置
def get_pid_file_path():
    """获取 PID 文件路径"""
    if sys.platform == 'darwin':
        pid_dir = os.path.expanduser('~/Library/Application Support/SuperPicky')
    else:
        pid_dir = os.path.expanduser('~/.superpicky')
    os.makedirs(pid_dir, exist_ok=True)
    return os.path.join(pid_dir, 'birdid_server.pid')


def get_server_script_path():
    """获取服务器脚本路径"""
    # 支持开发模式和打包模式
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, 'birdid_server.py')


def is_port_in_use(port, host='127.0.0.1'):
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


def check_server_health(port=5156, host='127.0.0.1', timeout=2):
    """检查服务器健康状态"""
    try:
        import urllib.request
        import ssl
        
        url = f'http://{host}:{port}/health'
        req = urllib.request.Request(url, method='GET')
        
        # macOS SSL证书问题修复
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                return data.get('status') == 'ok'
    except Exception:
        pass
    return False


def read_pid():
    """读取 PID 文件"""
    pid_file = get_pid_file_path()
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                return int(f.read().strip())
        except (ValueError, IOError):
            pass
    return None


def write_pid(pid):
    """写入 PID 文件"""
    pid_file = get_pid_file_path()
    with open(pid_file, 'w') as f:
        f.write(str(pid))


def remove_pid():
    """删除 PID 文件"""
    pid_file = get_pid_file_path()
    if os.path.exists(pid_file):
        try:
            os.remove(pid_file)
        except OSError:
            pass


def is_process_running(pid):
    """检查进程是否存在"""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # 发送信号 0 检查进程是否存在
        return True
    except (OSError, ProcessLookupError):
        return False


def get_server_status(port=5156):
    """
    获取服务器状态
    
    Returns:
        dict: {
            'running': bool,
            'pid': int or None,
            'healthy': bool,
            'port': int
        }
    """
    pid = read_pid()
    process_running = is_process_running(pid)
    port_in_use = is_port_in_use(port)
    healthy = check_server_health(port)
    
    return {
        'running': process_running or port_in_use,
        'pid': pid if process_running else None,
        'healthy': healthy,
        'port': port
    }


# 全局变量：跟踪线程模式的服务器
_server_thread = None
_server_instance = None


def start_server_thread(port=5156, log_callback=None):
    """
    在线程中启动服务器（用于打包模式）
    
    Args:
        port: 监听端口
        log_callback: 日志回调函数
        
    Returns:
        tuple: (success: bool, message: str, thread: Thread or None)
    """
    global _server_thread, _server_instance
    
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    # 检查是否已经运行
    t = get_t()
    if check_server_health(port):
        log(t("server.server_already_running", port=port))
        return True, "Server already running", _server_thread
    
    try:
        # 导入服务器模块
        from birdid_server import app, ensure_models_loaded
        from werkzeug.serving import make_server
        
        log(t("server.packaged_mode_thread"))
        
        def run_server():
            global _server_instance
            try:
                # 异步预加载模型（不阻塞服务器启动）
                def load_models_async():
                    try:
                        log(t("server.loading_models"))
                        ensure_models_loaded()
                        log(t("server.models_loaded"))
                    except Exception as e:
                        log(t("server.model_load_error", error=e))
                
                # 在后台线程中加载模型
                model_thread = threading.Thread(target=load_models_async, daemon=True)
                model_thread.start()
                
                # 立即启动服务器，不等待模型加载完成
                _server_instance = make_server('127.0.0.1', port, app, threaded=True)
                log(t("server.server_started", port=port))
                _server_instance.serve_forever()
            except Exception as e:
                log(t("server.server_thread_error", error=e))
        
        # 创建并启动守护线程
        _server_thread = threading.Thread(target=run_server, daemon=True, name="BirdID-API-Server")
        _server_thread.start()
        
        # 等待服务器启动（最多 10 秒，因为服务器启动很快）
        for i in range(20):
            time.sleep(0.5)
            if check_server_health(port):
                log(t("server.server_health_ok", port=port))
                return True, "Server start success", _server_thread
        
        log(t("server.server_timeout"))
        return True, "Server starting", _server_thread
        
    except Exception as e:
        log(t("server.thread_start_failed", error=e))
        import traceback
        traceback.print_exc()
        return False, str(e), None


def start_server_daemon(port=5156, log_callback=None):
    """
    启动服务器
    
    打包模式下使用线程方式启动（避免重复启动整个应用）
    开发模式下使用子进程方式启动
    
    Args:
        port: 监听端口
        log_callback: 日志回调函数
        
    Returns:
        tuple: (success: bool, message: str, pid: int or None)
    """
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    # 检查是否已经运行
    t = get_t()
    status = get_server_status(port)
    if status['healthy']:
        log(t("server.server_already_running", port=port))
        return True, "Server already running", status['pid']
    
    # 如果端口被占用但不健康，可能是僵尸进程
    if status['running'] and not status['healthy']:
        log(t("server.zombie_process"))
        stop_server()
        time.sleep(1)
    
    # 检测运行模式
    is_frozen = getattr(sys, 'frozen', False)
    
    if is_frozen:
        # 打包模式：使用线程方式启动
        log(t("server.packaged_mode_detected"))
        success, message, thread = start_server_thread(port, log_callback)
        # 线程模式没有独立 PID，返回主进程 PID
        return success, message, os.getpid() if success else None
    else:
        # 开发模式：使用子进程方式启动
        log(t("server.dev_mode_subprocess"))
        return _start_server_subprocess(port, log_callback)


def _start_server_subprocess(port=5156, log_callback=None):
    """
    以子进程方式启动服务器（仅开发模式使用）
    """
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    python_exe = sys.executable
    server_script = get_server_script_path()
    
    t = get_t()
    
    if not os.path.exists(server_script):
        return False, f"Server script not found: {server_script}", None
    
    cmd = [python_exe, server_script, '--port', str(port)]
    log(t("server.starting_daemon", cmd=' '.join(cmd)))
    
    try:
        # 以守护进程方式启动（分离子进程）
        if sys.platform == 'darwin':
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True
            )
        elif sys.platform == 'win32':
            # Windows: 使用 CREATE_NO_WINDOW 标志避免显示控制台窗口
            # 注意：CREATE_NO_WINDOW 在 Python 3.7+ 中可用
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    start_new_session=False
                )
            except AttributeError:
                # 如果 CREATE_NO_WINDOW 不可用，使用 CREATE_NEW_CONSOLE 和 DETACHED_PROCESS
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
                    start_new_session=False
                )
        else:
            # Linux/Unix
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )
        
        write_pid(process.pid)
        log(t("server.server_pid", pid=process.pid))
        
        # 等待服务器启动
        for i in range(10):
            time.sleep(0.5)
            if check_server_health(port):
                log(t("server.server_health_ok", port=port))
                return True, "Server start success", process.pid
        
        if is_process_running(process.pid):
            log(t("server.server_started_health_fail"))
            return True, "Server starting", process.pid
        else:
            log(t("server.server_process_exited"))
            remove_pid()
            return False, "服务器启动失败", None
            
    except Exception as e:
        log(t("server.start_failed", error=e))
        return False, str(e), None


def stop_server(log_callback=None):
    """
    停止服务器
    
    Returns:
        tuple: (success: bool, message: str)
    """
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    t = get_t()
    pid = read_pid()

    def _send_signal(sig):
        if sys.platform == 'win32':
            return
        try:
            os.killpg(pid, sig)
        except Exception:
            os.kill(pid, sig)
    
    if pid and is_process_running(pid):
        log(t("server.stop_server", pid=pid))
        try:
            # Windows 平台使用不同的信号处理
            if sys.platform == 'win32':
                # Windows 没有 SIGTERM/SIGKILL，使用 terminate() 方法
                import subprocess
                try:
                    # 尝试使用 taskkill 命令
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                                  capture_output=True, timeout=5)
                except Exception:
                    # 如果 taskkill 失败，尝试其他方法
                    pass
            else:
                # Unix/Linux/macOS 平台使用信号
                _send_signal(signal.SIGTERM)
            
            # 等待进程退出
            for i in range(24):
                time.sleep(0.25)
                if not is_process_running(pid):
                    break
            
            # 如果还没退出，强制终止（仅限非Windows平台）
            if is_process_running(pid) and sys.platform != 'win32':
                log(t("server.force_kill"))
                _send_signal(signal.SIGKILL)
                time.sleep(0.5)
            
            remove_pid()
            log(t("server.server_stopped"))
            return True, "Server stopped"
            
        except Exception as e:
            log(t("server.stop_failed", error=e))
            remove_pid()
            return False, str(e)
    else:
        # 清理可能的僵尸 PID 文件
        remove_pid()
        log(t("server.server_not_running"))
        return True, "Server not running"


def restart_server(port=5156, log_callback=None):
    """重启服务器"""
    stop_server(log_callback)
    time.sleep(1)
    return start_server_daemon(port, log_callback)


# 命令行入口
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='BirdID 服务器管理器')
    parser.add_argument('action', choices=['start', 'stop', 'restart', 'status'],
                        help='操作: start/stop/restart/status')
    parser.add_argument('--port', type=int, default=5156, help='端口号')
    
    args = parser.parse_args()
    
    if args.action == 'start':
        success, msg, pid = start_server_daemon(args.port)
        print(msg)
        sys.exit(0 if success else 1)
        
    elif args.action == 'stop':
        success, msg = stop_server()
        print(msg)
        sys.exit(0 if success else 1)
        
    elif args.action == 'restart':
        success, msg, pid = restart_server(args.port)
        print(msg)
        sys.exit(0 if success else 1)
        
    elif args.action == 'status':
        status = get_server_status(args.port)
        print(f"运行状态: {'运行中' if status['running'] else '未运行'}")
        print(f"健康状态: {'正常' if status['healthy'] else '异常'}")
        print(f"PID: {status['pid'] or 'N/A'}")
        print(f"端口: {status['port']}")
        sys.exit(0 if status['healthy'] else 1)
