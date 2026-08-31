import os
import sqlite3
import subprocess
import sys
import threading
import rawpy
import imageio
from .utils import log_message
from .exiftool_manager import get_exiftool_manager
import glob
import shutil

from .file_utils import ensure_hidden_directory, clear_readonly_attribute


_EXIFTOOL_CLI_INFO = None
_EXIFTOOL_CLI_INFO_LOCK = threading.Lock()


def _get_exiftool_cli_info():
    global _EXIFTOOL_CLI_INFO
    if _EXIFTOOL_CLI_INFO is None:
        with _EXIFTOOL_CLI_INFO_LOCK:
            if _EXIFTOOL_CLI_INFO is None:
                manager = get_exiftool_manager()
                exiftool_path = manager.exiftool_path
                exiftool_cwd = os.path.dirname(os.path.abspath(exiftool_path))
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
                _EXIFTOOL_CLI_INFO = (exiftool_path, exiftool_cwd, creationflags)
    return _EXIFTOOL_CLI_INFO


def _extract_binary_via_exiftool_cli(raw_file_path, tag):
    exiftool_path, exiftool_cwd, creationflags = _get_exiftool_cli_info()
    result = subprocess.run(
        [exiftool_path, '-b', tag, raw_file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        cwd=exiftool_cwd,
        creationflags=creationflags,
        check=False,
        # 单文件提取内嵌预览；超时由调用方的 per-tag try 接住并换下一个标签
        timeout=30
    )
    if result.returncode != 0:
        stderr_text = result.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(stderr_text or f"ExifTool exited with code {result.returncode}")
    return result.stdout

def raw_to_jpeg(raw_file_path):
    filename = os.path.basename(raw_file_path)
    file_prefix, file_ext = os.path.splitext(filename)
    directory_path = os.path.dirname(raw_file_path)

    # 在初步生成预览图前先移除原文件只读属性，避免后续元数据写入或移动阶段失败
    clear_readonly_attribute(raw_file_path)
    
    # V4.1.0: 使用 .superpicky/cache 目录存储临时 JPEG
    superpicky_dir = os.path.join(directory_path, ".superpicky")
    cache_dir = os.path.join(superpicky_dir, "cache", "temp_preview")
    
    # 确保目录存在并隐藏
    ensure_hidden_directory(superpicky_dir)
    ensure_hidden_directory(cache_dir)
    
    # 文件名不带 tmp_ 前缀，直接使用原名前缀
    jpg_file_path = os.path.join(cache_dir, f"{file_prefix}.jpg")
    
    if os.path.exists(jpg_file_path) and os.path.getsize(jpg_file_path) >= 128 * 1024:
        return jpg_file_path  # 返回完整路径（缓存命中且 ≥128KB，无需重新生成）
        
    if not os.path.exists(raw_file_path):
        log_message(f"ERROR, file [{filename}] cannot be found in RAW form", directory_path)
        return None

    # HEIF/HIF 格式（rawpy 不支持）：用 pillow-heif 解码全分辨率图
    heif_exts = {'.hif', '.heif', '.heic'}
    if file_ext.lower() in heif_exts:
        return _raw_to_jpeg_via_heif(raw_file_path, jpg_file_path, directory_path)

    try:
        with rawpy.imread(raw_file_path) as raw:
            thumbnail = raw.extract_thumb()
            if thumbnail is None:
                log_message(f"DEBUG: rawpy extract_thumb is None for {filename}", directory_path)
                return None
            if thumbnail.format == rawpy.ThumbFormat.JPEG:
                with open(jpg_file_path, 'wb') as f:
                    f.write(thumbnail.data)
            elif thumbnail.format == rawpy.ThumbFormat.BITMAP:
                imageio.imsave(jpg_file_path, thumbnail.data)
                # 成功转换——已由 photo_processor 的批量日志统计，无需逐文件记录
            return jpg_file_path
    except rawpy._rawpy.LibRawFileUnsupportedError:
        # LibRaw 不支持的格式（如 Sony A7M5 的已压缩 ARW）
        log_message(f"DEBUG: rawpy unsupported format for {filename}, falling back to ExifTool", directory_path)
        return _raw_to_jpeg_via_exiftool(raw_file_path, jpg_file_path, directory_path)
    except Exception as e:
        log_message(f"Error occurred while converting the RAW file:{raw_file_path}, Error: {e}", directory_path)
        # 即使是普通异常，也尝试走一次 ExifTool 回退（增加容错）
        return _raw_to_jpeg_via_exiftool(raw_file_path, jpg_file_path, directory_path)


def _raw_to_jpeg_via_heif(raw_file_path, jpg_file_path, directory_path):
    """使用 pillow-heif 解码 HEIF/HIF 文件并保存为 JPEG。"""
    try:
        import pillow_heif
        from PIL import Image as _Image
        heif_file = pillow_heif.read_heif(raw_file_path)
        img = _Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw").convert("RGB")
        img.save(jpg_file_path, "JPEG", quality=92)
        log_message(f"[HEIF] pillow-heif 解码成功: {img.size[0]}x{img.size[1]}", directory_path)
        return jpg_file_path
    except ImportError:
        log_message("pillow-heif 未安装，回退到 ExifTool", directory_path)
        return _raw_to_jpeg_via_exiftool(raw_file_path, jpg_file_path, directory_path)
    except Exception as e:
        log_message(f"HEIF 解码失败 ({os.path.basename(raw_file_path)}): {e}", directory_path)
        return _raw_to_jpeg_via_exiftool(raw_file_path, jpg_file_path, directory_path)


def _raw_to_jpeg_via_exiftool(raw_file_path, jpg_file_path, directory_path):
    """
    使用 ExifTool 从 RAW 提取内嵌 JPEG (V4.2.1: 使用统一的 ExifToolManager)
    用于 LibRaw 不支持的格式（如 Sony A7M5 的已压缩 ARW）。
    """
    # Use standalone CLI calls here because binary extraction through the
    # persistent ExifToolManager path is significantly slower for A7M5 compressed ARWs.
    
    # 按优先级尝试提取不同的内嵌图
    for tag in ["-JpgFromRaw", "-PreviewImage", "-ThumbnailImage"]:
        try:
            # 使用常驻进程提取二进制
            stdout_bytes = _extract_binary_via_exiftool_cli(raw_file_path, tag)
            
            if stdout_bytes and len(stdout_bytes) > 1000:
                with open(jpg_file_path, "wb") as f:
                    f.write(stdout_bytes)
                log_message(f"ExifTool {tag} fallback OK: {os.path.basename(raw_file_path)}", directory_path)
                return jpg_file_path
        except Exception as e:
            log_message(f"ExifTool {tag} fallback failed for {os.path.basename(raw_file_path)}: {e}", directory_path)
            continue

    # 所有方法均失败——记录友好信息，不 raise 让流程继续
    log_message(
        f"暂不支持此 RAW 格式 ({os.path.basename(raw_file_path)})，"
        "将在后续版本修复。建议使用无压缩 RAW 或 JPEG 拍摄。",
        directory_path
    )
    return None

def reset(directory, log_callback=None, i18n=None):
    """
    重置工作目录：
    1. 清理临时文件和日志
    2. 重置所有照片的EXIF元数据（Rating、Pick、Label）

    Args:
        directory: 工作目录
        log_callback: 日志回调函数（可选，用于UI显示）
        i18n: I18n instance for internationalization (optional)
    """
    def log(msg):
        """统一日志输出"""
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if not os.path.exists(directory):
        if i18n:
            log(i18n.t("errors.dir_not_exist", directory=directory))
        else:
            log(f"ERROR: {directory} does not exist")
        return False

    if i18n:
        log(i18n.t("logs.reset_start"))
        log(i18n.t("logs.reset_dir", directory=directory))
    else:
        log(f"🔄 开始重置目录: {directory}")

    # 1. 清理临时文件、日志和Crop图片
    if i18n:
        log("\n" + i18n.t("logs.clean_tmp"))
    else:
        log("\n📁 清理临时文件...")

    # 1.1 清理 _tmp 目录（包含所有临时文件、日志、crop图片等）
    tmp_dir = os.path.join(directory, ".superpicky")
    if os.path.exists(tmp_dir) and os.path.isdir(tmp_dir):
        try:
            # 先逐文件清空（含 ExFAT 上的 ._* 资源分叉文件），再删目录
            import stat
            for dirpath, dirnames, filenames in os.walk(tmp_dir, topdown=False):
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        os.remove(fpath)
                    except Exception:
                        try:
                            os.chmod(fpath, stat.S_IWRITE | stat.S_IREAD)
                            os.remove(fpath)
                        except Exception:
                            pass
                for dname in dirnames:
                    dpath = os.path.join(dirpath, dname)
                    try:
                        os.rmdir(dpath)
                    except Exception:
                        pass
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if i18n:
                log(i18n.t("logs.tmp_deleted"))
            else:
                log(f"  ✅ 已删除 _tmp 目录及其所有内容")
        except Exception as e:
            if i18n:
                log(i18n.t("logs.tmp_delete_failed", error=str(e)))
            else:
                log(f"  ❌ 删除 _tmp 目录失败: {e}")
            # 尝试使用系统命令强制删除（macOS/Linux）
            try:
                import subprocess
                if os.name == 'nt':
                     subprocess.run(['cmd', '/c', 'rd', '/s', '/q', tmp_dir],
                                    check=True, timeout=120)
                else:
                    subprocess.run(['rm', '-rf', tmp_dir], check=True, timeout=120)
                if i18n:
                    log(i18n.t("logs.tmp_force_delete"))
                else:
                    log(f"  ✅ 使用系统命令强制删除 _tmp 成功")
            except Exception as e2:
                if i18n:
                    log(i18n.t("logs.tmp_force_failed", error=str(e2)))
                else:
                    log(f"  ❌ 强制删除也失败: {e2}")

    # 1.2 清理旧版本的日志和CSV文件（如果存在于根目录）
    files_to_clean = [".report.csv", ".report.db", ".process_log.txt", "superpicky.log"]
    for name in files_to_clean:
        path = os.path.join(directory, name)
        if os.path.exists(path) and os.path.isfile(path):
            try:
                os.remove(path)
                if i18n:
                    log(i18n.t("logs.file_deleted", name=name))
                else:
                    log(f"  ✅ 已删除: {name}")
            except Exception as e:
                if i18n:
                    log(i18n.t("logs.delete_failed", filename=name, error=e))
                else:
                    log(f"  ❌ 删除失败 {name}: {e}")

    # 1.3 清理临时JPEG文件（tmp_*.jpg，如果有遗留在根目录的）
    tmp_jpg_pattern = os.path.join(directory, "tmp_*.jpg")
    tmp_jpg_files = glob.glob(tmp_jpg_pattern)
    tmp_jpg_files = [f for f in tmp_jpg_files if not os.path.basename(f).startswith('.')]
    if tmp_jpg_files:
        if i18n:
            log(i18n.t("logs.tmp_jpeg_found", count=len(tmp_jpg_files)))
        else:
            log(f"  发现 {len(tmp_jpg_files)} 个临时JPEG文件（tmp_*.jpg），正在删除...")
        deleted_tmp = 0
        for tmp_file in tmp_jpg_files:
            try:
                os.remove(tmp_file)
                deleted_tmp += 1
            except Exception as e:
                if i18n:
                    log(i18n.t("logs.delete_failed", filename=os.path.basename(tmp_file), error=e))
                else:
                    log(f"  ❌ 删除失败 {os.path.basename(tmp_file)}: {e}")
        if deleted_tmp > 0:
            if i18n:
                log(i18n.t("logs.tmp_jpeg_done", count=deleted_tmp))
            else:
                log(f"  ✅ 临时JPEG删除完成: {deleted_tmp} 成功")

    # 2. 删除所有XMP侧车文件（Lightroom会优先读取XMP）
    if i18n:
        log("\n" + i18n.t("logs.delete_xmp"))
    else:
        log("\n🗑️  删除XMP侧车文件...")
    xmp_pattern = os.path.join(directory, "**/*.xmp")
    xmp_files = glob.glob(xmp_pattern, recursive=True)
    # 过滤掉隐藏文件
    xmp_files = [f for f in xmp_files if not os.path.basename(f).startswith('.')]
    if xmp_files:
        if i18n:
            log(i18n.t("logs.xmp_found", count=len(xmp_files)))
        else:
            log(f"  发现 {len(xmp_files)} 个XMP文件，正在删除...")
        deleted_xmp = 0
        for xmp_file in xmp_files:
            try:
                os.remove(xmp_file)
                deleted_xmp += 1
            except Exception as e:
                if i18n:
                    log(i18n.t("logs.delete_failed", filename=os.path.basename(xmp_file), error=e))
                else:
                    log(f"  ❌ 删除失败 {os.path.basename(xmp_file)}: {e}")
        if i18n:
            log(i18n.t("logs.xmp_deleted", count=deleted_xmp))
        else:
            log(f"  ✅ XMP文件删除完成: {deleted_xmp} 成功")
    else:
        if i18n:
            log(i18n.t("logs.xmp_not_found"))
        else:
            log("  ℹ️  未找到XMP文件")

    # 3. 重置所有图片文件的EXIF元数据
    if i18n:
        log("\n" + i18n.t("logs.reset_exif"))
    else:
        log("\n🏷️  重置EXIF元数据...")

    # 支持的图片格式
    image_extensions = ['*.NEF', '*.nef', '*.CR2', '*.cr2', '*.ARW', '*.arw',
                       '*.JPG', '*.jpg', '*.JPEG', '*.jpeg', '*.DNG', '*.dng']

    # 收集所有图片文件（跳过隐藏文件）
    image_files = []
    for ext in image_extensions:
        pattern = os.path.join(directory, ext)
        files = glob.glob(pattern)
        # 过滤掉隐藏文件（以.开头的文件）
        files = [f for f in files if not os.path.basename(f).startswith('.')]
        image_files.extend(files)

    # V3.9.4: 对文件列表执行去重（Windows 下 *.NEF 和 *.nef 匹配结果相同，会导致计数翻倍）
    image_files = sorted(list(set(os.path.abspath(f) for f in image_files)))

    if image_files:
        if i18n:
            log(i18n.t("logs.images_found", count=len(image_files)))
        else:
            log(f"  发现 {len(image_files)} 个图片文件")

        try:
            # 使用批量重置功能（传递log_callback和i18n）
            manager = get_exiftool_manager()
            stats = manager.batch_reset_metadata(image_files, log_callback=log_callback, i18n=i18n)

            if i18n:
                log(i18n.t("logs.batch_complete", success=stats['success'], skipped=stats.get('skipped', 0), failed=stats['failed']))
            else:
                log(f"  ✅ EXIF重置完成: {stats['success']} 成功, {stats.get('skipped', 0)} 跳过(4-5星), {stats['failed']} 失败")

        except Exception as e:
            if i18n:
                log(i18n.t("logs.exif_reset_failed", error=str(e)))
            else:
                log(f"  ❌ EXIF重置失败: {e}")
            return False
    else:
        if i18n:
            log(i18n.t("logs.no_images"))
        else:
            log("  ⚠️  未找到图片文件")

    if i18n:
        log("\n" + i18n.t("logs.reset_complete"))
    else:
        log("\n✅ 目录重置完成！")
    return True


# ============================================================================
# 高级重置 / Advanced reset（无 manifest 时按「SuperPicky 生成目录」识别并摊平）
# Advanced reset: when no manifest exists, recognize SuperPicky-generated folders
# (by name) and recursively flatten their files back to the root, leaving any
# user-created folders untouched so it can never move the wrong thing.
# ============================================================================

# 「其他鸟类」目录名（照片端 logs.folder_other_birds 的中英取值）
# "Other Birds" folder labels (zh/en values of logs.folder_other_birds).
_OTHER_BIRDS_LABELS = ("其他鸟类", "Other_Birds")
# 旧版遗留评分目录名 / legacy rating folder names
_LEGACY_RATING_FOLDERS = ("2星_良好_锐度", "2星_良好_美学")

_SUPERPICKY_FOLDER_SET = None  # 缓存：所有「SuperPicky 生成目录名」集合


def _bird_reference_db_path():
    """
    定位鸟种参考库 bird_reference.sqlite（dev + 打包均可）。
    Locate bird_reference.sqlite for both dev and frozen builds.
    """
    try:
        import birdid
        candidate = os.path.join(os.path.dirname(birdid.__file__), "data", "bird_reference.sqlite")
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    # 兜底：相对项目根 / fallback relative to project root
    candidate = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "birdid", "data", "bird_reference.sqlite")
    return candidate if os.path.exists(candidate) else None


