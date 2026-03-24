#!/bin/bash
# SuperPicky - PKG + DMG 完整打包脚本
# 包含: PyInstaller打包 → PKG组件 → Distribution PKG → DMG → 签名公证
# 特色: 自动安装 Lightroom 插件，文件名含架构和 commit hash
# 作者: James Zhen Yu
# 平台: Apple Silicon (arm64) / Intel (x86_64)

set -e  # 遇到错误立即退出

# ============================================
# 配置参数
# ============================================
VERSION=$(python3 -c "from constants import APP_VERSION; print(APP_VERSION)")
APP_NAME="SuperPicky"
APP_NAME_CN="慧眼选鸟"
BUNDLE_ID="com.jamesphotography.superpicky"
DEVELOPER_ID="Developer ID Application: James Zhen Yu (JWR6FDB52H)"
INSTALLER_ID="Developer ID Installer: James Zhen Yu (JWR6FDB52H)"
APPLE_ID="james@jamesphotography.com.au"
TEAM_ID="JWR6FDB52H"
APP_PASSWORD=$(security find-generic-password -a "${APPLE_ID}" -s "SuperPicky-Notarize" -w)

# 检测 CPU 架构
ARCH=$(uname -m)
if [ "${ARCH}" = "arm64" ]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="intel"
fi

