## What's New

**Results Browser Enhancements**
- Recursive subdirectory batch processing in both CLI and GUI
- Directory switching and recent directory history in the browser
- Star rating changes now sync back to EXIF Rating on the original file
- Right-click to copy bird name from result cards
- File size now shown in the detail panel
- Fixed burst sequence looping in fullscreen viewer

**Model Size Reduction**
- Keypoint detection model reduced from ~283MB to ~95MB, significantly faster to load

**IOC Bird Name Search**
- New standalone Chinese/English bird name lookup powered by the IOC database

**Bug Fixes**
- Fixed Chinese path compatibility issues (cv2, rawpy)
- Fixed non-UTF-8 console encoding on Windows
- Fixed QComboBox interaction on macOS
- Fixed birdname.db resource path missing after packaging
- Improved ExifTool process cleanup on exit
- Fixed resource path and launch directory issues in macOS app bundle

**Windows CUDA Patch Support**
- CPU base package + CUDA GPU patch are now distributed separately

---

## New Sponsor

Thanks to **Juntao Zhang** for sponsoring one quarter of AI coding tools for this project.

---

## Downloads

| Platform | GitHub | Baidu | Quark |
|---|---|---|---|
| macOS ARM64 (Apple Silicon) | [Download](https://github.com/jamesphotography/SuperPicky/releases/download/v4.2.1-beta/SuperPicky_v4.2.1-beta_arm64_ec200340.dmg) | [Baidu (6s4k)](https://pan.baidu.com/s/1hJaHKGB7Rni1AoLddsTV1g?pwd=6s4k) | [Quark](https://pan.quark.cn/s/3d3652d53972) |
| Windows CPU Only | [Download](https://github.com/jamesphotography/SuperPicky/releases/download/v4.2.1-beta/SuperPicky_Win64_4.2.1-beta_ec20034_cpu.zip) | [Baidu (39f1)](https://pan.baidu.com/s/1iEjbypJKQh34mrvAvHA82Q?pwd=39f1) | [Quark](https://pan.quark.cn/s/08065f16a8d1) |
| Windows CUDA GPU Patch | [Download](https://github.com/jamesphotography/SuperPicky/releases/download/v4.2.1-beta/SuperPicky_Win64_4.2.1-beta_ec20034_cuda_patch.zip) | [Baidu (n2uj)](https://pan.baidu.com/s/1-JL4mn8cMKRJCSwXaQhB6Q?pwd=n2uj) | [Quark](https://pan.quark.cn/s/2caa4af00b1b) |

> This is a Beta release. For production use, please use v4.1.0 LTS.

---

## 更新内容

**结果浏览器增强**
- CLI 和 GUI 均支持子目录递归批处理
- 结果浏览器新增目录切换与最近目录历史
- 星级修改可同步写回原始文件的 EXIF Rating
- 结果卡片支持右键复制鸟名
- 详情面板新增文件大小显示
- 修复全屏模式下连拍序列循环切换问题

**模型体积优化**
- 关键点检测模型从约 283MB 精简至约 95MB，加载速度显著提升

**IOC 鸟名检索**
- 新增独立的中英文鸟名查询功能，基于 IOC 数据库

**问题修复**
- 修复中文路径兼容问题（cv2、rawpy）
- 修复 Windows 非 UTF-8 控制台编码问题
- 修复 macOS 下 QComboBox 交互异常
- 修复打包后 birdname.db 资源路径丢失
- 加强 ExifTool 进程退出时的安全回收
- 修复 macOS 打包资源路径与启动目录问题

**Windows CUDA 补丁包支持**
- 新增 CPU 主包 + CUDA GPU 补丁包的独立分发方式

---

## 新增赞助商

感谢 **张钧涛（Juntao Zhang）** 赞助支持本项目一个季度的 AI 编程工具使用费。

---

> 本版本为测试版，正式环境请使用 v4.1.0 LTS。