def _build_superpicky_folder_set():
    """
    构建「SuperPicky 生成目录名」匹配集：
        鸟名(中文 chinese_simplified + 英文 english_name[空格→下划线])
        ∪ 评分目录名(中文 RATING_FOLDER_NAMES + 英文 RATING_FOLDER_NAMES_EN + legacy)
        ∪ 其他鸟类(中/英)
    burst_ 前缀单独判断（不入集合）。结果缓存。

    Build the set of folder names SuperPicky generates, so advanced reset only
    touches those (never user folders).
    """
    global _SUPERPICKY_FOLDER_SET
    if _SUPERPICKY_FOLDER_SET is not None:
        return _SUPERPICKY_FOLDER_SET

    names = set()
    # 评分目录（中英两套 + legacy）/ rating folders (zh + en + legacy)
    try:
        from constants import RATING_FOLDER_NAMES, RATING_FOLDER_NAMES_EN
        names.update(RATING_FOLDER_NAMES.values())
        names.update(RATING_FOLDER_NAMES_EN.values())
    except Exception:
        pass
    names.update(_LEGACY_RATING_FOLDERS)
    names.update(_OTHER_BIRDS_LABELS)

    # 鸟名（中文原样 + 英文空格转下划线，与照片端命名一致）
    db_path = _bird_reference_db_path()
    if db_path:
        try:
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT chinese_simplified, english_name FROM BirdCountInfo")
                for cn, en in cur.fetchall():
                    if cn:
                        names.add(str(cn).strip())
                    if en:
                        names.add(str(en).strip().replace(" ", "_"))
        except Exception:
            pass

    names.discard("")
    _SUPERPICKY_FOLDER_SET = names
    return names


