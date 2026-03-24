import os
import time
import cv2
import numpy as np
from ultralytics import YOLO
from tools.utils import log_message
from config import config
# V3.2: 移除未使用的 sharpness 计算器导入
from iqa_scorer import get_iqa_scorer
from advanced_config import get_advanced_config
# V4.2.1
from tools.i18n import get_i18n

# 禁用 Ultralytics 设置警告
os.environ['YOLO_VERBOSE'] = 'False'


def load_yolo_model(log_callback=None):
    """加载 YOLO 模型（使用最佳计算设备）"""
    model_path = config.ai.get_model_path()
    model = YOLO(str(model_path))

    # 使用统一的设备检测逻辑
    try:
        from config import get_best_device
        device = get_best_device()
        i18n = get_i18n()
        
        # 使用 i18n 翻译设备类型消息
        if device.type == 'mps':
            msg = i18n.t("ai.using_mps")
        elif device.type == 'cuda':
            msg = i18n.t("ai.using_cuda")
        else:
            msg = i18n.t("ai.using_cpu")
        
        # 使用日志回调或直接打印
        if log_callback:
            log_callback(msg, "info")
        else:
            print(msg)
    except Exception as e:
        i18n = get_i18n()
        error_msg = i18n.t("ai.device_detection_failed", error=str(e))
        if log_callback:
            log_callback(error_msg, "warning")
        else:
            print(error_msg)

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


# V3.2: 移除 _get_sharpness_calculator（锐度现在由 keypoint_detector 计算）

# 初始化全局 IQA 评分器（延迟加载）
_iqa_scorer = None


def _get_iqa_scorer():
    """获取 IQA 评分器单例"""
    global _iqa_scorer
    if _iqa_scorer is None:
        from config import get_best_device
        _iqa_scorer = get_iqa_scorer(device=get_best_device().type)
    return _iqa_scorer


