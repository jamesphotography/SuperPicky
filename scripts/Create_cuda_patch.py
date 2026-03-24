# -*- coding: utf-8 -*-
"""Legacy helper script.

正式的 Windows CPU/CUDA Patch 构建入口已迁移到 `build_release_win.py`。
保留此脚本仅用于历史兼容与手工对照，不再由 GitHub Actions 调用。
"""
import os
import sys
import shutil
import hashlib

# 确保输出编码为 UTF-8
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 定义路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CPU_DIR = os.path.join(BASE_DIR, 'output', 'SuperPicky_Win64_CPU')
CUDA_DIR = os.path.join(BASE_DIR, 'output', 'SuperPicky_Win64_CUDA')
IMG_DIR = os.path.join(BASE_DIR, 'img')
INNO_DIR = os.path.join(BASE_DIR, 'inno')
PATCH_DIR = os.path.join(BASE_DIR, 'output', 'cuda_patch')
PATCH_MANIFEST = os.path.join(PATCH_DIR, '_internal', 'cuda_patch_manifest.txt')

# 确保补丁目录存在, 清空目录
if not os.path.exists(PATCH_DIR):
    os.makedirs(PATCH_DIR)
else:
    shutil.rmtree(PATCH_DIR)
    os.makedirs(PATCH_DIR)

# 计算文件哈希值
def get_file_hash(file_path):
    """计算文件的 SHA256 哈希值"""
    if not os.path.isfile(file_path):
        return None
    
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # 分块读取文件以处理大文件
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

# 遍历目录，获取所有文件路径
def get_all_files(directory):
    """获取目录下所有文件的相对路径"""
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            # 计算相对路径
            relative_path = os.path.relpath(os.path.join(root, filename), directory)
            files.append(relative_path)
    return files

# 复制img目录
def copy_img_dir():
    """复制 CPU 版本的 img 目录到补丁目录"""
    shutil.copytree(IMG_DIR, os.path.join(PATCH_DIR, 'img'))

def copy_inno_setup():
    """复制 CPU 版本的 Inno Setup 脚本到补丁目录"""
    shutil.copy2(os.path.join(INNO_DIR, 'SuperPicky_CUDA_Patch.iss'), os.path.join(PATCH_DIR, 'SuperPicky_CUDA_Patch.iss'))
    shutil.copy2(os.path.join(INNO_DIR, 'ChineseSimplified.isl'), os.path.join(PATCH_DIR, 'ChineseSimplified.isl'))


def write_patch_manifest(cuda_only_files):
    """写入 CUDA 独有文件清单，供主卸载程序清理补丁残留"""
    os.makedirs(os.path.dirname(PATCH_MANIFEST), exist_ok=True)
    with open(PATCH_MANIFEST, 'w', encoding='utf-8', newline='\n') as fh:
        for relative_path in sorted(cuda_only_files):
            fh.write(relative_path.replace('/', '\\') + '\n')

# 分析差异
def analyze_differences():
    print("开始分析 CPU 和 CUDA 版本的文件差异...")
    
    # 获取两个目录的文件列表
    cpu_files = get_all_files(CPU_DIR)
    cuda_files = get_all_files(CUDA_DIR)
    
    # 合并所有文件路径，去重
    all_files = set(cpu_files + cuda_files)
    
    # 差异文件计数
    different_files = 0
    cuda_only_files = 0
    cuda_only_file_list = []
    
    # 分析每个文件
    for file_path in all_files:
        cpu_file = os.path.join(CPU_DIR, file_path)
        cuda_file = os.path.join(CUDA_DIR, file_path)
        
        # 检查文件是否存在
        cpu_exists = os.path.exists(cpu_file)
        cuda_exists = os.path.exists(cuda_file)
        
        if not cpu_exists and cuda_exists:
            # CUDA 独有的文件
            print(f"CUDA 独有文件: {file_path}")
            cuda_only_files += 1
            cuda_only_file_list.append(file_path)
            # 复制到补丁目录
            patch_file = os.path.join(PATCH_DIR, file_path)
            os.makedirs(os.path.dirname(patch_file), exist_ok=True)
            shutil.copy2(cuda_file, patch_file)
        elif cpu_exists and cuda_exists:
            # 两个版本都有，比较哈希值
            cpu_hash = get_file_hash(cpu_file)
            cuda_hash = get_file_hash(cuda_file)
            
            if cpu_hash != cuda_hash:
                print(f"文件不同: {file_path}")
                different_files += 1
                # 复制到补丁目录
                patch_file = os.path.join(PATCH_DIR, file_path)
                os.makedirs(os.path.dirname(patch_file), exist_ok=True)
                shutil.copy2(cuda_file, patch_file)
    
    print(f"\n分析完成:")
    print(f"- 不同的文件数量: {different_files}")
    print(f"- CUDA 独有的文件数量: {cuda_only_files}")
    print(f"- 补丁文件已导出到: {PATCH_DIR}")
    write_patch_manifest(cuda_only_file_list)
    print(f"- CUDA 独有文件清单已写入: {PATCH_MANIFEST}")
    copy_img_dir()
    print(f"- img 目录已复制到补丁目录: {os.path.join(PATCH_DIR, 'img')}")
    copy_inno_setup()
    print(f"- Inno Setup 脚本已复制到补丁目录: {os.path.join(PATCH_DIR, 'SuperPicky_CUDA_Patch.iss')}")
    print(f"- Inno Setup 中文语言文件已复制到补丁目录: {os.path.join(PATCH_DIR, 'ChineseSimplified.isl')}")


if __name__ == "__main__":
    analyze_differences()
