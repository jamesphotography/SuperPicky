import os
import site
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
import sys
sys.path.append(os.path.abspath('.'))
from constants import APP_VERSION

# 获取当前工作目录
base_path = os.path.abspath('.')

# 动态获取 site-packages 路径
# 在 venv 环境下，site.getsitepackages() 通常包含 venv 的 site-packages
sp = site.getsitepackages()
site_packages = sp[1] if len(sp) > 1 else sp[0]

# 处理 ultralytics 路径
ultralytics_base = site_packages
if not os.path.exists(os.path.join(ultralytics_base, 'ultralytics')):
    # 备选方案：尝试从模块导入获取路径
    try:
        import ultralytics
        ultralytics_base = os.path.dirname(os.path.dirname(ultralytics.__file__))
    except ImportError:
        pass

# 动态收集数据文件
ultralytics_datas = collect_data_files('ultralytics')
imageio_datas = collect_data_files('imageio')
rawpy_datas = collect_data_files('rawpy')
pillow_heif_datas = collect_data_files('pillow_heif')

# 组合所有数据文件
all_datas = [
    # AI模型文件
    (os.path.join(base_path, 'models'), 'models'),
    # ExifTool 完整打包
    (os.path.join(base_path, 'exiftools_mac'), 'exiftools_mac'),
    # (os.path.join(base_path, 'exiftools_win'), 'exiftools_win'), # Windows ExifTool (Excluded on Mac to speed up signing)
    # 图片资源
    (os.path.join(base_path, 'img'), 'img'),
    # 国际化语言包
    (os.path.join(base_path, 'locales'), 'locales'),
    # macOS 本地化 (应用名称) - 必须放在 Resources 根目录
    (os.path.join(base_path, 'locales', 'en.lproj'), 'en.lproj'),
    (os.path.join(base_path, 'locales', 'zh-Hans.lproj'), 'zh-Hans.lproj'),
    # Ultralytics 配置
    (os.path.join(ultralytics_base, 'ultralytics/cfg'), 'ultralytics/cfg'),
    # V4.0.0: 鸟类识别模块数据 (V4.0.6: 移除旧 birdid/models，改用 models/model20240824.pth OSEA 模型)
    (os.path.join(base_path, 'birdid/data'), 'birdid/data'),
    # V4.0.0: Lightroom 插件
    (os.path.join(base_path, 'SuperBirdIDPlugin.lrplugin'), 'SuperBirdIDPlugin.lrplugin'),
    # V4.2.x: 鸟名查询数据库 (ioc/birdname.db)
    (os.path.join(base_path, 'ioc'), 'ioc'),
]

# 添加动态收集的数据
all_datas.extend(ultralytics_datas)
all_datas.extend(imageio_datas)
all_datas.extend(rawpy_datas)
all_datas.extend(pillow_heif_datas)
# 添加包元数据
all_datas.extend(copy_metadata('imageio'))
all_datas.extend(copy_metadata('rawpy'))
all_datas.extend(copy_metadata('ultralytics'))
all_datas.extend(copy_metadata('pillow_heif'))

a = Analysis(
    ['main.py'],
    pathex=[base_path],
    binaries=[],
    datas=all_datas,
    hiddenimports=[
        'ultralytics',
        'torch',
        'torchvision',
        'PIL',
        'cv2',
        'numpy',
        'yaml',
        'matplotlib',
        'matplotlib.pyplot',
        'matplotlib.backends.backend_agg',
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'timm',
        'timm.models',
        'timm.models.resnet',
        'imageio',
        'rawpy',
        'imagehash',
        'pywt',
        'pillow_heif',   # HEIF/HIF 支持
        'core',
        'core.burst_detector',
        'core.config_manager',
        'core.exposure_detector',
        'core.file_manager',
        'core.flight_detector',
        'core.focus_point_detector',
        'core.keypoint_detector',
        'core.photo_processor',
        'core.rating_engine',
        'core.stats_formatter',
        'multiprocessing',
        'multiprocessing.spawn',
        # V3.9.5: 更新检测模块
        'tools.update_checker',
        'packaging',
        'packaging.version',
        # V4.0.0: 鸟类识别模块
        'birdid',
        'birdid.bird_identifier',
        'birdid.ebird_country_filter',
        'birdid_server',
        'server_manager',  # V4.0.0: 服务器管理模块
        'flask',
        'flask.json',
        'cryptography',
        'cryptography.fernet',
        # V4.2.1: Countly telemetry build config (dynamically imported via importlib,
        # PyInstaller cannot auto-discover it, must be listed explicitly)
        '_telemetry_build',
        'app_user_stat._telemetry_build',
        'app_user_stat',
        'app_user_stat.telemetry',
        'app_user_stat.consent_texts',
        'app_user_stat.consent_texts.en_US',
        'app_user_stat.consent_texts.zh_CN',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_cv2.py'] if os.path.exists('pyi_rth_cv2.py') else [],
    excludes=[
        'PyQt5', 'PyQt6', 'tkinter',
        # 僵尸依赖拦截：polars 仅用于 ultralytics W&B 训练回调，生产推理不触发
        'polars',
        # 防御性排除：facexlib/datasets 已卸载，防止意外重装时被打入包
        'numba', 'llvmlite',
        'pyarrow',
        'facexlib',
        'datasets',
    ],
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
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(base_path, 'img', 'SuperPicky-V0.02.icns') if os.path.exists(os.path.join(base_path, 'img', 'SuperPicky-V0.02.icns')) else None,
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

# macOS .app bundle
app = BUNDLE(
    coll,
    name='SuperPicky.app',
    icon=os.path.join(base_path, 'img', 'SuperPicky-V0.02.icns') if os.path.exists(os.path.join(base_path, 'img', 'SuperPicky-V0.02.icns')) else None,
    bundle_identifier='com.jamesphotography.superpicky',
    info_plist={
        'CFBundleName': 'SuperPicky',
        'CFBundleDisplayName': 'SuperPicky',
        'CFBundleVersion': APP_VERSION,
        'CFBundleShortVersionString': APP_VERSION,
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription': '慧眼选鸟需要发送 AppleEvents 与其他应用通信。',
        'NSAppleScriptEnabled': False,
    },
)
