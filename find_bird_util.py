import os

from PIL import Image, ImageDraw, ImageFont
import rawpy
import textwrap

import torchvision.transforms as T
from torchvision.models.detection import fasterrcnn_resnet50_fpn
from torchvision.models.detection.faster_rcnn import FasterRCNN_ResNet50_FPN_Weights
import torch
import numpy as np
import cv2
import shutil


# 定义一个用于记录日志的函数
def log_message(message, dir):
    log_file_path = os.path.join(dir, "process_log.txt")  # gets path to log file
    log_file = open(log_file_path, "a")  # opens and allows writing
    log_file.write(message + "\n")  # writes message to log
    log_file.close()  # closes log file
    print(message)  # prints message in console


def write_text_on_existing_image(image_path, text, font_size=30, font_color=(255, 255, 255), max_width=1024):
    """
    Write paragraph text on an existing image at the top left corner.

    :param image_path: The path of the existing image.
    :param text: The paragraph text to write.
    :param font_size: The size of the font.
    :param font_color: The color of the font (R, G, B).
    :param max_width: The maximum width of the text area.
    """
    # 加载现有图片
    image = Image.open(image_path)

    # 调整图片大小，如果图片的宽度超过max_width
    if image.width > max_width:
        height = int(max_width * image.height / image.width)
        image = image.resize((max_width, height), Image.LANCZOS)

    # 准备绘制文字
    draw = ImageDraw.Draw(image)

    # 使用默认字体
    font_path = "/System/Library/Fonts/Helvetica.ttc"
    font = ImageFont.truetype(font_path, font_size)

    # 文本换行处理
    wrapped_text = textwrap.fill(text, width=int(max_width / font_size * 2))

    # 在图片左上角写文本
    draw.text((10, 10), wrapped_text, font=font, fill=font_color)

    # 保存修改后的图片，覆盖原文件
    image.save(image_path)


# checks and makes a new directory within directory_path and returns the path of the new dir
def make_new_dir(directory_path, new_dir_name):
    # gets path for the new directory
    new_dir_path = os.path.join(directory_path, new_dir_name)
    log_message(f"New directory path: {new_dir_path}", directory_path)

    # checks if directory already exists, make it if it does not
    if not os.path.exists(new_dir_path):
        os.makedirs(new_dir_path)

    return new_dir_path


# checks if the directory contains any raw files, makes sure that each raw file has a jpg counterpart
def directory_contains_raw(directory):
    # lists all the possible extensions for raws and jpgs
    raw_extensions = ['.nef', '.cr2', '.arw', '.raf', '.orf', '.rw2', '.pef', '.dng']
    jpg_extensions = ['.jpg', '.jpeg']

    # dictionaries to keep track of which files are raw and which aren't as well as their specific extension
    raw_dict = {}
    jpg_dict = {}

    # loops through the names of every file in the directory
    for filename in os.listdir(directory):

        file_prefix, file_ext = os.path.splitext(filename)  # gets file name and the extensions and splits it

        # adds files to their respective dictionaries based on their extension
        if file_ext.lower() in raw_extensions:
            raw_dict[file_prefix] = file_ext
        if file_ext.lower() in jpg_extensions:
            jpg_dict[file_prefix] = file_ext

    #
    for key, value in raw_dict.items():
        if key in jpg_dict.keys():
            log_message(f"{key} has raw and jpg files", directory)
            jpg_dict.pop(key)
            continue
        else:
            raw_to_jpeg(os.path.join(directory, key + value))
            log_message(f"{key} now has completed a conversion to jpg", directory)

    if len(jpg_dict.keys()) == 0:
        return True

    else:
        unusable_files = make_new_dir(directory, "Unusable Files")

        for key, value in jpg_dict.items():
            move_originals(key, directory, unusable_files)

    return False


