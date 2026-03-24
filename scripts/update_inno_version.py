#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新 Inno Setup 文件中的 AppVersion
从 constants.py 读取 APP_VERSION，获取当前 Git 提交哈希，
组合成类似 4.1.0-hash 的格式并更新到 inno/SuperPicky.iss
"""

import os
import sys
import subprocess


def get_commit_hash():
    """
    获取 COMMIT_HASH，优先通过 git 获取，其次从 Python 代码读取
    
    Returns:
        str: commit hash
    """
    # 优先通过 git 获取
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        if result.returncode == 0:
            commit_hash = result.stdout.strip()
            print(f"Using COMMIT_HASH from git: {commit_hash}")
            return commit_hash
    except Exception as e:
        print(f"Warning: could not get git commit hash: {e}")
    
    # Fallback: 从 Python 代码读取
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, base_dir)
        try:
            from core.build_info_local import COMMIT_HASH
            if COMMIT_HASH:
                print(f"Using COMMIT_HASH from build_info_local.py: {COMMIT_HASH}")
                return COMMIT_HASH
        except ImportError:
            pass
        from core.build_info import COMMIT_HASH as default_hash
        if default_hash:
            print(f"Using COMMIT_HASH from build_info.py: {default_hash}")
            return default_hash
    except Exception as e:
        print(f"Warning: could not read from build_info: {e}")
    
    return "unknown"


def read_app_version():
    """
    Read APP_VERSION from constants.py
    
    Returns:
        str: Application version
    """
    constants_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'constants.py'
    )
    
    try:
        with open(constants_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('APP_VERSION ='):
                    # Extract version, handle quotes
                    version = line.split('=', 1)[1].strip()
                    if version.startswith('"') and version.endswith('"'):
                        return version[1:-1]
                    elif version.startswith("'") and version.endswith("'"):
                        return version[1:-1]
                    return version
        print("APP_VERSION not found")
        return "0.0.0"
    except Exception as e:
        print(f"Error reading constants.py: {e}")
        return "0.0.0"


def update_inno_version():
    """
    Update AppVersion in inno/SuperPicky.iss
    """
    # Get version and hash
    app_version = read_app_version()
    commit_hash = get_commit_hash()
    
    # Combine version string
    new_version = f"{app_version}-{commit_hash}"
    print(f"Updating version to: {new_version}")
    
    # Locate inno files
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inno_paths = [
        os.path.join(base_dir, 'inno', 'SuperPicky.iss'),
        os.path.join(base_dir, 'inno', 'SuperPicky_CUDA_Patch.iss'),
    ]
    
    success = True
    for inno_path in inno_paths:
        if not os.path.exists(inno_path):
            print(f"Skipping {inno_path} - file not found")
            continue
            
        try:
            # Read file content
            with open(inno_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Replace AppVersion
            import re
            updated_content = re.sub(
                r'AppVersion=.+',
                f'AppVersion={new_version}',
                content
            )
            
            # Replace OutputBaseFilename based on file type
            if 'SuperPicky_CUDA_Patch.iss' in inno_path:
                updated_content = re.sub(
                    r'OutputBaseFilename=SuperPicky_CUDA_Patch_Win64.*',
                    f'OutputBaseFilename=SuperPicky_CUDA_Patch_Win64_{app_version}_{commit_hash}',
                    updated_content
                )
            else:
                updated_content = re.sub(
                    r'OutputBaseFilename=SuperPicky_Setup_Win64.*',
                    f'OutputBaseFilename=SuperPicky_Setup_Win64_{app_version}_{commit_hash}',
                    updated_content
                )
            
            # Write back to file
            with open(inno_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            
            print(f"Successfully updated {inno_path}")
        except Exception as e:
            print(f"Error updating {inno_path}: {e}")
            success = False
    
    return success


if __name__ == "__main__":
    success = update_inno_version()
    sys.exit(0 if success else 1)