def _is_superpicky_folder(name: str) -> bool:
    """目录名是否为 SuperPicky 生成（鸟名/评分/其他鸟类/burst_）。"""
    if name.startswith("burst_"):
        return True
    return name in _build_superpicky_folder_set()


def is_ignorable_reset_residue(filename: str) -> bool:
    """
    判断 reset 后是否可以忽略/清理的系统元数据文件。

    参数:
    filename (str): 文件名，不需要包含完整路径。

    返回:
    bool: True 表示这是可安全删除的系统元数据残留。

    Determine whether a post-reset file is ignorable OS metadata.

    Parameters:
    filename (str): File name only; a full path is not required.

    Return:
    bool: True when the file is safe-to-remove OS metadata residue.
    """
    lower_name = filename.lower()
    return (
        filename.startswith("._")
        or lower_name in {".ds_store", "thumbs.db", "desktop.ini"}
    )


def cleanup_ignorable_reset_residue(directory: str) -> int:
    """
    递归清理 reset 目录中的系统元数据残留文件。

    参数:
    directory (str): 需要清理的目录路径。

    返回:
    int: 成功删除的残留文件数量。

    Recursively remove ignorable OS metadata residue from a reset directory.

    Parameters:
    directory (str): Directory to clean.

    Return:
    int: Number of residue files successfully removed.
    """
    removed = 0
    if not os.path.isdir(directory):
        return removed

    for root, _dirs, files in os.walk(directory):
        for filename in files:
            if not is_ignorable_reset_residue(filename):
                continue
            path = os.path.join(root, filename)
            try:
                if os.path.islink(path) or os.path.isfile(path):
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
    return removed


