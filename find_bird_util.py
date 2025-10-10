import os
import rawpy
import imageio
from utils import log_message
from exiftool_manager import get_exiftool_manager
import glob

def raw_to_jpeg(raw_file_path):
    filename = os.path.basename(raw_file_path)
    file_prefix, _ = os.path.splitext(filename)
    directory_path = raw_file_path[:-len(filename)]
    jpg_file_path = os.path.join(directory_path, (file_prefix + ".jpg"))
    log_message(f"CONVERSION: Filename is [{filename}], Destination file path is [{jpg_file_path}]", directory_path)

    if os.path.exists(jpg_file_path):
        log_message(f"ERROR, file [{filename}] already exists in JPG/JPEG format", directory_path)
        return False
    if not os.path.exists(raw_file_path):
        log_message(f"ERROR, file [{filename}] cannot be found in RAW form", directory_path)
        return False

    try:
        with rawpy.imread(raw_file_path) as raw:
            thumbnail = raw.extract_thumb()
            if thumbnail is None:
                return None
            if thumbnail.format == rawpy.ThumbFormat.JPEG:
                with open(jpg_file_path, 'wb') as f:
                    f.write(thumbnail.data)
            elif thumbnail.format == rawpy.ThumbFormat.BITMAP:
                imageio.imsave(filename + '.jpg', thumbnail.data)
            log_message(f"CONVERSION: RAW extract to JPEG: {raw_file_path} -> {jpg_file_path}", directory_path)
    except Exception as e:
        log_message(f"Error occurred while converting the RAW file:{raw_file_path}, Error: {e}", directory_path)

def reset(directory, log_callback=None):
    """
    重置工作目录：
    1. 清理临时文件和日志
    2. 重置所有照片的EXIF元数据（Rating、Pick、Label）

    Args:
        directory: 工作目录
        log_callback: 日志回调函数（可选，用于UI显示）
    """
    def log(msg):
        """统一日志输出"""
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if not os.path.exists(directory):
        log(f"ERROR: {directory} does not exist")
        return False

    log(f"🔄 开始重置目录: {directory}")

    # 1. 清理临时文件、日志和Crop图片
    log("\n📁 清理临时文件...")
    files_to_clean = [".report.csv", ".process_log.txt"]

    # 清理日志和CSV
    for name in files_to_clean:
        path = os.path.join(directory, name)
        if os.path.exists(path) and os.path.isfile(path):
            try:
                os.remove(path)
                log(f"  ✅ 已删除: {name}")
            except Exception as e:
                log(f"  ❌ 删除失败 {name}: {e}")

    # Crop图片现在保存在~/Documents/SuperPicky/目录，不在源目录
    # 所以这里不需要清理Crop文件了

    # 2. 删除所有XMP侧车文件（Lightroom会优先读取XMP）
    log("\n🗑️  删除XMP侧车文件...")
    xmp_pattern = os.path.join(directory, "*.xmp")
    xmp_files = glob.glob(xmp_pattern)
    if xmp_files:
        log(f"  发现 {len(xmp_files)} 个XMP文件，正在删除...")
        deleted_xmp = 0
        for xmp_file in xmp_files:
            try:
                os.remove(xmp_file)
                deleted_xmp += 1
            except Exception as e:
                log(f"  ❌ 删除失败 {os.path.basename(xmp_file)}: {e}")
        log(f"  ✅ XMP文件删除完成: {deleted_xmp} 成功")
    else:
        log("  ℹ️  未找到XMP文件")

    # 3. 重置所有图片文件的EXIF元数据
    log("\n🏷️  重置EXIF元数据...")

    # 支持的图片格式
    image_extensions = ['*.NEF', '*.nef', '*.CR2', '*.cr2', '*.ARW', '*.arw',
                       '*.JPG', '*.jpg', '*.JPEG', '*.jpeg', '*.DNG', '*.dng']

    # 收集所有图片文件
    image_files = []
    for ext in image_extensions:
        pattern = os.path.join(directory, ext)
        image_files.extend(glob.glob(pattern))

    if image_files:
        log(f"  发现 {len(image_files)} 个图片文件")

        try:
            # 使用批量重置功能（传递log_callback）
            manager = get_exiftool_manager()
            stats = manager.batch_reset_metadata(image_files, log_callback=log_callback)

            log(f"  ✅ EXIF重置完成: {stats['success']} 成功, {stats.get('skipped', 0)} 跳过(4-5星), {stats['failed']} 失败")

        except Exception as e:
            log(f"  ❌ EXIF重置失败: {e}")
            return False
    else:
        log("  ⚠️  未找到图片文件")

    log("\n✅ 目录重置完成！")
    return True