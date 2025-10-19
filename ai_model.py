import os
import time
import cv2
import numpy as np
from ultralytics import YOLO
from utils import log_message, write_to_csv
from config import config
from sharpness import MaskBasedSharpnessCalculator
from iqa_scorer import get_iqa_scorer
from advanced_config import get_advanced_config

# 禁用 Ultralytics 设置警告
os.environ['YOLO_VERBOSE'] = 'False'


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


# 锐度计算器将根据用户选择动态创建
def _get_sharpness_calculator(normalization_mode=None):
    """
    获取锐度计算器实例

    Args:
        normalization_mode: 归一化模式 (None, 'sqrt', 'linear', 'log', 'gentle')

    Returns:
        MaskBasedSharpnessCalculator 实例
    """
    return MaskBasedSharpnessCalculator(method='variance', normalization=normalization_mode)

# 初始化全局 IQA 评分器（延迟加载）
_iqa_scorer = None


def _get_iqa_scorer():
    """获取 IQA 评分器单例"""
    global _iqa_scorer
    if _iqa_scorer is None:
        _iqa_scorer = get_iqa_scorer(device='mps')
    return _iqa_scorer


def detect_and_draw_birds(image_path, model, output_path, dir, ui_settings):
    """
    检测并标记鸟类（V3.1 - 简化版，移除预览功能）

    Args:
        image_path: 图片路径
        model: YOLO模型
        output_path: 输出路径（带框图片）
        dir: 工作目录
        ui_settings: [ai_confidence, sharpness_threshold, nima_threshold, save_crop, normalization_mode]
    """
    # V3.1: 从 ui_settings 获取参数
    ai_confidence = ui_settings[0] / 100  # AI置信度：50-100 -> 0.5-1.0（仅用于过滤）
    sharpness_threshold = ui_settings[1]  # 锐度阈值：6000-9000
    nima_threshold = ui_settings[2]       # NIMA美学阈值：5.0-6.0

    # V3.1: 不再保存Crop图片（移除预览功能）
    save_crop = False

    # 锐度归一化模式（V3.1默认log_compression）
    normalization_mode = ui_settings[4] if len(ui_settings) >= 5 else 'log_compression'

    # 根据用户选择的归一化模式创建锐度计算器
    sharpness_calculator = _get_sharpness_calculator(normalization_mode)

    found_bird = False
    bird_sharp = False
    bird_result = False
    nima_score = None  # 美学评分（全图）
    brisque_score = None  # 技术质量评分（crop图）
    # V3.1: 移除 bird_dominant, bird_centred（不再使用）

    # 使用配置检查文件类型
    if not config.is_jpg_file(image_path):
        log_message("ERROR: not a jpg file", dir)
        return None

    if not os.path.exists(image_path):
        log_message(f"ERROR: in detect_and_draw_birds, {image_path} not found", dir)
        return None

    # 记录总处理开始时间
    total_start = time.time()

    # Step 1: 图像预处理
    step_start = time.time()
    image = preprocess_image(image_path)
    height, width, _ = image.shape
    preprocess_time = (time.time() - step_start) * 1000
    log_message(f"  ⏱️  [1/7] 图像预处理: {preprocess_time:.1f}ms", dir)

    # Step 2: YOLO推理
    step_start = time.time()
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
            # 返回"无鸟"结果（V3.1）
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
                "NIMA美学": "-",
                "BRISQUE技术": "-",
                "星等": "❌",
                "评分": -1,
                "类别ID": "-"
            }
            write_to_csv(data, dir, False)
            return found_bird, bird_result, 0.0, 0.0, None, None

    yolo_time = (time.time() - step_start) * 1000
    log_message(f"  ⏱️  [2/7] YOLO推理: {yolo_time:.1f}ms", dir)

    # Step 3: 解析检测结果
    step_start = time.time()
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

    parse_time = (time.time() - step_start) * 1000
    log_message(f"  ⏱️  [3/7] 结果解析: {parse_time:.1f}ms", dir)

    # 如果没有找到鸟，记录到CSV并返回（V3.1）
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
            "NIMA美学": "-",
            "BRISQUE技术": "-",
            "星等": "❌",
            "评分": -1,
            "类别ID": "-"
        }
        write_to_csv(data, dir, False)
        return found_bird, bird_result, 0.0, 0.0, None, None

    # Step 4: 计算 NIMA 美学评分（使用全图，只计算一次）
    nima_time = 0
    if bird_idx != -1:
        step_start = time.time()
        try:
            scorer = _get_iqa_scorer()
            nima_score = scorer.calculate_nima(image_path)
            nima_time = (time.time() - step_start) * 1000
            if nima_score is not None:
                log_message(f"🎨 NIMA 美学评分: {nima_score:.2f} / 10", dir)
                log_message(f"  ⏱️  [4/7] NIMA评分: {nima_time:.1f}ms", dir)
        except Exception as e:
            nima_time = (time.time() - step_start) * 1000
            log_message(f"⚠️  NIMA 计算失败: {e}", dir)
            log_message(f"  ⏱️  [4/7] NIMA评分(失败): {nima_time:.1f}ms", dir)
            nima_score = None

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

            # V3.1: 不再保存Crop图片
            crop_path = None

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

            # Step 5: 计算 BRISQUE 技术质量评分（使用 crop 图片）
            step_start = time.time()
            try:
                scorer = _get_iqa_scorer()
                brisque_score = scorer.calculate_brisque(crop_img)
                brisque_time = (time.time() - step_start) * 1000
                if brisque_score is not None:
                    log_message(f"🔧 BRISQUE 技术质量: {brisque_score:.2f} / 100 (越低越好)", dir)
                    log_message(f"  ⏱️  [5/7] BRISQUE评分: {brisque_time:.1f}ms", dir)
            except Exception as e:
                brisque_time = (time.time() - step_start) * 1000
                log_message(f"⚠️  BRISQUE 计算失败: {e}", dir)
                log_message(f"  ⏱️  [5/7] BRISQUE评分(失败): {brisque_time:.1f}ms", dir)
                brisque_score = None

            # Step 6: 使用新的基于掩码的锐度计算
            step_start = time.time()
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
                sharpness_result = sharpness_calculator.calculate(crop_img, mask_crop)
                real_sharpness = sharpness_result['total_sharpness']
                sharpness = sharpness_result['normalized_sharpness']
                effective_pixels = sharpness_result['effective_pixels']
            else:
                # 如果没有掩码，只在 save_crop=True 时保存普通裁剪图
                if crop_path:
                    cv2.imwrite(crop_path, crop_img)

                # 创建全1掩码（退化为整个BBox）
                full_mask = np.ones((h, w), dtype=np.uint8)
                sharpness_result = sharpness_calculator.calculate(crop_img, full_mask)
                real_sharpness = sharpness_result['total_sharpness']
                sharpness = sharpness_result['normalized_sharpness']
                effective_pixels = sharpness_result['effective_pixels']

            sharpness_time = (time.time() - step_start) * 1000
            log_message(f"  ⏱️  [6/7] 锐度计算: {sharpness_time:.1f}ms", dir)

            cv2.rectangle(image, (x, y), (x + w, y + h), (0, 0, 255), 2)

            # V3.1: 新的评分逻辑
            # 计算中心坐标（仅用于日志输出）
            center_x = (x + w / 2) / width
            center_y = (y + h / 2) / height

            # 日志输出
            nima_str = f"{nima_score:.2f}" if nima_score is not None else "-"
            brisque_str = f"{brisque_score:.2f}" if brisque_score is not None else "-"
            log_message(f" AI: {conf:.2f} - Class: {class_id} "
                        f"- Sharpness:{real_sharpness:.2f} (Norm:{sharpness:.2f}) "
                        f"- Area:{area_ratio * 100:.2f}% - Pixels:{effective_pixels:,d}"
                        f" - NIMA:{nima_str}"
                        f" - BRISQUE:{brisque_str}"
                        f" - Center_x:{center_x:.2f} - Center_y:{center_y:.2f}", dir)

            # V3.1 星级评定规则：
            # V3.1: 使用高级配置的阈值
            # 1. 完全没鸟 → -1星（Rejected）
            # 2. 置信度/噪点/美学/锐度不达标 → 0星（技术质量差）
            # 3. 锐度 ≥ 阈值 且 NIMA ≥ 阈值 → 3星（优选）
            # 4. 锐度 ≥ 阈值 或 NIMA ≥ 阈值 → 2星（良好）
            # 5. 其他 → 1星（普通）

            adv_config = get_advanced_config()

            if conf < adv_config.min_confidence or \
               (brisque_score is not None and brisque_score > adv_config.max_brisque) or \
               (nima_score is not None and nima_score < adv_config.min_nima) or \
               sharpness < adv_config.min_sharpness:
                # 技术质量太差
                rating_stars = "0星"
                rating_value = 0
            elif sharpness >= sharpness_threshold and \
                 (nima_score is not None and nima_score >= nima_threshold):
                # 同时满足锐度和美学标准
                rating_stars = "⭐⭐⭐"
                rating_value = 3
                bird_result = True  # 标记为优选
            elif sharpness >= sharpness_threshold or \
                 (nima_score is not None and nima_score >= nima_threshold):
                # 满足锐度或美学标准之一
                rating_stars = "⭐⭐"
                rating_value = 2
            else:
                # 普通照片
                rating_stars = "⭐"
                rating_value = 1

            data = {
                "文件名": os.path.splitext(os.path.basename(image_path))[0],
                "是否有鸟": "是" if found_bird else "否",
                "置信度": f"{conf:.2f}",
                "X坐标": f"{center_x:.2f}",
                "Y坐标": f"{center_y:.2f}",
                "鸟占比": f"{area_ratio * 100:.2f}%",
                "像素数": f"{effective_pixels}",
                "原始锐度": f"{real_sharpness:.2f}",
                "归一化锐度": f"{sharpness:.2f}",
                "NIMA美学": f"{nima_score:.2f}" if nima_score is not None else "-",
                "BRISQUE技术": f"{brisque_score:.2f}" if brisque_score is not None else "-",
                "星等": rating_stars,
                "评分": rating_value,
                "类别ID": class_id
            }

            # Step 7: CSV写入
            step_start = time.time()
            write_to_csv(data, dir, False)
            csv_time = (time.time() - step_start) * 1000
            log_message(f"  ⏱️  [7/7] CSV写入: {csv_time:.1f}ms", dir)

    # --- 修改开始 ---
    # 只有在 found_bird 为 True 且 output_path 有效时，才保存带框的图片
    if found_bird and output_path:
        cv2.imwrite(output_path, image)
    # --- 修改结束 ---

    # 计算总处理时间
    total_time = (time.time() - total_start) * 1000
    log_message(f"  ⏱️  ========== 总耗时: {total_time:.1f}ms ==========", dir)

    # 返回 found_bird, bird_result, AI置信度, 归一化锐度, NIMA分数, BRISQUE分数（用于日志显示）
    bird_confidence = float(confidences[bird_idx]) if bird_idx != -1 else 0.0
    bird_sharpness = sharpness if bird_idx != -1 else 0.0
    return found_bird, bird_result, bird_confidence, bird_sharpness, nima_score, brisque_score