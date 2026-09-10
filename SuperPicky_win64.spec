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
site_packages = os.environ.get('SUPERPICKY_SITE_PACKAGES', '').strip()
if not site_packages:
    sp = [p for p in site.getsitepackages() if os.path.isdir(p)]
    for p in sp:
        if os.path.exists(os.path.join(p, 'ultralytics')):
            site_packages = p
            break
    if not site_packages and sp:
        site_packages = sp[0]
    if not site_packages:
        site_packages = site.getusersitepackages()

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
    #(os.path.join(base_path, 'exiftools_mac'), 'exiftools_mac'),
    (os.path.join(base_path, 'exiftools_win'), 'exiftools_win'), # Windows ExifTool (Excluded on Mac to speed up signing)
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
    (os.path.join(base_path, 'ioc'), 'ioc'),
    # V4.0.0: Lightroom 插件
    (os.path.join(base_path, 'SuperBirdIDPlugin.lrplugin'), 'SuperBirdIDPlugin.lrplugin'),
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
all_datas.extend(copy_metadata('pi_heif'))

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
        'pi_heif',     # CUDA 版本可能需要这个 HEIF/HIF 支持
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
        'birdid.geo_filter',       # 地理过滤：bird_identifier 顶层导入，其余调用点为函数内延迟导入
        'tools.country_names',     # 国家显示名：仅被 region_data / birdid_server 函数内导入
        'birdid_server',
        'server_manager',  # V4.0.0: 服务器管理模块
        'flask',
        'flask.json',
        'cryptography',
        'cryptography.fernet',
        'app_user_stat',
        'app_user_stat.telemetry',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_cv2.py'] if os.path.exists('pyi_rth_cv2.py') else [],
    excludes=['PyQt5', 'PyQt6', 'tkinter'],
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------------------
# CUDA 瘦身：排除运行时永远不会加载的 cuDNN 子库
# CUDA slimming: drop cuDNN sub-libraries that are never loaded at runtime
#
# cuDNN 9 已经把单体库拆成「dispatcher + 按需加载的子库」：cudnn64_9.dll 只有
# 0.4 MiB，真正的实现分散在 cudnn_ops / cudnn_cnn / cudnn_adv / cudnn_graph /
# cudnn_engines_* 等子库里，由 dispatcher 在实际调用到对应 API 时才
# LoadLibrary。其中 adv 子库只服务 RNN / LSTM / multi-head attention，而本项目
# 全部是 CNN 前向推理（YOLO 检测、timm 分类、TOPIQ 美学），运行时不可能碰到它。
#
# 因为是运行时按需加载而非 PE 静态导入，删掉它不会让 import torch 失败。
# 注意：cusolver / cufft / cusparse / curand / cublasLt 则是 torch_cuda.dll 的
# 静态导入（PyTorch v2.7.1 只对 nvcuda.dll 做了 DELAYLOAD，见 pytorch 仓库
# caffe2/CMakeLists.txt:553-559），少任何一个都会让 torch 直接导入失败，
# 绝对不要加进这个列表。
#
# 收益：未压缩 229.9 MiB，CUDA 安装器约减少 80 MB。
# 本 spec 由 CPU 与 CUDA 两种构建共用，而 CPU 版的 torch wheel 不含任何 cudnn
# DLL（只有 torch/backends/cudnn 那些 .py），所以此过滤对 CPU 构建是空操作；
# macOS 构建使用 SuperPicky_full.spec，完全不经过这里。
#
# cuDNN 9 splits the monolithic library into a small dispatcher plus on-demand
# sub-libraries; the `adv` one only serves RNN/LSTM/attention, which this
# CNN-only inference pipeline never calls. It is loaded via LoadLibrary rather
# than the PE import table, so removing it cannot break `import torch`.
# Do NOT add cusolver/cufft/cusparse/curand/cublasLt here: those are statically
# imported by torch_cuda.dll and their removal breaks torch outright.
EXCLUDED_BINARY_NAMES = {
    'cudnn_adv64_9.dll',
}

def _binary_basename(dest_name):
    """
    取二进制条目的文件名（大小写归一）/ Extract a binary entry's file name.

    不用 os.path.basename：PyInstaller 的条目名带 Windows 分隔符，而在
    macOS/Linux 上 os.path.basename 不把 '\\' 当分隔符，会让整条过滤静默失效，
    连本地校验都做不了。这里两种分隔符都处理。

    Not os.path.basename: entry names carry Windows separators, which POSIX
    os.path.basename does not split on — that would silently disable the filter
    and make local verification impossible. Handle both separators.

    参数 / Parameters:
    dest_name (str): 打包目标路径，如 'torch\\lib\\cudnn_adv64_9.dll'。

    返回 / Return:
    str: 小写文件名，如 'cudnn_adv64_9.dll'。
    """
    return dest_name.replace('\\', '/').rsplit('/', 1)[-1].lower()


_excluded_binaries = [
    entry for entry in a.binaries
    if _binary_basename(entry[0]) in EXCLUDED_BINARY_NAMES
]
a.binaries = [
    entry for entry in a.binaries
    if _binary_basename(entry[0]) not in EXCLUDED_BINARY_NAMES
]
for _entry in _excluded_binaries:
    print(f"[spec] 已排除二进制 / excluded binary: {_entry[0]}")
if not _excluded_binaries:
    # CPU 构建走到这里是正常的（本来就没有 cudnn DLL）；CUDA 构建若没排除到
    # 任何东西，说明 torch 的 cuDNN 布局变了，需要重新核对文件名。
    # Hitting this on a CPU build is expected; on a CUDA build it means torch's
    # cuDNN layout changed and the name list needs revisiting.
    print('[spec] 未匹配到待排除的二进制 / no binaries matched the exclusion list')

pyz = PYZ(a.pure)

# Windows 使用高精度 icon.ico（由 img/icon.png 生成），macOS 使用 .icns
_icon_ico = os.path.join(base_path, 'img', 'icon.ico')
_icon_icns = os.path.join(base_path, 'img', 'SuperPicky-V0.02.icns')
_exe_icon = _icon_ico if (sys.platform == 'win32' and os.path.exists(_icon_ico)) else (_icon_icns if os.path.exists(_icon_icns) else None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SuperPicky',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # CUDA/torch related binaries are sensitive to UPX compression on Windows.
    # Keep UPX disabled for runtime stability.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SuperPicky',
)

# macOS .app bundle
app = BUNDLE(
    coll,
    name='SuperPicky.app',
    icon=_icon_icns if os.path.exists(_icon_icns) else None,
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
