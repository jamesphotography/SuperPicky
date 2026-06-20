# 上传到 Google Drive 并获取共享链接

## 前提条件

已配置 rclone Google Drive（remote 名称：`gdrive`）。如未配置，先运行：
```bash
rclone config
# 选择 n -> 名称 gdrive -> 类型 22 (Google Drive) -> 其他留空 -> auto-config y
```

## 步骤

### 1. 上传文件到 Google Drive

```bash
rclone copy "dist/慧眼选鸟v3.9_arm64.dmg" gdrive: --progress
```

### 2. 获取 Google Drive 共享链接

使用浏览器：
1. 打开 https://drive.google.com/drive/my-drive
2. 找到上传的文件
3. 右键 -> 共享
4. 设置为"知道链接的任何人"可查看
5. 复制链接

共享链接格式：`https://drive.google.com/file/d/FILE_ID/view?usp=sharing`

### 3. 更新 GitHub Release

```bash
gh release edit v3.9.0 --notes "## 下载链接
- **GitHub**: 直接从下方 Assets 下载
- **Google Drive**: [文件名](共享链接)
..."
```

## 常用命令

```bash
# 查看已配置的 remotes
rclone listremotes

# 列出 Google Drive 根目录文件
rclone ls gdrive:

# 上传到指定文件夹
rclone copy "本地文件" gdrive:目标文件夹/
```