def detect_and_draw_birds(image_path, model, output_path, dir, ui_settings, i18n=None, skip_nima=False, focus_point=None, report_db=None):
    """
    检测并标记鸟类（V4.2 - 支持多鸟对焦点选择）

    Args:
        image_path: 图片路径
        model: YOLO模型
        output_path: 输出路径（带框图片）
        dir: 工作目录
        ui_settings: [ai_confidence, sharpness_threshold, nima_threshold, save_crop, normalization_mode]
        i18n: I18n instance for internationalization (optional)
        skip_nima: 如果为True，跳过NIMA计算（用于双眼不可见的情况）
        focus_point: 对焦点坐标 (x, y)，归一化 0-1，用于多鸟时选择对焦的鸟
    
    Returns:
        元组 (found_bird, bird_result, confidence, sharpness, nima_score, bird_bbox, img_dims, bird_mask, bird_count)
        bird_count: 检测到的鸟的数量（V4.2 新增）
    """
    # V3.1: 从 ui_settings 获取参数
    ai_confidence = ui_settings[0] / 100  # AI置信度：50-100 -> 0.5-1.0（仅用于过滤）
    sharpness_threshold = ui_settings[1]  # 锐度阈值：6000-9000
    nima_threshold = ui_settings[2]       # NIMA美学阈值：5.0-6.0
    save_crop = ui_settings[3]            # 是否保存裁切（V4.1: 恢复支持）

    # V3.2: 移除未使用的 normalization_mode 和 sharpness_calculator
    # 锐度现在由 photo_processor 中的 keypoint_detector 计算

    found_bird = False
    bird_sharp = False
    bird_result = False
    nima_score = None  # 美学评分
    # V3.2: 移除 BRISQUE（不再使用）

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
    # V3.3: 简化日志，移除步骤详情
    # log_message(f"  ⏱️  [1/4] 图像预处理: {preprocess_time:.1f}ms", dir)

    # Step 2: YOLO推理
    step_start = time.time()
    # 使用最佳设备进行推理
    try:
        from config import get_best_device
        device = get_best_device()
        
        # 使用最佳设备进行推理
        results = model(image, device=device.type)
    except Exception as device_error:
        # 设备推理失败，降级到CPU
        t = i18n.t if i18n else get_i18n().t
        log_message(t("ai.device_inference_failed", error=device_error), dir)
        try:
            results = model(image, device='cpu')
        except Exception as cpu_error:
            log_message(t("ai.ai_inference_failed", error=cpu_error), dir)
            # 返回"无鸟"结果（V3.1）
            # V3.3: 使用英文列名
            data = {
                "filename": os.path.splitext(os.path.basename(image_path))[0],
                "has_bird": "no",
                "confidence": 0.0,
                "head_sharp": "-",
                "left_eye": "-",
                "right_eye": "-",
                "beak": "-",
                "nima_score": "-",
                "rating": -1
            }
            if report_db:
                report_db.insert_photo(data)
            return found_bird, bird_result, 0.0, 0.0, None, None, None, None, 0  # V4.2: 9 values including bird_count

    yolo_time = (time.time() - step_start) * 1000
    # V3.3: 简化日志，移除步骤详情
    # if i18n:
    #     log_message(i18n.t("logs.yolo_inference", time=yolo_time), dir)
    # else:
    #     log_message(f"  ⏱️  [2/4] YOLO推理: {yolo_time:.1f}ms", dir)

    # Step 3: 解析检测结果
    step_start = time.time()
    detections = results[0].boxes.xyxy.cpu().numpy()
    confidences = results[0].boxes.conf.cpu().numpy()
    class_ids = results[0].boxes.cls.cpu().numpy()

    # 获取掩码数据（如果是分割模型）
    masks = None
    if hasattr(results[0], 'masks') and results[0].masks is not None:
        masks = results[0].masks.data.cpu().numpy()

    # V4.2: 收集所有检测到的鸟
    all_birds = []
    for idx, (detection, conf, class_id) in enumerate(zip(detections, confidences, class_ids)):
        if int(class_id) == config.ai.BIRD_CLASS_ID:
            x1, y1, x2, y2 = detection
            all_birds.append({
                'idx': idx,
                'conf': float(conf),
                'bbox': (int(x1), int(y1), int(x2), int(y2))
            })
    
    bird_count = len(all_birds)
    
    # V4.2: 鸟选择策略
    bird_idx = -1
    if bird_count == 1:
        # 只有一只鸟，直接选择
        bird_idx = all_birds[0]['idx']
    elif bird_count > 1 and focus_point is not None:
        # 多只鸟，用对焦点选择
        fx, fy = focus_point  # 归一化坐标 0-1
        fx_px, fy_px = int(fx * width), int(fy * height)  # 转换为像素坐标
        
        found_by_focus = False
        for bird in all_birds:
            x1, y1, x2, y2 = bird['bbox']
            if x1 <= fx_px <= x2 and y1 <= fy_px <= y2:
                bird_idx = bird['idx']
                found_by_focus = True
                break
        
        if not found_by_focus:
            # 对焦点不在任何鸟身上，回退到置信度最高
            bird_idx = max(all_birds, key=lambda b: b['conf'])['idx']
    elif bird_count > 1:
        # 多只鸟但没有对焦点，选择置信度最高
        bird_idx = max(all_birds, key=lambda b: b['conf'])['idx']

    parse_time = (time.time() - step_start) * 1000
    # V3.3: 简化日志，移除步骤详情
    # if i18n:
    #     log_message(i18n.t("logs.result_parsing", time=parse_time), dir)
    # else:
    #     log_message(f"  ⏱️  [3/4] 结果解析: {parse_time:.1f}ms", dir)

    # 如果没有找到鸟，记录到CSV并返回（V3.1）
    if bird_idx == -1:
        # V3.3: 使用英文列名
        data = {
            "filename": os.path.splitext(os.path.basename(image_path))[0],
            "has_bird": "no",
            "confidence": 0.0,
            "head_sharp": "-",
            "left_eye": "-",
            "right_eye": "-",
            "beak": "-",
            "nima_score": "-",
            "rating": -1
        }
        if report_db:
            report_db.insert_photo(data)
        return found_bird, bird_result, 0.0, 0.0, None, None, None, None, 0  # V4.2: 9 values including bird_count
    # V3.2: 移除 NIMA 计算（现在由 photo_processor 在裁剪区域上计算）
    # nima_score 设为 None，photo_processor 会重新计算
    nima_score = None
    
    # V3.9.3: 提前声明默认值，避免 continue 后变量未定义
    sharpness = 0.0
    x, y, w, h = 0, 0, 0, 0

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

            # V3.2: 移除 Step 5 锐度计算（现在由 photo_processor 中的 keypoint_detector 计算 head_sharpness）
            # 设置占位值以保持 CSV 兼容性
            real_sharpness = 0.0
            sharpness = 0.0
            effective_pixels = 0

            # V3.2: 移除 BRISQUE 评估（不再使用）

            cv2.rectangle(image, (x, y), (x + w, y + h), (0, 0, 255), 2)

            # V3.1: 新的评分逻辑
            # 计算中心坐标（仅用于日志输出）
            center_x = (x + w / 2) / width
            center_y = (y + h / 2) / height

            # V3.3: 简化日志，移除AI详情输出
            # log_message(f" AI: {conf:.2f} - Class: {class_id} "
            #             f"- Area:{area_ratio * 100:.2f}% - Pixels:{effective_pixels:,d}"
            #             f" - Center_x:{center_x:.2f} - Center_y:{center_y:.2f}", dir)

            # V3.2: 移除评分逻辑（现在由 photo_processor 的 RatingEngine 计算）
            # rating_value 设为占位值，photo_processor 会重新计算
            rating_value = 0

            # V3.3: 使用英文列名
            # V4.1: 添加路径信息
            try:
                rel_current_path = os.path.relpath(image_path, dir)
            except ValueError:
                rel_current_path = image_path # Fallback to absolute if different drive
                
            rel_debug_path = None
            
            # V4.1: 如果启用了保存裁切 (save_crop) 且没有指定 output_path，自动保存到 cache/debug
            if save_crop and not output_path:
                from tools.file_utils import ensure_hidden_directory
                
                superpicky_dir = os.path.join(dir, ".superpicky")
                cache_dir = os.path.join(superpicky_dir, "cache")
                # V4.2: Rename to yolo_debug for clarity
                debug_dir = os.path.join(cache_dir, "yolo_debug")
                
                try:
                    ensure_hidden_directory(superpicky_dir)
                    ensure_hidden_directory(debug_dir)
                    
                    filename = os.path.basename(image_path)
                    prefix, ext = os.path.splitext(filename)
                    output_path = os.path.join(debug_dir, f"{prefix}.jpg")
                except Exception:
                    pass


            
            data = {
                "filename": os.path.splitext(os.path.basename(image_path))[0],
                "has_bird": "yes" if found_bird else "no",
                "confidence": float(f"{conf:.2f}"),
                "head_sharp": "-",        # 将由 photo_processor 填充
                "left_eye": "-",          # 将由 photo_processor 填充
                "right_eye": "-",         # 将由 photo_processor 填充
                "beak": "-",              # 将由 photo_processor 填充
                "nima_score": float(f"{nima_score:.2f}") if nima_score is not None else "-",
                "rating": rating_value,
                # V4.1 Paths
                "current_path": rel_current_path,
                "debug_crop_path": None, # Will be filled by photo_processor
                "yolo_debug_path": None  # Will fill below
            }
            
            # Update yolo_debug_path if we generated it
            if found_bird and save_crop and output_path:
                try:
                    data["yolo_debug_path"] = os.path.relpath(output_path, dir)
                except ValueError:
                    data["yolo_debug_path"] = output_path

            # Step 5: CSV写入
            step_start = time.time()
            if report_db:
                report_db.insert_photo(data)
            csv_time = (time.time() - step_start) * 1000
            # V3.3: 简化日志
            # log_message(f"  ⏱️  [4/4] CSV写入: {csv_time:.1f}ms", dir)


    # 只有在 found_bird 为 True 且 output_path 有效时，才保存带框的图片
    if found_bird and output_path:
        cv2.imwrite(output_path, image)
    # --- 修改结束 ---

    # 计算总处理时间 (V3.3: 移除此处日志, 由 photo_processor 输出真正总耗时)
    total_time = (time.time() - total_start) * 1000
    # log_message(f"  ⏱️  ========== 总耗时: {total_time:.1f}ms ==========", dir)

    # 返回 found_bird, bird_result, AI置信度, 归一化锐度, NIMA分数, bbox, 图像尺寸, 分割掩码
    bird_confidence = float(confidences[bird_idx]) if bird_idx != -1 else 0.0
    bird_sharpness = sharpness if bird_idx != -1 else 0.0
    # bbox 格式: (x, y, w, h) - 在缩放后的图像上
    # img_dims 格式: (width, height) - 缩放后图像的尺寸，用于计算缩放比例
    bird_bbox = (x, y, w, h) if found_bird else None
    img_dims = (width, height) if found_bird else None
    
    # 获取对应鸟的掩码
    bird_mask = None
    if found_bird and masks is not None:
        # masks shape: (N, H, W) where N is number of detections
        # YOLO masks are usually same size as input image (or smaller and upscaled)
        # Ultralytics results.masks.data is usually (N, H, W) 
        # But we need to be careful about resizing if it's smaller
        # results.masks.data contains masks for all detections
        # We need the one corresponding to bird_idx
        try:
            # Mask is already resized to image size by ultralytics by default in modern versions
            # But let's verify if we need to resize
            # results[0].masks.data is a torch tensor on GPU/CPU
            raw_mask = results[0].masks.data[bird_idx].cpu().numpy()
            
            # Ensure mask is same size as processed image (width, height)
            if raw_mask.shape != (height, width):
                raw_mask = cv2.resize(raw_mask, (width, height), interpolation=cv2.INTER_NEAREST)
            
            # Convert to binary uint8 mask (0 or 255)
            # YOLO masks are float [0,1], threshold at 0.5
            bird_mask = (raw_mask > 0.5).astype(np.uint8) * 255
        except Exception as e:
            # Mask processing failed, ignore
            pass

    return found_bird, bird_result, bird_confidence, bird_sharpness, nima_score, bird_bbox, img_dims, bird_mask, bird_count