def force_flatten_directory(directory, log_callback=None, i18n=None) -> dict:
    """
    高级重置核心：把「SuperPicky 生成的顶层目录」内的文件递归移回 directory 根。

    仅处理顶层目录名匹配（鸟名 中+英 / 评分名 中+英 / 其他鸟类 / burst_）的目录，
    用户自建目录原样不动。同名文件跳过、不覆盖、只移动不删除；移完删除清空的目录。
    隐藏文件 / AppleDouble(._*) / .superpicky 内部目录一律跳过（不污染根目录；
    .superpicky 由随后的 reset() 统一删除）。

    Args:
        directory: 目标目录（用户选中的根目录）
        log_callback: 日志回调
        i18n: I18n 实例

    Returns:
        dict: {'moved': int, 'skipped': int, 'dirs_removed': int, 'folders_matched': int}

    Advanced-reset core: recursively move files out of SuperPicky-generated top-level
    folders back to the root; user folders are left untouched. Conflicts are skipped
    (never overwritten); nothing is deleted except now-empty matched folders.
    """
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    stats = {"moved": 0, "skipped": 0, "dirs_removed": 0, "folders_matched": 0}
    if not os.path.isdir(directory):
        return stats

    if i18n:
        log(i18n.t("logs.adv_flatten_start"))
    else:
        log("\n🧹 高级重置：识别 SuperPicky 目录并摊平文件...")

    try:
        top_entries = sorted(os.listdir(directory))
    except Exception:
        return stats

    matched_dirs = []
    for entry in top_entries:
        entry_path = os.path.join(directory, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry.startswith("."):  # 隐藏 / .superpicky 等内部目录，跳过
            continue
        if _is_superpicky_folder(entry):
            matched_dirs.append(entry_path)

    stats["folders_matched"] = len(matched_dirs)

    for top_dir in matched_dirs:
        # 递归把该匹配目录内的所有文件移回根（任意深度）
        for root, dirs, files in os.walk(top_dir):
            for fname in files:
                if fname.startswith("."):  # ._* / .DS_Store 等
                    continue
                src = os.path.join(root, fname)
                dst = os.path.join(directory, fname)
                if os.path.exists(dst):
                    stats["skipped"] += 1
                    if i18n:
                        log(i18n.t("logs.restore_skipped_exists", filename=fname))
                    else:
                        log(f"  ⏭️  同名跳过（不覆盖）: {fname}")
                    continue
                try:
                    shutil.move(src, dst)
                    stats["moved"] += 1
                except Exception as e:
                    if i18n:
                        log(i18n.t("logs.move_failed", filename=fname, error=e))
                    else:
                        log(f"  ❌ 移动失败 {fname}: {e}")

        # 删除清空的子目录（最深优先），再删顶层匹配目录
        for root, dirs, files in os.walk(top_dir, topdown=False):
            try:
                if not os.listdir(root):
                    os.rmdir(root)
                    stats["dirs_removed"] += 1
            except Exception:
                pass

    if i18n:
        log(i18n.t("logs.adv_flatten_done",
                   moved=stats["moved"], folders=stats["folders_matched"],
                   skipped=stats["skipped"]))
    else:
        log(f"  ✅ 高级重置完成：识别 {stats['folders_matched']} 个目录，"
            f"移回 {stats['moved']} 个文件，同名跳过 {stats['skipped']} 个")
    return stats
