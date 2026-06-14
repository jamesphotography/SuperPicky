import os
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
        check=False
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
                     subprocess.run(['cmd', '/c', 'rd', '/s', '/q', tmp_dir], check=True)
                else:
                    subprocess.run(['rm', '-rf', tmp_dir], check=True)
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
