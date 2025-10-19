# SuperPicky - 关于窗口内容草稿

---

## 📸 SuperPicky - 慧眼选鸟

**版本**: V3.1.2
**发布日期**: 2025-10-19

---

## 👨‍💻 作者信息

**开发者**: 詹姆斯·于震 (James Yu)
**网站**: [jamesphotography.com.au](https://jamesphotography.com.au)
**YouTube**: [youtube.com/@JamesZhenYu](https://www.youtube.com/@JamesZhenYu)
**邮箱**: james@jamesphotography.com.au

### 关于作者

詹姆斯·于震是一位澳籍华裔职业摄影师，著有畅销三部曲《詹姆斯的风景摄影笔记》（总销量超10万册），他开发慧眼选鸟以提高鸟类摄影师后期筛选效率，让摄影师将更多时间专注于拍摄而非选片。

---

## 🎯 软件简介

**SuperPicky（慧眼选鸟）** 是一款专为鸟类摄影师设计的智能照片筛选工具。它能够：

✅ **自动识别鸟类** - 使用先进的AI技术检测照片中的鸟类主体
✅ **多维度评分** - 综合锐度、美学、技术质量等指标智能评级
✅ **精选推荐** - 自动标记美学与锐度双优的顶级作品
✅ **无缝集成** - 直接写入EXIF元数据，与Lightroom完美配合
✅ **批量处理** - 支持RAW格式，高效处理大量照片

---

## 🔧 使用的开源技术

慧眼选鸟基于以下优秀的开源项目构建：

### 1. Ultralytics YOLOv8
用于鸟类目标检测与分割，精确识别照片中的鸟类位置和轮廓。

**许可证**: AGPL-3.0
**项目地址**: https://github.com/ultralytics/ultralytics

### 2. PyIQA (Image Quality Assessment)
用于图像质量评估，包括NIMA美学评分和BRISQUE技术质量评分。

**许可证**: CC BY-NC-SA 4.0 (非商业使用)
**项目地址**: https://github.com/chaofengc/IQA-PyTorch
**引用**: Chen et al., "TOPIQ", IEEE TIP, 2024

### 3. ExifTool
用于EXIF元数据读写，将评分和旗标写入RAW文件。

**许可证**: Perl Artistic License / GPL
**项目地址**: https://exiftool.org

---

## 📜 版权与许可

**版权所有 © 2024-2025 詹姆斯·于震 (James Yu)**

慧眼选鸟是基于开源技术开发的**非商业用途**摄影工具。

### 使用条款

**✅ 允许**: 个人使用、教育学习、分享推荐
**❌ 禁止**: 商业用途、销售盈利、移除版权

### 免责声明

本软件按"现状"提供，不提供任何保证。作者不对使用本软件产生的任何后果负责。

**重要提示**:
- AI模型可能误判，请勿完全依赖自动评分
- 处理前请备份原始文件
- 重要项目建议先小批量测试

---

## 🙏 致谢

感谢以下项目和开发者：

- **Ultralytics团队** - 提供了卓越的YOLOv8目标检测框架
- **Chaofeng Chen和Jiadi Mo** - 开发了PyIQA图像质量评估工具箱
- **Phil Harvey** - 开发了强大的ExifTool元数据处理工具
- **所有鸟类摄影师** - 你们的反馈和建议推动了SuperPicky的不断改进

---

## 📧 联系方式

如果您在使用过程中遇到问题、有改进建议，或希望合作开发：

- **邮箱**: james@jamesphotography.com.au
- **网站**: https://jamesphotography.com.au

---

## 🔄 开源声明

慧眼选鸟遵循其依赖项目的开源许可要求：

- **AGPL-3.0** (YOLOv8): 修改并分发需开源，网络服务需提供源代码
- **CC BY-NC-SA 4.0** (PyIQA): 限制非商业使用

**商业使用**: 如需商业用途，请联系作者及相关开源项目获取商业许可

---

## 📝 更新日志

### V3.1.2 (2025-10-19)
- ✨ 新增详细的性能计时统计功能
- 🐛 修复CSV报告"评分"字段缺失问题
- 🐛 修复平均处理时间显示错误
- 🐛 修复Picked照片评分显示问题
- 📊 添加精选旗标统计显示
- 📈 添加BRISQUE影响分析

### V3.1 (2025-10-18)
- ✨ 集成PyIQA图像质量评估
- ✨ 添加NIMA美学评分
- ✨ 添加BRISQUE技术质量评分
- ✨ 实现精选旗标功能（美学+锐度双Top排名）
- ✨ 改进锐度归一化算法（Log Compression）
- ⚡ 启用MPS硬件加速（Apple Silicon）
- 🔧 添加高级配置系统

### V3.0 (2025-10-15)
- 🎉 首次正式发布
- ✨ 基于YOLOv8的鸟类检测
- ✨ 基于掩码的锐度计算
- ✨ 自动RAW转换
- ✨ EXIF元数据写入
- ✨ 星级评分系统

---

## 📌 技术支持

**常见问题**: 请参阅用户手册（待编写）

**Bug报告**: 请通过邮箱详细描述问题，并附上：
- 系统版本（macOS版本）
- SuperPicky版本
- 错误日志（位于`_tmp/process_log.txt`）
- 问题截图（如适用）

**功能建议**: 欢迎通过邮箱提出您的想法！

---

**SuperPicky - 让AI帮你挑选最美的瞬间** 🦅📸

---

*本文档最后更新: 2025-10-19*
