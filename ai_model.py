import os
import cv2
import numpy as np
from ultralytics import YOLO
from utils import log_message, write_to_csv
from config import config
from sharpness import MaskBasedSharpnessCalculator


def load_yolo_model():
    """加载 YOLO 模型（启用MPS GPU加速）"""
    model_path = config.ai.get_model_path()
    model = YOLO(str(model_path))

    # 尝试使用 Apple MPS (Metal Performance Shaders) GPU 加速
    try:
        import torch
        if torch.backends.mps.is_available():
            print("✅ 检测到 Apple GPU (MPS)，启用硬件加速")
            # YOLO模型会自动识别device参数
            # 注意：不需要手动 model.to('mps')，YOLO会在推理时自动处理
        else:
            print("⚠️  MPS不可用，使用CPU推理")
    except Exception as e:
        print(f"⚠️  GPU检测失败: {e}，使用CPU推理")

    return model


def preprocess_image(image_path, target_size=None):
    """预处理图像"""
    if target_size is None:
        target_size = config.ai.TARGET_IMAGE_SIZE
    
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    scale = target_size / max(w, h)
    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


# 初始化全局锐度计算器（使用掩码 + 方差 + sqrt归一化）
_sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization='sqrt')


def detect_and_draw_birds(image_path, model, output_path, dir, ui_settings, crop_temp_dir=None, center_threshold=None, preview_callback=None):
    """检测并标记鸟类"""
    # 从 ui_settings 获取参数
    ai_confidence = ui_settings[0] / 100  # AI置信度：0-100 -> 0.0-1.0
    area_threshold = ui_settings[1] / 100  # 鸟类占比：0.5-10 -> 0.005-0.1
    sharpness_threshold = ui_settings[2]   # 锐度阈值：0-300

    # 居中阈值：优先使用 ui_settings，否则使用参数或默认值
    if len(ui_settings) >= 4:
        center_threshold = ui_settings[3] / 100  # 5-40 -> 0.05-0.4
    elif center_threshold is None:
        center_threshold = config.ai.CENTER_THRESHOLD

    # 是否保存Crop图片（预览时总是临时保存）
    save_crop = ui_settings[4] if len(ui_settings) >= 5 else False
    # 如果有预览回调，强制生成crop图片（用于预览）
    if preview_callback:
        save_crop = True

    bird_dominant = False
    found_bird = False
    bird_sharp = False
    bird_centred = False
    bird_result = False

    # 使用配置检查文件类型
    if not config.is_jpg_file(image_path):
        log_message("ERROR: not a jpg file", dir)
        return None

    if not os.path.exists(image_path):
        log_message(f"ERROR: in detect_and_draw_birds, {image_path} not found", dir)
        return None

    image = preprocess_image(image_path)
    height, width, _ = image.shape

    # 使用MPS设备进行推理（如果可用），失败时降级到CPU
    try:
        # 尝试使用MPS设备
        results = model(image, device='mps')
    except Exception as mps_error:
        # MPS失败，降级到CPU
        log_message(f"⚠️  MPS推理失败，降级到CPU: {mps_error}", dir)
        try:
            results = model(image, device='cpu')
        except Exception as cpu_error:
            log_message(f"❌ AI推理完全失败: {cpu_error}", dir)
            # 返回"无鸟"结果
            data = {
                "文件名": os.path.splitext(os.path.basename(image_path))[0],
                "是否有鸟": "否",
                "置信度": "0.00",
                "X坐标": "-",
                "Y坐标": "-",
                "鸟占比": "0.00%",
                "像素数": "0",
                "原始锐度": "0.00",
                "归一化锐度": "0.00",
                "星等": "❌",
                "面积达标": "否",
                "居中": "否",
                "锐度达标": "否",
                "类别ID": "-"
            }
            write_to_csv(data, dir, False)
            return found_bird, bird_result, 0.0, 0.0

    detections = results[0].boxes.xyxy.cpu().numpy()
    confidences = results[0].boxes.conf.cpu().numpy()
    class_ids = results[0].boxes.cls.cpu().numpy()

    # 获取掩码数据（如果是分割模型）
    masks = None
    if hasattr(results[0], 'masks') and results[0].masks is not None:
        masks = results[0].masks.data.cpu().numpy()

    # 只处理面积最大的鸟
    bird_idx = -1
    max_area = 0

    for idx, (detection, conf, class_id) in enumerate(zip(detections, confidences, class_ids)):
        if int(class_id) == config.ai.BIRD_CLASS_ID:
            x1, y1, x2, y2 = detection
            area = (x2 - x1) * (y2 - y1)
            if area > max_area:
                max_area = area
                bird_idx = idx

    # 如果没有找到鸟，记录到CSV并返回
    if bird_idx == -1:
        data = {
            "文件名": os.path.splitext(os.path.basename(image_path))[0],
            "是否有鸟": "否",
            "置信度": "0.00",
            "X坐标": "-",
            "Y坐标": "-",
            "鸟占比": "0.00%",
            "像素数": "0",
            "原始锐度": "0.00",
            "归一化锐度": "0.00",
            "星等": "❌",
            "面积达标": "否",
            "居中": "否",
            "锐度达标": "否",
            "类别ID": "-"
        }
        write_to_csv(data, dir, False)
        return found_bird, bird_result, 0.0, 0.0

    # 只处理面积最大的那只鸟
    for idx, (detection, conf, class_id) in enumerate(zip(detections, confidences, class_ids)):
        # 跳过非鸟类或非最大面积的鸟
        if idx != bird_idx:
            continue
        x1, y1, x2, y2 = detection

        x = int(x1)
        y = int(y1)
        w = int(x2 - x1)
        h = int(y2 - y1)
        class_id = int(class_id)

        # 使用配置中的鸟类类别 ID
        if class_id == config.ai.BIRD_CLASS_ID:
            found_bird = True
            area_ratio = (w * h) / (width * height)
            filename = os.path.basename(image_path)

            # 只有在 save_crop=True 时才设置裁剪路径
            crop_path = None
            if save_crop:
                if crop_temp_dir:
                    crop_path = os.path.join(crop_temp_dir, 'Crop_' + filename)
                else:
                    # 如果没有提供裁剪目录，则保存到主工作目录
                    crop_path = os.path.join(dir, 'Crop_' + filename)

            x = max(0, min(x, width - 1))
            y = max(0, min(y, height - 1))
            w = min(w, width - x)
            h = min(h, height - y)

            if w <= 0 or h <= 0:
                log_message(f"ERROR: Invalid crop region for {image_path}", dir)
                continue

            crop_img = image[y:y + h, x:x + w]

            if crop_img is None or crop_img.size == 0:
                log_message(f"ERROR: Crop image is empty for {image_path}", dir)
                continue

            # 使用新的基于掩码的锐度计算
            mask_crop = None
            if masks is not None and idx < len(masks):
                mask = masks[idx]
                # 调整mask大小到图像尺寸
                if mask.shape != (height, width):
                    mask_resized = cv2.resize(mask, (width, height))
                else:
                    mask_resized = mask

                # 裁剪掩码到鸟的区域
                mask_crop = mask_resized[y:y + h, x:x + w]

                # 创建带掩码的裁剪图用于可视化
                crop_with_mask = crop_img.copy()

                # 创建彩色掩码（半透明绿色）
                mask_binary = (mask_crop > 0.5).astype(np.uint8)
                colored_mask = np.zeros_like(crop_img)
                colored_mask[:, :, 1] = 255  # 绿色通道

                # 应用半透明掩码
                crop_with_mask = cv2.addWeighted(
                    crop_with_mask, 1.0,
                    cv2.bitwise_and(colored_mask, colored_mask,
                                   mask=mask_binary),
                    0.4, 0
                )

                # 只有在 save_crop=True 时才保存带掩码的可视化图片
                if crop_path:
                    cv2.imwrite(crop_path, crop_with_mask)

                # 使用新算法计算锐度（基于掩码）
                sharpness_result = _sharpness_calculator.calculate(crop_img, mask_crop)
                real_sharpness = sharpness_result['total_sharpness']
                sharpness = sharpness_result['normalized_sharpness']
                effective_pixels = sharpness_result['effective_pixels']
            else:
                # 如果没有掩码，只在 save_crop=True 时保存普通裁剪图
                if crop_path:
                    cv2.imwrite(crop_path, crop_img)

                # 创建全1掩码（退化为整个BBox）
                full_mask = np.ones((h, w), dtype=np.uint8)
                sharpness_result = _sharpness_calculator.calculate(crop_img, full_mask)
                real_sharpness = sharpness_result['total_sharpness']
                sharpness = sharpness_result['normalized_sharpness']
                effective_pixels = sharpness_result['effective_pixels']

            cv2.rectangle(image, (x, y), (x + w, y + h), (0, 0, 255), 2)

            bird_sharp = sharpness >= sharpness_threshold
            bird_dominant = area_ratio >= area_threshold

            center_x = (x + w / 2) / width
            center_y = (y + h / 2) / height

            is_center_x_in_range = center_threshold <= center_x <= (1 - center_threshold)
            is_center_y_in_range = center_threshold <= center_y <= (1 - center_threshold)

            bird_centred = is_center_x_in_range and is_center_y_in_range

            if conf >= ai_confidence:  # 使用 >= 确保等于阈值时也能通过
                if bird_centred and bird_dominant and bird_sharp:
                    bird_result = True

            # 日志输出（新算法使用方差，数值较小）
            log_message(f" AI: {conf:.2f} - Class: {class_id} "
                        f"- Sharpness:{real_sharpness:.2f} (Norm:{sharpness:.2f}) "
                        f"- Area:{area_ratio * 100:.2f}% - Pixels:{effective_pixels:,d}"
                        f" - Center_x:{center_x:.2f} - Center_y:{center_y:.2f}", dir)

            # 计算星级（根据用户设置的阈值判断）
            # ui_settings[0] = AI置信度阈值 (%)
            # ui_settings[2] = 锐度阈值
            # 3星：满足所有优选条件（bird_result=True）
            # 2星：有鸟 + 置信度≥50% + 锐度≥50
            # 1星：有鸟但不满足2星条件
            if bird_result:
                rating_stars = "⭐⭐⭐"
            elif found_bird and conf >= 0.5 and sharpness >= 50:
                rating_stars = "⭐⭐"
            elif found_bird:
                rating_stars = "⭐"
            else:
                rating_stars = "❌"

            data = {
                "文件名": os.path.splitext(os.path.basename(image_path))[0],
                "是否有鸟": "是" if found_bird else "否",
                "置信度": f"{conf:.2f}",
                "X坐标": f"{(x + w / 2) / width:.2f}",
                "Y坐标": f"{(y + h / 2) / height:.2f}",
                "鸟占比": f"{area_ratio * 100:.2f}%",
                "像素数": f"{effective_pixels}",
                "原始锐度": f"{real_sharpness:.2f}",
                "归一化锐度": f"{sharpness:.2f}",
                "星等": rating_stars,
                "面积达标": "是" if bird_dominant else "否",
                "居中": "是" if bird_centred else "否",
                "锐度达标": "是" if bird_sharp else "否",
                "类别ID": class_id
            }

            write_to_csv(data, dir, False)

            # 如果有预览回调且crop图片存在，触发预览更新
            if preview_callback and crop_path and os.path.exists(crop_path):
                # 直接使用原始JPG路径，不复制（节省50-150ms/张）
                jpg_preview_path = image_path

                # 准备元数据
                metadata = {
                    'filename': os.path.basename(image_path),
                    'confidence': float(conf),
                    'sharpness': sharpness,
                    'area_ratio': area_ratio,
                    'centered': bird_centred,
                    'rating': 3 if bird_result else (2 if (conf >= 0.5 and sharpness >= 50) else 1),
                    'pick': 1 if bird_result else 0
                }
                # 传递crop路径和jpg路径
                preview_callback(crop_path, jpg_preview_path, metadata)

    # --- 修改开始 ---
    # 只有在 found_bird 为 True 且 output_path 有效时，才保存带框的图片
    if found_bird and output_path:
        cv2.imwrite(output_path, image)
    # --- 修改结束 ---

    # 返回 found_bird, bird_result, AI置信度, 归一化锐度（用于1星/2星判断）
    bird_confidence = float(confidences[bird_idx]) if bird_idx != -1 else 0.0
    bird_sharpness = sharpness if bird_idx != -1 else 0.0
    return found_bird, bird_result, bird_confidence, bird_sharpness