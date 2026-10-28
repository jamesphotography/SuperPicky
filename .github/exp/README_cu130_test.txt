SuperPicky 4.6.4 RC11 —— 新显卡（CUDA 13）测试版
==================================================

（English version below）

这是什么
--------
这是专门给 NVIDIA 新显卡（尤其是 RTX 50 系列）准备的测试安装包，不是正式发布版。

程序功能和 RC11 完全相同，只是换了新的 GPU 运行库（PyTorch 2.14.1 + CUDA 13.0）。
现在的正式 CUDA 版用的是 CUDA 11.8，RTX 50 系列用不了 GPU 加速，只能退回 CPU，
速度慢很多。这个测试版就是为了解决这个问题。

这个版本还没有在真实显卡上验证过，所以请您帮忙测试。

文件（两种下载方式，内容完全相同，任选一种）
--------------------------------------------
A. 单文件版（网盘下载）
   SuperPicky_Setup_FullCUDA13_Win64_v4.6.4RC11_test.exe（约 2.2 GB）
   下载后直接双击运行。

B. 分卷版（GitHub 下载，国内可用镜像）
   https://github.com/jamesphotography/SuperPicky/releases/tag/test-cu130-rc11
   GitHub 单个文件不能超过 2 GB，所以安装包拆成了 3 个文件，三个都要下载：
     SuperPicky_Setup_FullCUDA_Win64_v4.6.4RC11_ea9341b.exe    （3.4 MB）
     SuperPicky_Setup_FullCUDA_Win64_v4.6.4RC11_ea9341b-1.bin  （约 2.0 GB）
     SuperPicky_Setup_FullCUDA_Win64_v4.6.4RC11_ea9341b-2.bin  （约 120 MB）
   * 三个文件必须放在同一个文件夹里，然后双击运行 .exe。
   * 不要改文件名，否则安装程序找不到 .bin 文件。
   * 国内下载慢的话，可以在链接前面加上 https://gh-proxy.com/ 再下载。

谁适合测
--------
* RTX 50 系列（5060 / 5070 / 5080 / 5090 等）：最需要您的反馈。
* RTX 20 / 30 / 40 系列、GTX 16 系列：也可以测，用来确认没有变慢、结果没有变化。
* GTX 10 系列（1060 / 1070 / 1080 等）及更早的显卡：这个版本不支持它们的 GPU 加速，
  程序会自动改用 CPU，不会报错。请继续使用正式版。

安装前
------
1. 需要 Windows 10 / 11 64 位。
2. 请先把 NVIDIA 显卡驱动更新到 580 或更新的版本。驱动太旧的话 GPU 用不上，
   程序会改用 CPU。查看驱动版本：在桌面右键打开「NVIDIA 控制面板」→ 左下角「系统信息」。
3. 安装时 Windows 可能提示「Windows 已保护你的电脑」，点「更多信息」→「仍要运行」。
4. 这个安装包会覆盖电脑上现有的 SuperPicky，您的设置会保留。想换回正式版的话，
   重新安装 RC11 正式版即可：
   https://github.com/jamesphotography/SuperPicky/releases/tag/v4.6.4-rc11

建议的测试方法
--------------
1. 准备一个 100～200 张照片的文件夹，先复制一份备用。
2. 用这个测试版选片，留意总共花了多长时间。
3. 如果方便，再用 RC11 正式版处理那份复制的文件夹，对比：
   * 速度：哪个快，大概快多少；
   * 结果：星级、识别出的鸟种是否基本一致。

请发回给我们
------------
1. 显卡型号和驱动版本。
2. 选片完成后，照片文件夹里会有 superpicky.log，请把它发给我们。
   日志里会写明程序用的是 GPU 还是 CPU。
3. 速度和结果的对比（如果做了）。
4. 遇到的任何报错、闪退或异常，最好附截图。

可以在 GitHub 提 issue：https://github.com/jamesphotography/SuperPicky/issues
也可以通过您拿到这个安装包的渠道直接告诉我们。

谢谢您帮忙测试！


==================================================
SuperPicky 4.6.4 RC11 — New GPU (CUDA 13) test build
==================================================

What this is
------------
A test installer for newer NVIDIA GPUs, especially the RTX 50 series. It is
not an official release.

Features are identical to RC11; only the GPU runtime changed, to PyTorch 2.14.1
+ CUDA 13.0. The current official CUDA build uses CUDA 11.8, which cannot use
RTX 50 GPUs, so those cards fall back to the much slower CPU. This build is meant
to fix that.

It has not been tested on real GPUs yet, which is why we'd love your help.

Files (two ways to download; the contents are identical, pick one)
------------------------------------------------------------------
A. Single file (cloud drive)
   SuperPicky_Setup_FullCUDA13_Win64_v4.6.4RC11_test.exe (about 2.2 GB)
   Download it and double-click to run.

B. Split installer (GitHub)
   https://github.com/jamesphotography/SuperPicky/releases/tag/test-cu130-rc11
   GitHub limits each file to 2 GB, so the installer is split into 3 files.
   Download all three:
     SuperPicky_Setup_FullCUDA_Win64_v4.6.4RC11_ea9341b.exe    (3.4 MB)
     SuperPicky_Setup_FullCUDA_Win64_v4.6.4RC11_ea9341b-1.bin  (about 2.0 GB)
     SuperPicky_Setup_FullCUDA_Win64_v4.6.4RC11_ea9341b-2.bin  (about 120 MB)
   * Put all three files in the same folder, then double-click the .exe.
   * Don't rename them, or the installer won't find the .bin files.

Who should test
---------------
* RTX 50 series (5060 / 5070 / 5080 / 5090 etc.): your feedback matters most.
* RTX 20 / 30 / 40 series and GTX 16 series: also welcome, to confirm nothing
  got slower and results did not change.
* GTX 10 series (1060 / 1070 / 1080 etc.) and older: GPU acceleration is not
  supported in this build. The app switches to CPU automatically without errors.
  Please stay on the official release.

Before installing
-----------------
1. Windows 10 / 11, 64-bit.
2. Update your NVIDIA driver to version 580 or newer. With an older driver the
   GPU won't be used and the app falls back to CPU. To check: right-click the
   desktop → NVIDIA Control Panel → System Information (bottom left).
3. If Windows shows "Windows protected your PC", click More info → Run anyway.
4. This installer replaces your current SuperPicky; your settings are kept. To go
   back, reinstall the official RC11:
   https://github.com/jamesphotography/SuperPicky/releases/tag/v4.6.4-rc11

Suggested test
--------------
1. Pick a folder of 100–200 photos and make a copy of it.
2. Cull it with this test build and note how long it takes.
3. If you can, cull the copy with the official RC11 and compare:
   * Speed: which is faster, and roughly by how much.
   * Results: are the star ratings and identified species about the same?

Please send us
--------------
1. Your GPU model and driver version.
2. superpicky.log from the photo folder you culled. It shows whether the app used
   the GPU or the CPU.
3. Your speed / result comparison, if you did one.
4. Any errors, crashes or odd behaviour, ideally with screenshots.

Open an issue at https://github.com/jamesphotography/SuperPicky/issues, or reply
through wherever you got this installer.

Thanks for helping test!