def resize_image(image, max_length=1024):
    # 计算缩放比例
    width, height = image.size
    scaling_factor = max_length / max(width, height)

    # 如果需要缩放，进行缩放操作
    if scaling_factor < 1:
        new_width = int(width * scaling_factor)
        new_height = int(height * scaling_factor)
        return image.resize((new_width, new_height), Image.LANCZOS)
    return image


def resize_folder(directory):
    jpg_extensions = ['.jpg', '.jpeg']

    # creates a folder called "Resized" within the given directory to store all the resized images
    resized_path = make_new_dir(directory, "Resized")

    for filename in os.listdir(directory):
        log_message("=" * 30, directory)
        log_message(f"Begin resizing process on file {filename}", directory)
        file_path = os.path.join(directory, filename)
        file_prefix, file_ext = os.path.splitext(filename)

        # checks if its a jpg file
        if file_ext.lower() in jpg_extensions:
            log_message(f"file extension matches, resizing image\n", directory)
            # 打开并保存缩放后的 JPEG 图像
            with Image.open(file_path) as img:
                resized_img = resize_image(img)
                resized_jpeg_path = os.path.join(resized_path, filename)
                resized_img.save(resized_jpeg_path)


def raw_to_jpeg(raw_file_path):
    filename = os.path.basename(raw_file_path)
    file_prefix, file_ext = os.path.splitext(filename)

    directory_path = raw_file_path[:-len(filename)]
    jpg_file_path = os.path.join(directory_path, (file_prefix + ".jpg"))

    log_message(f"filename is: {filename}", directory_path)

    log_message(f"destination file path is: {jpg_file_path}", directory_path)

    if os.path.exists(jpg_file_path):
        log_message("ERROR, file already exists", directory_path)
        return False

    # 异常处理，确保转换过程中的错误被捕获并记录
    try:
        with rawpy.imread(raw_file_path) as raw:
            rgb = raw.postprocess(use_auto_wb=True)  # 使用自动白平衡
            image = Image.fromarray(rgb)
            image.save(jpg_file_path)

            log_message(f"RAW 文件转换为 JPEG: {raw_file_path} -> {jpg_file_path}", directory_path)
    except Exception as e:
        log_message(f"转换 RAW 文件时出错: {raw_file_path}, 错误: {e}", directory_path)


def calculate_sharpness(image):
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = np.var(laplacian)
    return sharpness


# 初始化 Faster R-CNN 模型
def get_model():
    model = fasterrcnn_resnet50_fpn(weights=FasterRCNN_ResNet50_FPN_Weights.DEFAULT)
    model.eval()
    return model


# 检测图片中的鸟类并绘制边界框
def detect_and_draw_birds(image_path, model, output_path, area_threshold=0.03, center_threshold=0.6):
    bird_dominant = False
    bird_detected = False
    bird_sharp = False
    bird_centred = False

    if ".jpg" not in image_path.lower() and ".jpeg" not in image_path.lower():
        print("ERROR, input file not an image of jpg format")
        return None

    # 确保打开的是图像文件
    with Image.open(image_path) as img:
        # 对图像进行转换和预处理
        transform = T.Compose([T.ToTensor()])
        image_tensor = transform(img).unsqueeze(0)

        image_width, image_height = img.size

        sharpness = 0
        area_ratio = 0.0
        center_distance_x = 0.0
        center_distance_y = 0.0

        # 使用模型进行预测
        with torch.no_grad():
            prediction = model(image_tensor)

        # 绘制检测到的鸟类的边界框
        draw = ImageDraw.Draw(img)
        for element in zip(prediction[0]['boxes'], prediction[0]['labels'], prediction[0]['scores']):
            box, label, score = element

            # checks if a bird has been detected and draws a box around it if true
            if score >= 0.8 and label == 16:  # 假设 16 是鸟类的标签， 0.7 confidence threshold
                draw.rectangle([(box[0], box[1]), (box[2], box[3])], outline="red", width=3)
                bird_detected = True

                # checks if the bird is at the centre of the image
                x1, y1, x2, y2 = box
                box_area = (x2 - x1) * (y2 - y1)
                image_area = image_width * image_height
                area_ratio = box_area / image_area

                center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
                center_distance_x = center_x / image_width
                center_distance_y = center_y / image_height

                print(area_ratio)
                print(center_distance_x, center_distance_y)

                if area_ratio >= area_threshold:
                    bird_dominant = True

                if center_distance_x <= center_threshold and center_distance_y <= center_threshold:
                    bird_centred = True

                # calculates sharpness
                bird_region = img.crop((int(box[0]), int(box[1]), int(box[2]), int(box[3])))
                sharpness = calculate_sharpness(bird_region)
                if sharpness >= 800:
                    bird_sharp = True

                print(f"Sharpness = {sharpness}")

        # 保存绘制了边界框的图片
        img.save(output_path)
        write_text_on_existing_image(output_path,
                                     f"Area ratio: {area_ratio * 100:.4f}, \nCentre X {center_distance_x:.2f}, "
                                     f"Centre Y{center_distance_y:.2f}, \nSharpness: {sharpness:.2f}")

        return bird_detected, bird_dominant, bird_centred, bird_sharp


