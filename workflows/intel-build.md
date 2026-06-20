---
description: Intel Mac 打包签名公证流程
---

# Intel Mac (iMac) 打包签名公证流程

## 前提条件检查

```bash
# 1. 拉取最新代码
cd /path/to/SuperPicky2026
git pull origin master

# 2. 检查 Python 环境
python3 --version  # 确保是 x86_64 版本

# 3. 检查证书是否有效
security find-identity -v -p codesigning | grep "Developer ID"

# 4. 检查 PyInstaller
pip3 show pyinstaller
```

## 打包流程

### 步骤 1: 清理旧文件
```bash
rm -rf build/ dist/
```

### 步骤 2: 运行打包脚本
```bash
# 开发测试 (不公证，快速打包)
python3 build_release_mac.py --build-type full --arch x86_64

# 正式发布 (签名+公证)
python3 build_release_mac.py --build-type full --arch x86_64 --notarize --sign-p12 /path/to/certificate.p12 --sign-p12-password-env MACOS_CERTIFICATE_PWD --apple-id "your@email.com" --team-id "YOUR_TEAM_ID"
```

## 常见问题处理

### 问题 1: PyInstaller 版本不兼容
```bash
pip3 install --upgrade pyinstaller
```

### 问题 2: 模块找不到
```bash
# 重新安装依赖
pip3 install -r requirements.txt
```

### 问题 3: 签名失败 - 证书问题
```bash
# 检查证书
security find-identity -v -p codesigning

# 如果证书过期，需要在 Xcode 中更新
```

### 问题 4: 公证失败 - 需要 Keychain 密码
```bash
# 确保 notarytool 密码在 Keychain 中
xcrun notarytool store-credentials "AC_PASSWORD" \
  --apple-id "your@email.com" \
  --team-id "YOUR_TEAM_ID" \
  --password "app-specific-password"
```

### 问题 5: libc++.1.dylib 警告
这些警告通常可以忽略，不影响应用运行。

## 验证打包结果

```bash
# 检查签名
codesign -vvv --deep --strict dist/SuperPicky.app

# 检查 Gatekeeper
spctl -a -vv dist/SuperPicky.app

# 测试运行
dist/SuperPicky.app/Contents/MacOS/SuperPicky
```

## 上传到 GitHub Release

```bash
# 上传 Intel 版本（使用不同文件名）
gh release upload v3.9.3 dist/SuperPicky_v3.9.3_intel.dmg --clobber
```