# 从 Python 代码读取 Commit Hash（保证跨平台一致）
# 优先读 build_info_local.py（本地 override），其次 build_info.py
COMMIT_HASH=$(python3 -c "
try:
    from core.build_info_local import COMMIT_HASH
except ImportError:
    from core.build_info import COMMIT_HASH
print(COMMIT_HASH or 'unknown')
")

# 文件名格式: SuperPicky_v4.1.0_arm64_f20f9b5.dmg
PKG_NAME="${APP_NAME}_v${VERSION}_${ARCH_TAG}_${COMMIT_HASH}_Installer.pkg"
DMG_NAME="${APP_NAME}_v${VERSION}_${ARCH_TAG}_${COMMIT_HASH}.dmg"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

# ============================================
# 辅助函数
# ============================================
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "\n${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${PURPLE}$1${NC}"; echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ============================================
# 步骤1: 清理和准备
# ============================================
log_step "步骤 1/8: 清理旧构建"

rm -rf build dist pkg_root pkg_scripts
rm -f *.pkg *.dmg distribution.xml welcome.html conclusion.html
mkdir -p dist

log_success "清理完成"

# ============================================
# 步骤2: PyInstaller 打包
# ============================================
log_step "步骤 2/8: PyInstaller 打包应用"

log_info "激活 .venv 虚拟环境..."
source .venv/bin/activate

# 注入 Git Commit Hash 到 build_info.py（COMMIT_HASH 已在顶部配置区获取）
BUILD_INFO_FILE="core/build_info.py"
BUILD_INFO_BACKUP="${BUILD_INFO_FILE}.backup"
cp "${BUILD_INFO_FILE}" "${BUILD_INFO_BACKUP}"
sed -i.tmp "s/COMMIT_HASH = None.*/COMMIT_HASH = \"${COMMIT_HASH}\"/" "${BUILD_INFO_FILE}"
rm -f "${BUILD_INFO_FILE}.tmp"
log_info "  Commit Hash: ${COMMIT_HASH}"

log_info "开始 PyInstaller 打包..."
pyinstaller SuperPicky.spec --clean --noconfirm

# 恢复原始 build_info.py
mv "${BUILD_INFO_BACKUP}" "${BUILD_INFO_FILE}"

if [ ! -d "dist/${APP_NAME}.app" ]; then
    log_error "打包失败！未找到 dist/${APP_NAME}.app"
    exit 1
fi

# 创建 .app bundle
log_info "创建 macOS 应用包..."
APP_PATH="dist/${APP_NAME}.app"

# 创建 BUNDLE 结构（如果需要）
mkdir -p "${APP_PATH}/Contents/MacOS"
mkdir -p "${APP_PATH}/Contents/Resources"

# 移动可执行文件和资源
if [ -d "dist/${APP_NAME}" ] && [ ! -f "${APP_PATH}/Contents/MacOS/${APP_NAME}" ]; then
    mv dist/${APP_NAME}/* "${APP_PATH}/Contents/MacOS/"
fi

log_success "PyInstaller 打包完成"

# 移动资源到 Contents/Resources
log_info "整理资源文件..."
if [ -d "${APP_PATH}/Contents/MacOS/SuperBirdIDPlugin.lrplugin" ]; then
    log_info "  移动 Lightroom 插件到 Resources..."
    mv "${APP_PATH}/Contents/MacOS/SuperBirdIDPlugin.lrplugin" "${APP_PATH}/Contents/Resources/"
fi

if [ -d "${APP_PATH}/Contents/MacOS/en.lproj" ]; then
    log_info "  移动 en.lproj 到 Resources..."
    mv "${APP_PATH}/Contents/MacOS/en.lproj" "${APP_PATH}/Contents/Resources/"
fi

if [ -d "${APP_PATH}/Contents/MacOS/zh-Hans.lproj" ]; then
    log_info "  移动 zh-Hans.lproj 到 Resources..."
    mv "${APP_PATH}/Contents/MacOS/zh-Hans.lproj" "${APP_PATH}/Contents/Resources/"
fi

# ============================================
# 步骤3: 代码签名
# ============================================
log_step "步骤 3/8: 代码签名"

log_info "签名嵌入的库和框架..."
find "${APP_PATH}/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) -print0 | \
    xargs -0 -P 8 -I {} codesign --force --sign "${DEVELOPER_ID}" --timestamp --options runtime {} 2>/dev/null || true

log_info "签名主应用..."
codesign --force --deep --sign "${DEVELOPER_ID}" \
    --timestamp \
    --options runtime \
    --entitlements entitlements.plist \
    "${APP_PATH}"

log_info "验证签名..."
codesign --verify --deep --strict --verbose=2 "${APP_PATH}"

log_success "代码签名完成"

# ============================================
# 步骤4: 创建 PKG 组件包
# ============================================
log_step "步骤 4/8: 创建 PKG 组件包"

mkdir -p pkg_root/Applications
mkdir -p pkg_scripts

# 复制应用（重命名为中文名）
log_info "复制应用到安装目录..."
# 复制应用（使用英文名 SuperPicky.app 以支持国际化）
log_info "复制应用到安装目录..."
ditto "${APP_PATH}" "pkg_root/Applications/${APP_NAME}.app"

# 创建 postinstall 脚本
log_info "创建 postinstall 脚本..."
cat > pkg_scripts/postinstall << 'POSTINSTALL_EOF'
#!/bin/bash
# SuperPicky V__VERSION__ - 安装后配置脚本
# Post-install configuration script

echo "Configuring SuperPicky V__VERSION__..."

APP_PATH="/Applications/SuperPicky.app"

# 获取真实用户（而非 root）
# Get real user (not root)
REAL_USER=$(stat -f '%Su' /dev/console)
REAL_HOME=$(eval echo ~$REAL_USER)

echo "User: $REAL_USER"
echo "Home: $REAL_HOME"

# ============================================
# Language Detection / 语言检测
# ============================================
IS_CHINESE=0
# Check global preferences for Simplified Chinese
if defaults read -g AppleLanguages 2>/dev/null | grep -q "zh-Hans"; then
    IS_CHINESE=1
fi

# Define Strings based on language
if [ "$IS_CHINESE" -eq 1 ]; then
    TXT_TITLE="慧眼选鸟 - Lightroom 插件安装"
    TXT_PROMPT="请选择要安装插件的 Lightroom 版本："
    TXT_NOTE="(可按住 Command 键多选)"
    TXT_OPT_USER="Lightroom 用户模块 (推荐)"
    TXT_OPT_CLASSIC="Lightroom Classic 应用内 (需重启LR)"
    TXT_OPT_APP_IN="应用内"
    TXT_MSG_NO_LR="⚠ 未检测到 Lightroom 安装"
    TXT_MSG_MANUAL="插件已保存在应用包内，您可以稍后手动安装"
    TXT_MSG_CANCEL="已跳过插件安装"
    TXT_MSG_MANUAL_HINT="您可以稍后从应用包内手动复制插件"
    TXT_MSG_SUCCESS="✓ Lightroom 插件安装完成"
    TXT_OPT_SKIP="⊘ 跳过，不安装插件"
    TXT_BTN_CANCEL="不要安装"
else
    TXT_TITLE="SuperPicky - Lightroom Plugin Installer"
    TXT_PROMPT="Please select Lightroom versions to install the plugin:"
    TXT_NOTE="(Hold Command key to select multiple)"
    TXT_OPT_USER="Lightroom User Modules (Recommended)"
    TXT_OPT_CLASSIC="Lightroom Classic Internal (Requires Restart)"
    TXT_OPT_APP_IN="Inside App"
    TXT_MSG_NO_LR="⚠ No Lightroom installation detected"
    TXT_MSG_MANUAL="Plugin is inside the app bundle, you can install manually later"
    TXT_MSG_CANCEL="Plugin installation skipped"
    TXT_MSG_MANUAL_HINT="You can manually copy the plugin from the app bundle later"
    TXT_MSG_SUCCESS="✓ Lightroom Plugin installation completed"
    TXT_OPT_SKIP="⊘ Skip, don't install plugin"
    TXT_BTN_CANCEL="Don't Install"
fi

# 1. 设置应用权限 / Set permissions
chmod -R 755 "$APP_PATH"
echo "✓ Application permissions set"

# 2. 设置 ExifTool 可执行权限 / Set ExifTool permissions
EXIFTOOL_PATH="$APP_PATH/Contents/Frameworks/exiftools_mac/exiftool"
if [ -f "$EXIFTOOL_PATH" ]; then
    chmod +x "$EXIFTOOL_PATH"
    echo "✓ ExifTool permissions set"
fi

# 3. 设置 ExifTool lib 目录权限
LIB_DIR="$APP_PATH/Contents/Frameworks/exiftools_mac/lib"
if [ -d "$LIB_DIR" ]; then
    chmod -R 755 "$LIB_DIR"
fi

# 4. 安装 Lightroom 插件 / Install Lightroom Plugin
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Detecting Lightroom versions..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PLUGIN_SOURCE="$APP_PATH/Contents/Resources/SuperBirdIDPlugin.lrplugin"

# 检测可用的 Lightroom 版本
declare -a LR_OPTIONS
declare -a LR_PATHS
declare -a LR_NAMES

# 用户 Modules 目录（推荐）
USER_MODULES="$REAL_HOME/Library/Application Support/Adobe/Lightroom/Modules"
if [ -d "$REAL_HOME/Library/Application Support/Adobe/Lightroom" ]; then
    LR_OPTIONS+=("$TXT_OPT_USER")
    LR_PATHS+=("$USER_MODULES")
    LR_NAMES+=("Lightroom User Modules")
    echo "  ✓ Found: Lightroom User Directory"
fi

# Lightroom Classic 应用内 PlugIns（需要 admin）
LR_CLASSIC_PLUGINS="/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/PlugIns"
if [ -d "$LR_CLASSIC_PLUGINS" ]; then
    LR_OPTIONS+=("$TXT_OPT_CLASSIC")
    LR_PATHS+=("$LR_CLASSIC_PLUGINS")
    LR_NAMES+=("Lightroom Classic App")
    echo "  ✓ Found: Lightroom Classic App"
fi

# 检测其他可能的 Lightroom 安装
for lr_app in /Applications/Adobe\ Lightroom*/Adobe\ Lightroom*.app/Contents/PlugIns; do
    if [ -d "$lr_app" ] && [[ "$lr_app" != "$LR_CLASSIC_PLUGINS" ]]; then
        app_name=$(basename "$(dirname "$(dirname "$lr_app")")" | sed 's/Adobe //')
        LR_OPTIONS+=("$app_name $TXT_OPT_APP_IN")
        LR_PATHS+=("$lr_app")
        LR_NAMES+=("$app_name")
        echo "  ✓ Found: $app_name"
    fi
done

# 如果没有检测到任何 Lightroom
if [ ${#LR_OPTIONS[@]} -eq 0 ]; then
    echo "$TXT_MSG_NO_LR"
    echo "$TXT_MSG_MANUAL"
    echo "Path: $PLUGIN_SOURCE"
else
    echo ""
    echo "Found ${#LR_OPTIONS[@]} install locations"

    # 添加跳过选项（排在最末尾）/ Add skip option at the end
    LR_OPTIONS+=("$TXT_OPT_SKIP")

    # 构建 osascript 选项列表
    OPTIONS_STR=""
    for opt in "${LR_OPTIONS[@]}"; do
        if [ -z "$OPTIONS_STR" ]; then
            OPTIONS_STR="\"$opt\""
        else
            OPTIONS_STR="$OPTIONS_STR, \"$opt\""
        fi
    done
    
    # 使用 osascript 弹出多选对话框
    # Using osascript to show dialog
    echo "Showing selection dialog..."
    
    # Passing variables to osascript is tricky with heredoc variables inside heredoc
    # We construct the Applescript string with our bash variables
    
    SELECTED=$(osascript -e "
        set theChoices to {$OPTIONS_STR}
        set selectedItems to choose from list theChoices with title \"$TXT_TITLE\" with prompt \"$TXT_PROMPT

$TXT_NOTE\" cancel button name \"$TXT_BTN_CANCEL\" default items {item 1 of theChoices} with multiple selections allowed
        if selectedItems is false then
            return \"CANCELLED\"
        else
            set AppleScript's text item delimiters to \"|||\"
            return selectedItems as text
        end if
    " 2>/dev/null)
    
    if [ "$SELECTED" = "CANCELLED" ] || [ -z "$SELECTED" ]; then
        echo "$TXT_MSG_CANCEL"
        echo "$TXT_MSG_MANUAL_HINT"
    else
        echo "User selection: $SELECTED"
        echo ""
        
        INSTALLED_COUNT=0
        
        # 解析用户选择并安装
        IFS='|||' read -ra SELECTED_ITEMS <<< "$SELECTED"
        for selection in "${SELECTED_ITEMS[@]}"; do
            # 用户选择了跳过 / User chose to skip
            if [ "$selection" = "$TXT_OPT_SKIP" ]; then
                echo "$TXT_MSG_CANCEL"
                continue
            fi
            # 查找对应的路径
            for i in "${!LR_OPTIONS[@]}"; do
                if [ "${LR_OPTIONS[$i]}" = "$selection" ]; then
                    TARGET_PATH="${LR_PATHS[$i]}"
                    TARGET_NAME="${LR_NAMES[$i]}"
                    
                    echo "Installing to: $TARGET_NAME..."
                    
                    # 创建目录（如果不存在）
                    mkdir -p "$TARGET_PATH"
                    
                    # 删除旧版本
                    if [ -d "$TARGET_PATH/SuperBirdIDPlugin.lrplugin" ]; then
                        rm -rf "$TARGET_PATH/SuperBirdIDPlugin.lrplugin"
                    fi
                    
                    # 复制插件
                    if cp -R "$PLUGIN_SOURCE" "$TARGET_PATH/"; then
                        # 设置正确的所有者（用户目录需要）
                        if [[ "$TARGET_PATH" == "$REAL_HOME"* ]]; then
                            chown -R "$REAL_USER" "$TARGET_PATH/SuperBirdIDPlugin.lrplugin"
                        fi
                        echo "  ✓ Installed to: $TARGET_NAME"
                        INSTALLED_COUNT=$((INSTALLED_COUNT + 1))
                    else
                        echo "  ✗ Failed to install: $TARGET_NAME"
                    fi
                    break
                fi
            done
        done
        
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "$TXT_MSG_SUCCESS ($INSTALLED_COUNT locations)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    fi
fi

# 6. 安装 Lightroom 导出预设 / Install Export Presets
echo ""
echo "Installing Lightroom Export Presets..."
PRESET_SOURCE="$APP_PATH/Contents/Resources/SuperBirdIDPlugin.lrplugin/SuperPicky.lrtemplate"
PRESET_DIR="$REAL_HOME/Library/Application Support/Adobe/Lightroom/Export Presets/User Presets"

if [ -f "$PRESET_SOURCE" ]; then
    mkdir -p "$PRESET_DIR"
    cp "$PRESET_SOURCE" "$PRESET_DIR/"
    chown "$REAL_USER" "$PRESET_DIR/SuperPicky.lrtemplate"
    echo "✓ Export preset installed to: $PRESET_DIR"
else
    echo "⚠ Export preset file not found, skipping"
fi

# 7. 清除隔离标记 / Clear quarantine
xattr -cr "$APP_PATH" 2>/dev/null || true
echo "✓ Quarantine cleared"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ SuperPicky V__VERSION__ Installation Completed!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📍 Location: /Applications/SuperPicky.app"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$IS_CHINESE" -eq 1 ]; then
    echo "⚠️  Lightroom 插件首次使用说明："
    echo ""
    echo "   1. 打开 Lightroom → 文件 → 增效工具管理器"
    echo "   2. 在左侧列表找到 SuperPicky BirdID Plugin"
    echo "   3. 点击右侧「启用」按钮"
else
    echo "⚠️  Lightroom Plugin First-time Setup:"
    echo ""
    echo "   1. Open Lightroom → File → Plug-in Manager"
    echo "   2. Find 'SuperPicky BirdID Plugin' in the list"
    echo "   3. Click the 'Enable' button on the right"
fi
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exit 0
POSTINSTALL_EOF

chmod +x pkg_scripts/postinstall
sed -i '' "s/__VERSION__/${VERSION}/g" pkg_scripts/postinstall

# 创建组件 plist 禁用 relocation（防止应用被安装到错误位置）
log_info "创建组件 plist (禁用 relocation)..."
cat > pkg_components.plist << 'COMPONENT_PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
    <dict>
        <key>BundleHasStrictIdentifier</key>
        <true/>
        <key>BundleIsRelocatable</key>
        <false/>
        <key>BundleIsVersionChecked</key>
        <false/>
        <key>BundleOverwriteAction</key>
        <string>upgrade</string>
        <key>RootRelativeBundlePath</key>
        <string>Applications/SuperPicky.app</string>
    </dict>
</array>
</plist>
COMPONENT_PLIST_EOF

# 构建组件包
log_info "构建 PKG 组件包..."
pkgbuild --root pkg_root \
    --scripts pkg_scripts \
    --component-plist pkg_components.plist \
    --identifier "${BUNDLE_ID}" \
    --version "${VERSION}" \
    --install-location "/" \
    "${APP_NAME}-component.pkg"

log_success "PKG 组件包创建完成"

# ============================================
# 步骤5: 创建 Distribution PKG
# ============================================
log_step "步骤 5/8: 创建 Distribution PKG"

# 创建欢迎页面
cat > welcome.html << 'WELCOME_EOF'
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        /* Support dark and light mode with transparent background */
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; 
            padding: 20px; 
            line-height: 1.6; 
            background: transparent; 
            color: #1a1a1a; 
        }
        @media (prefers-color-scheme: dark) {
            body { background: transparent; color: #e0e0e0; }
            h1 { color: #f1f5f9; }
            .version { color: #94a3b8; }
            h2, h3 { color: #94a3b8; }
            .highlight { color: #60a5fa; }
            li { color: #d1d5db; }
            p { color: #d1d5db; }
            strong { color: #f1f5f9; }
        }
        h1 { color: #2c3e50; margin-bottom: 5px; }
        .version { color: #7f8c8d; font-size: 0.9em; margin-bottom: 20px; }
        h2, h3 { color: #34495e; }
        .highlight { color: #3498db; font-weight: bold; }
        ul { padding-left: 20px; }
        li { margin: 8px 0; color: #374151; }
        .new-badge { background: #e74c3c; color: white; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; }
    </style>
</head>
<body>
    <h1>Welcome to SuperPicky</h1>
    <p class="version">Version __VERSION__</p>

    <p>This installer will install <strong>SuperPicky</strong> and its <strong>Lightroom Plugin</strong> on your computer.</p>

    <h2>What's New in V__VERSION__ <span class="new-badge">NEW</span></h2>
    <ul>
        <li><span class="highlight">1. 架构性能</span> - 全新 ONNX 推理引擎与轻量化关键点模型 (283MB → 95MB)，全面加速</li>
        <li><span class="highlight">2. 批处理强化</span> - 支持文件夹递归批处理、EXIF 星级双向同步、最近目录历史记录</li>
        <li><span class="highlight">3. 识鸟工具集</span> - 新增独立的 IOC 鸟名中英词典，以及识别结果右键快捷复制功能</li>
        <li><span class="highlight">4. 修复跨平台Bug</span> - 修复中文路径崩溃、死锁问题，回收 ExifTool 进程，安全拦截截屏权限</li>
    </ul>

    <h3>System Requirements</h3>
    <ul>
        <li>macOS 12.0 (Monterey) or later</li>
        <li>Apple Silicon (M1/M2/M3/M4) or Intel processor</li>
        <li>Approximately 2GB of available disk space</li>
    </ul>

    <p>Click "Continue" to proceed with the installation.</p>
</body>
</html>
WELCOME_EOF
sed -i '' "s/__VERSION__/${VERSION}/g" welcome.html

cat > conclusion.html << 'CONCLUSION_EOF'
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        /* Support dark and light mode with transparent background */
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; 
            padding: 20px; 
            line-height: 1.6; 
            background: transparent; 
            color: #1a1a1a; 
        }
        @media (prefers-color-scheme: dark) {
            body { background: transparent; color: #e0e0e0; }
            h1 { color: #4ade80; }
            h2 { color: #94a3b8; }
            .success { background: transparent; border-color: #22c55e; color: #4ade80; }
            .success strong { color: #4ade80; }
            .info-box { background: transparent; border-color: #3b82f6; color: #bfdbfe; }
            .info-box strong { color: #60a5fa; }
            .info-box p { color: #d1d5db; }
            .warning { background: transparent; border-color: #f59e0b; color: #fbbf24; }
            .warning strong { color: #fbbf24; }
            .warning p { color: #d1d5db; }
            a { color: #60a5fa; }
            li { color: #d1d5db; }
            p { color: #d1d5db; }
        }
        h1 { color: #27ae60; }
        h2 { color: #34495e; }
        .success { background: transparent; border: 2px solid #27ae60; padding: 15px; border-radius: 5px; margin: 20px 0; color: #27ae60; }
        .info-box { background: transparent; border-left: 4px solid #3498db; padding: 15px; margin: 15px 0; color: #1a1a1a; }
        .warning { background: transparent; border-left: 4px solid #f59e0b; padding: 15px; margin: 15px 0; color: #856404; }
        a { color: #3498db; text-decoration: none; }
        li { color: #374151; }
        @media (prefers-color-scheme: dark) {
            li { color: #d1d5db; }
        }
    </style>
</head>
<body>
    <h1>✓ Installation Complete</h1>

    <div class="success">
        <strong>SuperPicky V__VERSION__</strong> has been successfully installed!
    </div>

    <h2>Installed Components</h2>
    <div class="info-box">
        <p><strong>📍 Main Application:</strong> /Applications/SuperPicky.app</p>
        <p><strong>📍 Lightroom Plugin:</strong> ~/Library/Application Support/Adobe/Lightroom/Modules/</p>
    </div>

    <h2>Getting Started</h2>
    <div class="info-box">
        <p><strong>Main Application:</strong></p>
        <ul>
            <li>Find and launch "SuperPicky" from Launchpad</li>
            <li>Or navigate to the Applications folder</li>
        </ul>
        <p><strong>Lightroom Plugin:</strong></p>
        <ul>
            <li>Open Lightroom and select a photo</li>
            <li>Menu: Library → Plug-in Extras → SuperPicky - Identify Current Photo</li>
        </ul>
    </div>

    <div class="warning">
        <p><strong>⚠️ First-Time Usage Notes:</strong></p>
        <ul>
            <li>First launch may take 10-30 seconds to load AI models</li>
            <li>The main app must be running before using the Lightroom plugin</li>
            <li><strong>Enable Lightroom Plugin:</strong> File → Plug-in Manager → Find "SuperPicky" → Click "Enable"</li>
        </ul>
    </div>

    <p style="margin-top: 30px; font-size: 0.9em;">
        Thank you for using SuperPicky! For support, visit <a href="https://github.com/jamesphotography/SuperPicky">GitHub</a>
    </p>
</body>
</html>
CONCLUSION_EOF
sed -i '' "s/__VERSION__/${VERSION}/g" conclusion.html

# 创建 Distribution XML
cat > distribution.xml << DISTRIBUTION_EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="1">
    <title>慧眼选鸟 SuperPicky</title>
    <organization>com.jamesphotography</organization>
    <domains enable_localSystem="true"/>
    <options customize="never" require-scripts="false" hostArchitectures="arm64,x86_64"/>

    <welcome file="welcome.html" mime-type="text/html"/>
    <license file="LICENSE" mime-type="text/plain"/>
    <conclusion file="conclusion.html" mime-type="text/html"/>

    <choices-outline>
        <line choice="default">
            <line choice="${BUNDLE_ID}"/>
        </line>
    </choices-outline>

    <choice id="default"/>
    <choice id="${BUNDLE_ID}" visible="false">
        <pkg-ref id="${BUNDLE_ID}"/>
    </choice>

    <pkg-ref id="${BUNDLE_ID}" version="${VERSION}" onConclusion="none">
        ${APP_NAME}-component.pkg
    </pkg-ref>
</installer-gui-script>
DISTRIBUTION_EOF

# 构建最终 PKG
log_info "构建 Distribution PKG..."
productbuild --distribution distribution.xml \
    --resources . \
    --package-path . \
    "${PKG_NAME}"

log_success "Distribution PKG 创建完成"

# ============================================
# 步骤6: 签名 PKG
# ============================================
log_step "步骤 6/8: 签名 PKG"

log_info "签名 PKG 安装包..."
productsign --sign "${INSTALLER_ID}" "${PKG_NAME}" "${PKG_NAME/.pkg/-signed.pkg}"
mv "${PKG_NAME/.pkg/-signed.pkg}" "${PKG_NAME}"

log_info "验证 PKG 签名..."
pkgutil --check-signature "${PKG_NAME}"

log_success "PKG 签名完成"

# ============================================
# 步骤7: 创建 DMG
# ============================================
log_step "步骤 7/8: 创建 DMG"

TEMP_DMG_DIR="dist/dmg_temp"
rm -rf "${TEMP_DMG_DIR}"
mkdir -p "${TEMP_DMG_DIR}"

# 复制 PKG 到 DMG
cp "${PKG_NAME}" "${TEMP_DMG_DIR}/"

# 复制 Lightroom 插件副本（供手动安装）
log_info "复制 Lightroom 插件副本..."
cp -R "SuperBirdIDPlugin.lrplugin" "${TEMP_DMG_DIR}/"

# 生成 PDF 安装指南
log_info "生成 PDF 安装指南..."
if [ -f "docs/安装指南_v${VERSION}.html" ]; then
    # 使用 cupsfilter 或 wkhtmltopdf 生成 PDF（如果可用）
    # 备选：直接复制 HTML，用户可用浏览器打印为 PDF
    cp "docs/安装指南_v${VERSION}.html" "${TEMP_DMG_DIR}/Installation Guide 安装指南.html"
    log_info "  已复制 HTML 安装指南（可在浏览器中打印为 PDF）"
fi

# 创建网站使用教程快捷方式
log_info "创建网站快捷方式..."
cat > "${TEMP_DMG_DIR}/Online Tutorial 在线教程.webloc" << 'WEBLOC_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>URL</key>
    <string>https://github.com/jamesphotography/SuperPicky</string>
</dict>
</plist>
WEBLOC_EOF

# 创建插件安装说明
cat > "${TEMP_DMG_DIR}/Lightroom Plugin Manual Installation 插件手动安装.txt" << 'PLUGIN_README_EOF'
================================================================================
慧眼选鸟 Lightroom 插件 - 手动安装说明
SuperPicky Lightroom Plugin - Manual Installation Guide
================================================================================

如果自动安装没有成功，或者您使用的是 Lightroom Classic 以外的版本，
请按照以下步骤手动安装插件：

If automatic installation failed, or you're using a version other than
Lightroom Classic, please follow these steps to install manually:

--------------------------------------------------------------------------------
【Lightroom Classic】
--------------------------------------------------------------------------------
  1. 复制 SuperBirdIDPlugin.lrplugin 文件夹到:
     Copy the SuperBirdIDPlugin.lrplugin folder to:
     ~/Library/Application Support/Adobe/Lightroom/Modules/

--------------------------------------------------------------------------------
【Lightroom Classic (旧版 / Older versions)】
--------------------------------------------------------------------------------
  1. 打开 Lightroom → 文件 → 增效工具管理器
     Open Lightroom → File → Plug-in Manager
  2. 点击「添加」
     Click "Add"
  3. 选择 SuperBirdIDPlugin.lrplugin 文件夹
     Select the SuperBirdIDPlugin.lrplugin folder
  4. 重启 Lightroom
     Restart Lightroom

--------------------------------------------------------------------------------
【使用方法 / How to Use】
--------------------------------------------------------------------------------
  1. 先启动「慧眼选鸟」主程序，开启识鸟 API
     Launch SuperPicky first and enable the Bird ID API
  2. 在 Lightroom 中选中一张照片
     Select a photo in Lightroom
  3. 菜单: 图库 → 增效工具 → 慧眼选鸟 - 识别当前照片
     Menu: Library → Plug-in Extras → SuperPicky - Identify Current Photo

--------------------------------------------------------------------------------
【注意事项 / Important Notes】
--------------------------------------------------------------------------------
  - 使用插件前需要先启动主程序
    The main app must be running before using the plugin
  - 确保主程序的「识鸟 API」开关已开启
    Make sure the "Bird ID API" toggle is enabled in the main app

================================================================================
版本 Version: __VERSION__
© 2026 James Zhen Yu
================================================================================
PLUGIN_README_EOF
sed -i '' "s/__VERSION__/${VERSION}/g" "${TEMP_DMG_DIR}/Lightroom Plugin Manual Installation 插件手动安装.txt"

# 创建总说明文件
cat > "${TEMP_DMG_DIR}/README 安装说明.txt" << README_EOF
================================================================================
慧眼选鸟 SuperPicky V${VERSION} 安装说明
SuperPicky V${VERSION} Installation Guide
================================================================================

--------------------------------------------------------------------------------
【推荐安装方式 / Recommended Installation】
--------------------------------------------------------------------------------
双击「${PKG_NAME}」按向导安装
Double-click "${PKG_NAME}" and follow the installer wizard

  - 会自动安装主应用到 /Applications
    Automatically installs the app to /Applications
  - 会自动安装 Lightroom 插件
    Automatically installs the Lightroom plugin

--------------------------------------------------------------------------------
【手动安装 Lightroom 插件 / Manual Lightroom Plugin Installation】
--------------------------------------------------------------------------------
如果自动安装失败，请参考「Lightroom Plugin Manual Installation 插件手动安装.txt」
If automatic installation fails, see "Lightroom Plugin Manual Installation 插件手动安装.txt"

或直接将 SuperBirdIDPlugin.lrplugin 文件夹复制到:
Or copy the SuperBirdIDPlugin.lrplugin folder to:
  ~/Library/Application Support/Adobe/Lightroom/Modules/

--------------------------------------------------------------------------------
【首次使用 / Getting Started】
--------------------------------------------------------------------------------
  - 从启动台打开「慧眼选鸟」
    Launch "SuperPicky" from Launchpad
  - Lightroom 插件: 图库 → 增效工具 → 慧眼选鸟
    Lightroom Plugin: Library → Plug-in Extras → SuperPicky

--------------------------------------------------------------------------------
【问题反馈 / Feedback & Issues】
--------------------------------------------------------------------------------
https://github.com/jamesphotography/SuperPicky

================================================================================
© 2026 James Zhen Yu
================================================================================
README_EOF

# 创建 DMG
log_info "创建 DMG 镜像..."
hdiutil create -volname "${APP_NAME} ${VERSION}" \
    -srcfolder "${TEMP_DMG_DIR}" \
    -ov -format UDZO \
    "dist/${DMG_NAME}"

rm -rf "${TEMP_DMG_DIR}"

log_success "DMG 创建完成"

# ============================================
# 步骤8: 公证
# ============================================
log_step "步骤 8/8: 提交公证"

DMG_PATH="dist/${DMG_NAME}"

log_info "签名 DMG..."
codesign --force --sign "${DEVELOPER_ID}" --timestamp "${DMG_PATH}"

log_info "提交到 Apple 公证服务..."
NOTARIZE_OUTPUT=$(xcrun notarytool submit "${DMG_PATH}" \
    --apple-id "${APPLE_ID}" \
    --password "${APP_PASSWORD}" \
    --team-id "${TEAM_ID}" \
    --wait \
    --output-format json 2>&1)

echo "${NOTARIZE_OUTPUT}"

if echo "${NOTARIZE_OUTPUT}" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"Accepted"'; then
    log_success "公证成功！"
    
    log_info "装订公证票据..."
    xcrun stapler staple "${DMG_PATH}"
    xcrun stapler validate "${DMG_PATH}"
    
    log_success "✅ V${VERSION} 打包发布全部完成！"
    log_info "最终文件: ${DMG_PATH}"
else
    log_error "❌ 公证失败，请检查日志"
    exit 1
fi
