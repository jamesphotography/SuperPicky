#!/usr/bin/env python3
"""
SuperPicky BirdID API 服务器
提供 HTTP REST API 供外部程序调用鸟类识别功能
完全兼容 Lightroom 插件 (端口 5156)
"""

__version__ = "1.0.0"

import os
import sys
import base64
import tempfile
from io import BytesIO

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.i18n import t
from config import config, get_birdid_settings_path

from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image

# 注意：birdid.bird_identifier（torch/YOLO 模型栈）改为懒加载。
# 顶层 import 会让服务器监听被模型栈导入阻塞十几秒以上，导致应用启动后
# Lightroom 插件在这段窗口期内连不上 5156 端口（表现为"没有反应"）。
# 各接口在函数体内按需导入；模型预热由 server_manager 的后台线程完成。
# NOTE: birdid.bird_identifier (the torch/YOLO model stack) is lazily imported.
# A top-level import blocks the server from listening for 10+ seconds, so the
# Lightroom plugin cannot reach port 5156 right after app launch. Each endpoint
# imports on demand; model warm-up happens in server_manager's background thread.

# 创建 Flask 应用
app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 全局配置
DEFAULT_PORT = config.server.PORT
DEFAULT_HOST = config.server.HOST


def get_gui_settings():
    """读取 GUI 界面设置的国家/地区过滤"""
    import re
    settings_path = str(get_birdid_settings_path())
    
    settings = {
        'use_geo_filter': True,
        'country_code': None,
        'region_code': None
    }
    
    if os.path.exists(settings_path):
        try:
            import json
            with open(settings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            settings['use_geo_filter'] = data.get('use_geo_filter', True)
            
            # 解析国家代码（从 country_list 映射）
            # 设置文件存储的是显示名称，需要转换为代码
            country_display = data.get('selected_country', '')
            if country_display and country_display not in ('自动检测 (GPS)', '全球模式'):
                # 常见国家映射
                country_map = {
                    '澳大利亚': 'AU', '美国': 'US', '英国': 'GB', '中国': 'CN',
                    '香港': 'HK', '台湾': 'TW', '日本': 'JP'
                }
                settings['country_code'] = country_map.get(country_display)
                
                # 如果是其他国家（从"更多国家"选的），格式可能是 "国家名 (Country Name)"
                if not settings['country_code'] and '(' in country_display:
                    # 尝试加载 regions 数据匹配
                    pass
            
            # 解析区域代码
            region_display = data.get('selected_region', '')
            if region_display and region_display != '整个国家':
                # 格式: "South Australia (AU-SA)"
                match = re.search(r'\(([A-Z]{2}-[A-Z0-9]+)\)', region_display)
                if match:
                    settings['region_code'] = match.group(1)
        except Exception as e:
            print(t("server.read_gui_settings_failed", error=e))
    
    return settings


def get_gui_language():
    """
    获取 GUI 界面的语言设置
    
    V4.0.5: 修复 - 复用 AdvancedConfig 的平台感知路径
    之前硬编码 ~/Documents/SuperPicky_Data/ 路径不存在，导致永远返回 None
    
    Returns:
        'zh_CN' 或 'en_US' 或 None (默认自动)
    """
    try:
        from advanced_config import get_advanced_config
        config = get_advanced_config()
        return config.language
    except Exception:
        pass
    
    return None


def update_gui_settings_from_gps(region_code: str, region_name: str = None):
    """
    将 GPS 检测到的区域同步到 GUI 设置文件
    这样主界面的国家/地区选择会自动更新
    
    Args:
        region_code: eBird 区域代码（如 "AU-SA" 或 "AU"）
        region_name: 区域名称（可选，用于显示）
    """
    import json
    settings_path = str(get_birdid_settings_path())
    
    try:
        # 读取现有设置
        settings = {}
        if os.path.exists(settings_path):
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        
        # 解析区域代码
        if '-' in region_code:
            # 格式: "AU-SA" -> 国家 AU, 区域 SA
            country_code = region_code.split('-')[0]
        else:
            # 只有国家代码
            country_code = region_code
        
        # 国家代码到显示名称的映射
        country_display_map = {
            'AU': '澳大利亚', 'US': '美国', 'GB': '英国', 'CN': '中国',
            'HK': '香港', 'TW': '台湾', 'JP': '日本', 'NZ': 'New Zealand'
        }
        
        # 更新国家选择
        country_display = country_display_map.get(country_code, country_code)
        settings['selected_country'] = country_display
        
        # 如果有具体区域，更新区域选择
        if '-' in region_code and region_name:
            settings['selected_region'] = f"{region_name} ({region_code})"
        else:
            settings['selected_region'] = '整个国家'
        
        # 确保目录存在
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        
        # 保存设置
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        
        print(t("server.sync_gps_success", country=country_display, region=region_name if region_name else ""))
        
    except Exception as e:
        print(t("server.sync_gps_failed", error=e))


def ensure_models_loaded():
    """
    确保模型已加载（首次调用会触发重型模型栈导入与权重加载）。

    Ensure models are loaded (first call triggers the heavy model-stack
    import and weight loading).
    """
    from birdid.bird_identifier import (
        get_classifier,
        get_database_manager,
        get_yolo_detector,
        YOLO_AVAILABLE,
    )

    print(t("server.loading_models_cli"))
    get_classifier()
    print(t("server.classifier_loaded"))

    db = get_database_manager()
    if db:
        print(t("server.db_loaded"))

    if YOLO_AVAILABLE:
        detector = get_yolo_detector()
        if detector:
            print(t("server.yolo_loaded_simple"))


@app.route('/health', methods=['GET'])
def health_check():
    """
    健康检查接口。

    不触发重型模型栈导入：服务器一监听就能返回 ok，让 Lightroom 插件
    在模型后台加载期间也能确认服务存在。models_loaded 反映模型栈是否就绪。

    Health check endpoint. It never triggers the heavy model-stack import:
    the server can answer ok as soon as it listens, so the Lightroom plugin
    can confirm the service exists while models are still loading in the
    background. models_loaded reflects whether the model stack is ready.
    """
    bird_identifier = sys.modules.get('birdid.bird_identifier')
    return jsonify({
        'status': 'ok',
        'service': 'SuperPicky BirdID API',
        'version': __version__,
        'models_loaded': bird_identifier is not None,
        'yolo_available': (
            getattr(bird_identifier, 'YOLO_AVAILABLE', None)
            if bird_identifier is not None else None
        ),
    })


@app.route('/recognize', methods=['POST'])
def recognize_bird():
    """
    识别鸟类

    请求体 (JSON):
    {
        "image_path": "/path/to/image.jpg",  // 图片路径（二选一）
        "image_base64": "base64_encoded_image",  // Base64编码的图片（二选一）
        "use_yolo": true,  // 是否使用YOLO裁剪（可选，默认true）
        "use_gps": true,  // 是否使用GPS过滤（可选，默认true）
        "top_k": 3  // 返回前K个结果（可选，默认3）
    }

    返回 (JSON):
    {
        "success": true,
        "results": [
            {
                "rank": 1,
                "cn_name": "白头鹎",
                "en_name": "Light-vented Bulbul",
                "scientific_name": "Pycnonotus sinensis",
                "confidence": 95.5,
                "ebird_match": true
            },
            ...
        ],
        "gps_info": {
            "latitude": 39.123,
            "longitude": 116.456,
            "info": "GPS: 39.123, 116.456"
        }
    }
    """
    try:
        data = request.get_json()

        if not data:
            print(f"[API] ❌ {t('server.invalid_request')}")
            return jsonify({'success': False, 'error': t("server.invalid_request")}), 400

        # 获取图片
        image = None
        image_path = data.get('image_path')
        image_base64 = data.get('image_base64')
        temp_file = None
        
        # 日志：显示请求信息
        if image_path:
            print(t("server.log_request_file", file=os.path.basename(image_path)))
        elif image_base64:
            print(t("server.log_request_base64"))

        if image_path:
            # 从文件路径加载
            if not os.path.exists(image_path):
                print(t("server.file_not_found", path=image_path))
                return jsonify({'success': False, 'error': t("server.file_not_found", path=image_path)}), 404
        elif image_base64:
            # 从 Base64 解码
            try:
                image_data = base64.b64decode(image_base64)
                image = Image.open(BytesIO(image_data))

                # 创建临时文件用于 EXIF 读取
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                image.save(temp_file.name, 'JPEG')
                image_path = temp_file.name
            except Exception as e:
                return jsonify({'success': False, 'error': t("server.base64_decode_failed", error=e)}), 400
        else:
            return jsonify({'success': False, 'error': t("server.missing_params")}), 400

        # 获取参数
        use_yolo = data.get('use_yolo', True)
        use_gps = data.get('use_gps', True)
        top_k = data.get('top_k', 3)
        
        # 读取 GUI 设置的国家/地区过滤
        gui_settings = get_gui_settings()
        country_code = data.get('country_code', gui_settings['country_code'])
        region_code = data.get('region_code', gui_settings['region_code'])
        use_geo_filter = data.get('use_geo_filter', gui_settings['use_geo_filter'])
        
        # 日志：显示识别参数
        print(t("server.log_params"))
        print(t("server.log_yolo", value=t("server.yes") if use_yolo else t("server.no")))
        print(t("server.log_gps", value=t("server.yes") if use_gps else t("server.no")))
        print(t("server.log_ebird", value=t("server.yes") if use_geo_filter else t("server.no")))
        print(t("server.log_location", country=country_code or 'N/A', region=region_code or 'N/A'))

        # 执行识别（懒加载：首次请求会在此处完成模型栈导入）
        # Run recognition (lazy: the first request finishes the model-stack import here)
        from advanced_config import get_advanced_config
        from birdid.bird_identifier import identify_bird
        result = identify_bird(
            image_path,
            use_yolo=use_yolo,
            use_gps=use_gps,
            top_k=top_k,
            country_code=country_code,
            region_code=region_code,
            use_geo_filter=use_geo_filter,
            name_format=get_advanced_config().name_format,
        )
        
        # 日志：显示识别结果
        if result.get('success'):
            results = result.get('results', [])
            if results:
                top_result = results[0]
                print(t("server.log_success", name=top_result.get('cn_name', '?'), conf=top_result.get('confidence', 0)))
            else:
                print(t("server.log_no_result"))
        else:
            print(t("server.log_fail", error=result.get('error', 'Unknown')))

        # 清理临时文件
        if temp_file:
            try:
                os.unlink(temp_file.name)
            except:
                pass

        if not result['success']:
            return jsonify({
                'success': False,
                'error': result.get('error', t("server.identify_failed_default"))
            }), 500

        # 格式化结果（兼容 Lightroom 插件格式）
        formatted_results = []
        
        # 获取语言设置，决定 display_name 使用中文还是英文
        gui_language = get_gui_language()
        use_chinese = gui_language is None or gui_language == 'zh_CN'
        
        for i, r in enumerate(result.get('results', []), 1):
            cn_name = r.get('cn_name', '')
            en_name = r.get('en_name', '')
            # 根据语言设置选择 display_name
            if use_chinese:
                display_name = cn_name if cn_name else en_name
            else:
                display_name = en_name if en_name else cn_name
            
            formatted_results.append({
                'rank': i,
                'cn_name': cn_name,
                'en_name': en_name,
                'display_name': display_name,  # 根据语言设置自动选择
                'scientific_name': r.get('scientific_name', ''),
                'confidence': float(r.get('confidence', 0)),
                'ebird_match': r.get('ebird_match', False),
                'description': r.get('description', '')
            })
        
        # 智能候选筛选：根据置信度差距决定返回多少个候选
        if len(formatted_results) >= 2:
            top_confidence = formatted_results[0]['confidence']
            
            # 计算与第一名的相对差距（百分比）
            # 如果第1名 = 50%, 第2名 = 40%, 相对差距 = (50-40)/50 = 20%
            smart_results = [formatted_results[0]]  # 总是包含第1名
            
            for r in formatted_results[1:]:
                if top_confidence > 0:
                    relative_gap = (top_confidence - r['confidence']) / top_confidence * 100
                    # 如果相对差距 <= 50%，认为是"接近的候选"
                    if relative_gap <= 50:
                        smart_results.append(r)
                    else:
                        break  # 后面的差距只会更大，停止添加
            
            # 日志：显示筛选结果
            if len(smart_results) == 1:
                print(t("server.log_smart_filter_1", conf=top_confidence))
            else:
                print(t("server.log_smart_filter_n", count=len(smart_results)))
            
            formatted_results = smart_results
        
        # 如果没有结果，返回详细的错误信息
        if not formatted_results:
            geo_info = result.get('geo_info')
            if geo_info and geo_info.get('enabled'):
                region = geo_info.get('country_code') or 'Unknown'
                species_count = geo_info.get('species_count') or 0
                error_msg = t("server.ebird_filter_error", region=region, species_count=species_count)
                print(f"[API] ⚠️  {error_msg}")
                return jsonify({
                    'success': False,
                    'error': error_msg,
                    'geo_info': geo_info
                })
            else:
                return jsonify({
                    'success': False,
                    'error': t("server.identify_no_bird")
                })

        response = {
            'success': True,
            'results': formatted_results,
            'yolo_info': result.get('yolo_info'),
            'gps_info': result.get('gps_info'),
            'geo_info': result.get('geo_info')
        }

        # 过滤降级警告：命中 L3/L4/L5 说明候选层被放宽了
        # Degradation warning: hitting L3/L4/L5 means the tier was widened
        from birdid.geo_filter import (
            TIER_COUNTRY, TIER_NEIGHBORHOOD, TIER_NONE, describe_tier,
        )
        geo_info = result.get('geo_info') or {}
        if geo_info.get('tier') in (TIER_NEIGHBORHOOD, TIER_COUNTRY, TIER_NONE):
            response['warning'] = describe_tier(geo_info)

        # 如果照片有 GPS 信息，同步检测到的区域到主界面设置
        gps_info = result.get('gps_info')
        if gps_info and gps_info.get('latitude') and gps_info.get('longitude'):
            # 使用 GPS 坐标检测区域
            try:
                from birdid.ebird_country_filter import eBirdCountryFilter
                ebird_filter = eBirdCountryFilter("", cache_dir="ebird_cache", offline_dir="offline_ebird_data")
                detected_region, region_name_raw = ebird_filter.get_region_code_from_gps(
                    gps_info['latitude'], gps_info['longitude']
                )
                if detected_region:
                    # 州/省代码到完整名称的映射
                    state_name_map = {
                        # 澳大利亚
                        'AU-WA': 'Western Australia',
                        'AU-SA': 'South Australia',
                        'AU-NSW': 'New South Wales',
                        'AU-VIC': 'Victoria',
                        'AU-QLD': 'Queensland',
                        'AU-TAS': 'Tasmania',
                        'AU-NT': 'Northern Territory',
                        'AU-ACT': 'Australian Capital Territory',
                        # 可以继续添加其他国家的州/省
                    }
                    region_name = state_name_map.get(detected_region, region_name_raw)
                    update_gui_settings_from_gps(detected_region, region_name)
            except Exception as e:
                print(t("server.gps_detect_failed", error=e))

        return jsonify(response)

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
        }), 500


