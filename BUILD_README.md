# SuperPicky V3.0 - 打包和公证指南

## 📋 准备工作清单

### ✅ 已完成的清理工作
- [x] 删除所有 `__pycache__` 目录
- [x] 删除所有 `.pyc` 和 `.pyo` 文件
- [x] 删除所有 `.DS_Store` 文件
- [x] 删除所有测试文件 (`test_*.py`)
- [x] 删除临时文档 (`MERGE_SUMMARY.md`)
- [x] 清理旧的 `build` 和 `dist` 目录

### ✅ 已创建的文件
- [x] `build_and_notarize.sh` - 自动化打包、签名和公证脚本
- [x] `entitlements.plist` - 代码签名权限配置文件

---

## 🚀 使用步骤

### 1. 运行打包脚本

只需要执行一条命令：

```bash
./build_and_notarize.sh
```

这个脚本会自动完成以下所有步骤：
1. ✅ 清理旧的 build 和 dist 目录
2. ✅ 使用 PyInstaller 打包应用
3. ✅ 对应用进行深度代码签名
4. ✅ 创建 DMG 安装包
5. ✅ 签名 DMG 文件
6. ✅ 提交到 Apple 公证服务
7. ✅ 装订公证票据到 DMG

### 2. 等待公证完成

公证过程通常需要 **5-15分钟**，脚本会自动等待并显示进度。

### 3. 完成！

成功后，你会在 `dist` 目录下找到：
- `SuperPicky.app` - 应用程序
- `SuperPicky_v3.0.dmg` - 已签名和公证的 DMG 安装包

---

## 🔧 配置信息

脚本中已配置的信息：
- **应用名称**: SuperPicky
- **版本**: 3.0
- **Bundle ID**: com.jamesphotography.superpicky
- **开发者证书**: Developer ID Application: James Zhen Yu (JWR6FDB52H)
- **Apple ID**: james@jamesphotography.com.au
- **Team ID**: JWR6FDB52H
- **App密码**: vfmy-vjcb-injx-guid

---

## ⚠️ 可能遇到的问题

### 问题1：公证失败
**原因**: 可能是 App 密码过期或无效

**解决方案**:
1. 访问 https://appleid.apple.com/account/manage
2. 生成新的 App-Specific Password
3. 在脚本中更新 `APP_PASSWORD` 变量

### 问题2：代码签名失败
**原因**: 证书过期或无效

**解决方案**:
```bash
# 检查可用证书
security find-identity -v -p codesigning

# 确保有 "Developer ID Application" 证书
```

### 问题3：PyInstaller 打包失败
**原因**: 缺少依赖或 spec 文件配置错误

**解决方案**:
```bash
# 检查依赖
pip list

# 重新生成 spec 文件
pyi-makespec --onefile --windowed main.py
```

---

## 📝 手动步骤（如果自动脚本失败）

### 1. 打包
```bash
pyinstaller SuperPicky.spec --clean --noconfirm
```

### 2. 签名
```bash
codesign --force --deep --sign "Developer ID Application: James Zhen Yu (JWR6FDB52H)" \
    --timestamp --options runtime \
    --entitlements entitlements.plist \
    dist/SuperPicky.app
```

### 3. 验证签名
```bash
codesign --verify --deep --strict --verbose=2 dist/SuperPicky.app
```

### 4. 创建 DMG
```bash
hdiutil create -volname "SuperPicky" -srcfolder dist/SuperPicky.app -ov -format UDZO dist/SuperPicky_v3.0.dmg
```

### 5. 签名 DMG
```bash
codesign --force --sign "Developer ID Application: James Zhen Yu (JWR6FDB52H)" \
    --timestamp dist/SuperPicky_v3.0.dmg
```

### 6. 公证
```bash
xcrun notarytool submit dist/SuperPicky_v3.0.dmg \
    --apple-id "james@jamesphotography.com.au" \
    --password "vfmy-vjcb-injx-guid" \
    --team-id "JWR6FDB52H" \
    --wait
```

### 7. 装订公证票据
```bash
xcrun stapler staple dist/SuperPicky_v3.0.dmg
xcrun stapler validate dist/SuperPicky_v3.0.dmg
```

---

## ✅ 验证最终产品

打包完成后，验证以下内容：

1. **代码签名验证**:
   ```bash
   codesign --verify --deep --strict --verbose=2 dist/SuperPicky.app
   spctl --assess --verbose=4 dist/SuperPicky.app
   ```

2. **DMG 验证**:
   ```bash
   codesign --verify --verbose=2 dist/SuperPicky_v3.0.dmg
   ```

3. **公证验证**:
   ```bash
   xcrun stapler validate dist/SuperPicky_v3.0.dmg
   spctl --assess --type open --context context:primary-signature --verbose=4 dist/SuperPicky_v3.0.dmg
   ```

4. **测试安装**:
   - 双击 DMG 文件
   - 将 SuperPicky.app 拖到 Applications 文件夹
   - 从 Applications 启动应用
   - 确保没有 Gatekeeper 警告

---

## 📦 分发

一切验证通过后，你可以：
1. 上传 `SuperPicky_v3.0.dmg` 到你的网站
2. 通过邮件发送给用户
3. 上传到 GitHub Releases
4. 提交到 Mac App Store（需要额外配置）

---

## 🎉 完成！

现在你的 SuperPicky V3.0 应用已经：
- ✅ 完整打包
- ✅ 代码签名
- ✅ Apple 公证
- ✅ 可以安全分发

用户下载后可以直接安装使用，不会收到任何安全警告！
