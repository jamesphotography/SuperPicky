**⚠️ 测试版，不是正式发布 / Test build, not an official release**

## 中文

给 NVIDIA 新显卡（尤其是 **RTX 50 系列**）准备的测试安装包。功能与 [RC11](https://github.com/jamesphotography/SuperPicky/releases/tag/v4.6.4-rc11) 完全相同，只是把 GPU 运行库换成了 PyTorch 2.14.1 + CUDA 13.0。还没有在真实显卡上验证过，欢迎帮忙测试。

**三个文件都要下载，放在同一个文件夹里，然后双击运行 `.exe`。不要改文件名。**
（GitHub 单个文件不能超过 2 GB，所以安装包拆成了 3 个文件。）

- 支持：RTX 50 / 40 / 30 / 20 系列、GTX 16 系列
- 不支持 GPU 加速：GTX 10 系列及更早（会自动改用 CPU，请继续用正式版）
- 显卡驱动需要 **580 或更新**
- 会覆盖现有的 SuperPicky，设置保留；换回正式版只需重装 RC11
- 国内下载慢：在附件链接前加 `https://gh-proxy.com/`

请把显卡型号、驱动版本，以及选片后照片文件夹里的 `superpicky.log` 发给我们（在 [Issues](https://github.com/jamesphotography/SuperPicky/issues) 提交即可）。详细说明见附件 `README_cu130_test.txt`。

## English

A test installer for newer NVIDIA GPUs, especially the **RTX 50 series**. Features are identical to [RC11](https://github.com/jamesphotography/SuperPicky/releases/tag/v4.6.4-rc11); only the GPU runtime changed, to PyTorch 2.14.1 + CUDA 13.0. It hasn't been tested on real GPUs yet, so your help is welcome.

**Download all three files into the same folder, then double-click the `.exe`. Don't rename them.**
(GitHub limits each file to 2 GB, so the installer is split into 3 files.)

- Supported: RTX 50 / 40 / 30 / 20 series, GTX 16 series
- No GPU acceleration: GTX 10 series and older (falls back to CPU; please stay on the official release)
- NVIDIA driver **580 or newer** required
- Replaces your current SuperPicky and keeps your settings; reinstall RC11 to go back

Please send us your GPU model, driver version and the `superpicky.log` from the photo folder you culled (via [Issues](https://github.com/jamesphotography/SuperPicky/issues)). Full instructions are in the attached `README_cu130_test.txt`.
