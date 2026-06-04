import os
import site
import subprocess
import sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

sys.path.append(os.path.abspath('.'))

base_path = os.path.abspath('.')


def _optional_copy_metadata(package_name):
    try:
        return copy_metadata(package_name)
    except Exception:
        return []


def _find_uv_binary():
    explicit = os.environ.get('SUPERPICKY_UV_BINARY')
    if not explicit:
        raise RuntimeError(
            'Windows Lite builds must set SUPERPICKY_UV_BINARY. '
            'Run build_release_win.py so uv is downloaded into build_tools/uv '
            'instead of using the build machine PATH.'
        )
    uv_path = os.path.abspath(explicit)
    if not os.path.exists(uv_path):
        raise RuntimeError(f'SUPERPICKY_UV_BINARY does not exist: {uv_path}')
    result = subprocess.run(
        [uv_path, '--version'],
        text=True,
        capture_output=True,
        timeout=20,
    )
    if result.returncode != 0:
        output = (result.stdout or '') + (result.stderr or '')
        raise RuntimeError(f'SUPERPICKY_UV_BINARY is not executable: {uv_path}\n{output}')
    return uv_path


sp = [p for p in site.getsitepackages() if os.path.isdir(p)]
site_packages = sp[0] if sp else site.getusersitepackages()

ultralytics_base = site_packages
if not os.path.exists(os.path.join(ultralytics_base, 'ultralytics')):
    try:
        import ultralytics
        ultralytics_base = os.path.dirname(os.path.dirname(ultralytics.__file__))
    except ImportError:
        pass

ultralytics_datas = collect_data_files('ultralytics')
imageio_datas = collect_data_files('imageio')
rawpy_datas = collect_data_files('rawpy')
pillow_heif_datas = collect_data_files('pillow_heif')
uv_binaries = [(_find_uv_binary(), 'uv')]

all_datas = [
    (os.path.join(base_path, 'exiftools_win'), 'exiftools_win'),
    (os.path.join(base_path, 'img'), 'img'),
    (os.path.join(base_path, 'locales'), 'locales'),
    (os.path.join(base_path, 'ioc'), 'ioc'),
    (os.path.join(base_path, 'models', 'yolo11l-seg.pt'), 'models'),
    (os.path.join(base_path, 'birdid', 'data', 'bird_reference.sqlite'), 'birdid/data'),
    (os.path.join(base_path, 'birdid', 'data', 'ebird_classid_mapping.json'), 'birdid/data'),
    (os.path.join(base_path, 'birdid', 'data', 'ebird_regions.json'), 'birdid/data'),
    (os.path.join(base_path, 'birdid', 'data', 'offline_ebird_data'), 'birdid/data/offline_ebird_data'),
    (os.path.join(base_path, 'SuperBirdIDPlugin.lrplugin'), 'SuperBirdIDPlugin.lrplugin'),
    (os.path.join(base_path, 'requirements_base.txt'), '.'),
    (os.path.join(base_path, 'core', 'runtime_requirements.py'), 'core'),
    (os.path.join(ultralytics_base, 'ultralytics', 'cfg'), 'ultralytics/cfg'),
]

all_datas.extend(ultralytics_datas)
all_datas.extend(imageio_datas)
all_datas.extend(rawpy_datas)
all_datas.extend(pillow_heif_datas)
all_datas.extend(_optional_copy_metadata('imageio'))
all_datas.extend(_optional_copy_metadata('rawpy'))
all_datas.extend(_optional_copy_metadata('ultralytics'))
all_datas.extend(_optional_copy_metadata('pillow_heif'))
all_datas.extend(_optional_copy_metadata('pi_heif'))

# Windows Lite 冻结包会在主程序最早期进入 `--runtime-bootstrap` 路径，
# 由打包后的可执行文件自身安装并校验 Torch 运行时。
# PyInstaller 对这条链路里的标准库模块并不总能静态识别，
# 所以需要在这里集中声明，避免后续再零散追加到主 hiddenimports 列表。
# The Windows Lite frozen build enters `--runtime-bootstrap` very early and
# installs/verifies the Torch runtime from the packaged executable itself.
# PyInstaller does not always detect the stdlib modules used along that path,
# so keep them centralized here instead of appending ad hoc entries later.
runtime_bootstrap_stdlib_hiddenimports = [
    'argparse',
    'ast',
    'base64',
    'bisect',
    'cProfile',
    'concurrent',
    'copy',
    'csv',
    'ctypes',
    'dataclasses',
    'datetime',
    'difflib',
    'dis',
    'enum',
    'faulthandler',
    'fnmatch',
    'gc',
    'getpass',
    'glob',
    'gzip',
    'hashlib',
    'heapq',
    'html',
    'html.entities',
    'html.parser',
    'inspect',
    'ipaddress',
    'linecache',
    'locale',
    'logging.config',
    'logging.handlers',
    'modulefinder',
    'numbers',
    'pickletools',
    'profile',
    'pprint',
    'pstats',
    'queue',
    'resource',
    'runpy',
    'shlex',
    'signal',
    'sqlite3',
    'statistics',
    'sysconfig',
    'tarfile',
    'timeit',
    'tokenize',
    'traceback',
    'unittest',
    'uuid',
    'weakref',
    'xml',
    'zipfile',
    '_markupbase',
]

app_hiddenimports = [
    'ultralytics',
    'PIL',
    'cv2',
    'numpy',
    'yaml',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'imageio',
    'rawpy',
    'imagehash',
    'pywt',
    'pillow_heif',
    'core',
    'core.burst_detector',
    'core.config_manager',
    'core.exposure_detector',
    'core.file_manager',
    'core.flight_detector',
    'core.focus_point_detector',
    'core.initialization_manager',
    'core.initialization_progress',
    'core.keypoint_detector',
    'core.photo_processor',
    'core.rating_engine',
    'core.runtime_bootstrap',
    'core.source_registry',
    'core.source_probe',
    'core.source_probe_parallel',
    'core.stats_formatter',
    'core.uv_runtime_manager',
    'scripts',
    'scripts.download_models',
    'multiprocessing',
    'multiprocessing.spawn',
    'tools.update_checker',
    'packaging',
    'packaging.version',
    'birdid',
    'birdid.bird_identifier',
    'birdid.ebird_country_filter',
    'birdid_server',
    'server_manager',
    'flask',
    'flask.json',
    'httpx',
    'httpcore',
    'h11',
    'anyio',
    'sniffio',
    'huggingface_hub',
    'huggingface_hub.file_download',
    'huggingface_hub.utils',
    'cryptography',
    'cryptography.fernet',
    '_telemetry_build',
    'app_user_stat._telemetry_build',
    'app_user_stat',
    'app_user_stat.telemetry',
    'app_user_stat.consent_texts',
    'app_user_stat.consent_texts.en_US',
    'app_user_stat.consent_texts.zh_CN',
]

a = Analysis(
    ['main.py'],
    pathex=[base_path],
    binaries=uv_binaries,
    datas=all_datas,
    hiddenimports=app_hiddenimports + runtime_bootstrap_stdlib_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_cv2.py'] if os.path.exists('pyi_rth_cv2.py') else [],
    excludes=[
        'torch', 'torchvision', 'torchaudio', 'timm',
        'PyQt5', 'PyQt6', 'tkinter',
        'polars', 'numba', 'llvmlite', 'pyarrow', 'facexlib', 'datasets',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

icon_path = os.path.join(base_path, 'img', 'icon.ico')
if not os.path.exists(icon_path):
    icon_path = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SuperPicky',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
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
