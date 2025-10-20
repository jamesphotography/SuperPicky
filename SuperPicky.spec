# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

# 获取当前工作目录
base_path = os.path.abspath('.')

# Python虚拟环境路径
venv_path = '/Users/jameszhenyu/SuperPicky3/venv/lib/python3.12/site-packages'

a = Analysis(
    ['main.py'],
    pathex=[base_path],
    binaries=[],
    datas=[
        # AI模型文件（只使用yolo11m-seg.pt）
        (os.path.join(base_path, 'models/yolo11m-seg.pt'), 'models'),

        # ExifTool
        (os.path.join(base_path, 'exiftool'), '.'),
        (os.path.join(base_path, 'exiftool_bundle'), 'exiftool_bundle'),

        # 图片资源
        (os.path.join(base_path, 'img'), 'img'),

        # Ultralytics配置文件
        (os.path.join(venv_path, 'ultralytics/cfg/default.yaml'), 'ultralytics/cfg'),
        (os.path.join(venv_path, 'ultralytics/utils'), 'ultralytics/utils'),
        (os.path.join(venv_path, 'ultralytics/nn'), 'ultralytics/nn'),
    ],
    hiddenimports=[
        'ultralytics',
        'torch',
        'torchvision',
        'PIL',
        'PIL._tkinter_finder',
        'tkinter',
        'tkinter.ttk',
        'cv2',
        'numpy',
        'yaml',
        'ttkthemes',
        'matplotlib',
        'matplotlib.pyplot',
        'matplotlib.backends.backend_agg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SuperPicky',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity='Developer ID Application: James Zhen Yu (JWR6FDB52H)',
    entitlements_file='entitlements.plist',
    icon='img/SuperPicky-V0.02.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SuperPicky',
)

app = BUNDLE(
    coll,
    name='SuperPicky.app',
    icon='img/SuperPicky-V0.02.icns',
    bundle_identifier='com.jamesphotography.superpicky',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'CFBundleName': 'SuperPicky',
        'CFBundleDisplayName': 'SuperPicky - 慧眼选鸟',
        'CFBundleVersion': '3.1.2',
        'CFBundleShortVersionString': '3.1.2',
        'NSHumanReadableCopyright': 'Copyright © 2025 James Zhen Yu. All rights reserved.',
        'LSMinimumSystemVersion': '10.15',
        'NSRequiresAquaSystemAppearance': False,
    },
)