@app.route('/exif/write-title', methods=['POST'])
def write_exif_title():
    """
    写入鸟种名称到 EXIF Title

    请求体:
    {
        "image_path": "/path/to/image.jpg",
        "bird_name": "白头鹎"
    }
    """
    try:
        data = request.get_json()
        image_path = data.get('image_path')
        bird_name = data.get('bird_name')

        if not image_path or not bird_name:
            return jsonify({'success': False, 'error': t("server.missing_required_params")}), 400

        if not os.path.exists(image_path):
            return jsonify({'success': False, 'error': t("server.file_not_found", path=image_path)}), 404

        from tools.exiftool_manager import get_exiftool_manager
        exiftool_mgr = get_exiftool_manager()
        success = exiftool_mgr.set_metadata(image_path, {'Title': bird_name})

        return jsonify({
            'success': success,
            'message': t("server.write_success", value=bird_name) if success else t("server.write_failed")
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/exif/write-caption', methods=['POST'])
def write_exif_caption():
    """
    写入鸟种描述到 EXIF Caption

    请求体:
    {
        "image_path": "/path/to/image.jpg",
        "caption": "鸟种描述文本"
    }
    """
    try:
        data = request.get_json()
        image_path = data.get('image_path')
        caption = data.get('caption')

        if not image_path or not caption:
            return jsonify({'success': False, 'error': t("server.missing_required_params")}), 400

        if not os.path.exists(image_path):
            return jsonify({'success': False, 'error': t("server.file_not_found", path=image_path)}), 404

        from tools.exiftool_manager import get_exiftool_manager
        exiftool_mgr = get_exiftool_manager()
        success = exiftool_mgr.set_metadata(image_path, {'Caption-Abstract': caption})

        return jsonify({
            'success': success,
            'message': t("server.write_caption_success") if success else t("server.write_failed")
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description=t("server.server_desc"))
    parser.add_argument('--host', default=DEFAULT_HOST, help=t("server.arg_host", default=DEFAULT_HOST))
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=t("server.arg_port", default=DEFAULT_PORT))
    parser.add_argument('--debug', action='store_true', help=t("server.arg_debug"))
    parser.add_argument('--no-preload', action='store_true', help=t("server.arg_no_preload"))

    args = parser.parse_args()

    print("=" * 60)
    print(f"  {t('server.server_desc')} v{__version__}")
    print("=" * 60)
    print(t("server.server_listen", host=args.host, port=args.port))
    print(t("server.server_health", host=args.host, port=args.port))
    print(t("server.server_recognize", host=args.host, port=args.port))
    print("=" * 60)
    print(t("server.server_stop_hint"))
    print("=" * 60)

    # 预加载模型
    if not args.no_preload:
        print(t("server.preload_start"))
        ensure_models_loaded()
        print(t("server.preload_done"))

    # 启动服务器
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main()