def get_originals(file_prefix, dir_pth):
    og_files = []

    for filename in os.listdir(dir_pth):
        if file_prefix in filename:
            og_files.append(filename)

    return og_files


def move_originals(file_prefix, dir_pth, save_to_pth):
    og_files = get_originals(file_prefix, dir_pth)

    if len(og_files) < 1:
        log_message(f"ERROR, original files for {file_prefix} not found", dir_pth)
        return False

    for file in og_files:
        source_pth = os.path.join(dir_pth, file)

        if os.path.exists(source_pth):
            shutil.move(source_pth, os.path.join(save_to_pth, file))
            log_message(f"{source_pth} moved into {save_to_pth}", dir_pth)
        else:
            log_message(f"ERROR, file to be moved {source_pth} does not exist", dir_pth)
            return False
    return True


def run_model_on_directory(dir_pth):
    output_dir = make_new_dir(dir_pth, "Boxed")
    super_picky_dir = make_new_dir(dir_pth, "Super_Picky")
    bird_detected_dir = make_new_dir(dir_pth, "Contains_Birds")
    no_birds_dir = make_new_dir(dir_pth, "No_Birds")

    resized_dir = os.path.join(dir_pth, "Resized")
    if not os.path.exists(resized_dir):
        log_message("ERROR in run_model_on_directory, 'Resized' folder not found in give directory", dir_pth)
        return False

    print(f"Number of photos to be processed: {len(os.listdir(resized_dir))}")
    log_message("=" * 30, dir_pth)
    log_message(f"Number of photos to be processed: {len(os.listdir(resized_dir))}", dir_pth)

    for filename in os.listdir(resized_dir):
        log_message("=" * 30, dir_pth)
        log_message(f"Processing file: {filename}", dir_pth)
        file_prefix, file_ext = os.path.splitext(filename)

        filepath = os.path.join(resized_dir, filename)
        output_pth = os.path.join(output_dir, filename)

        # runs model and draws a box on the resized image
        result = detect_and_draw_birds(filepath, get_model(), output_pth)
        if result == None:
            continue
        detected, dominant, centered, sharp = result[0], result[1], result[2], result[3]

        log_message(f"detected: {detected}, dominant: {dominant}, centered: {centered},"
                    f" sharp: {sharp}", dir_pth)

        save_to_pth = dir_pth
        if detected:
            if dominant and sharp:
                save_to_pth = super_picky_dir
            else:
                save_to_pth = bird_detected_dir
        else:
            save_to_pth = no_birds_dir

        move_originals(file_prefix, dir_pth, save_to_pth)

    return True


def run_super_picky(directory):
    if not directory_contains_raw(directory):
        log_message(f"ERROR: {directory} does not contain any raw files", directory)

    resize_folder(directory)

    if run_model_on_directory(directory):
        return True
    return False
