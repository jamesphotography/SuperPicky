# -*- coding: utf-8 -*-
"""
Crop Studio 独立启动器(开发演示用)/ Standalone Crop Studio launcher (dev demo).

直接打开全屏后期工作区,用于在 GUI 中查看「智能修图」降噪的 before/after 效果,
无需启动整个主程序。

用法 / Usage:
    .venv/bin/python scripts/run_crop_studio_demo.py [图片路径]
    # 不给路径则用内置海鹦样片 / defaults to the bundled puffin sample.

打开后:点左栏「智能修图」(gem 图标)→ 微调条出现 → 点「预览效果」
→ 弹出左右并排 before/after 对话框(左=原图,右=降噪后)。
"""
from __future__ import annotations

import os
import sys

# 确保可从仓库根导入 / make repo root importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    default_sample = os.path.join(
        _ROOT, "docs", "Promotion", "wechat", "articles",
        "v4.3.0-rarity", "06.jpg")
    src = sys.argv[1] if len(sys.argv) > 1 else default_sample
    if not os.path.exists(src):
        print(f"[错误] 图片不存在 / image not found: {src}")
        return 2

    from PySide6.QtWidgets import QApplication
    from tools.i18n import get_i18n
    from ui.crop_studio import CropStudio

    app = QApplication.instance() or QApplication(sys.argv)
    photo = {
        "filename": os.path.basename(src),
        "temp_jpeg_path": src,
        "current_path": src,
        "original_path": src,
    }
    studio = CropStudio(photo, get_i18n())
    studio.showFullScreen()
    # 直接进入降噪左右对比模式,省一步点击 / jump straight into denoise compare mode.
    try:
        studio._toggle_enhance_mode()
    except Exception:  # noqa: BLE001 — 演示便利项,失败不影响主流程
        pass

    print("Crop Studio 已打开,已进入降噪对比:拖中间竖线比对,移动滑块看变化;关闭退出。")
    print(f"样片 / sample: {src}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
