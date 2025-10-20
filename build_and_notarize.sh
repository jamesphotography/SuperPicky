#!/bin/bash
# SuperPicky V3.1.3 - 打包、签名和公证脚本
# 作者: James Zhen Yu
# 日期: 2025-10-20

set -e  # 遇到错误立即退出

# ============================================
# 配置参数
# ============================================
VERSION="3.1.3"
APP_NAME="SuperPicky"
BUNDLE_ID="com.jamesphotography.superpicky"
DEVELOPER_ID="Developer ID Application: James Zhen Yu (JWR6FDB52H)"
APPLE_ID="james@jamesphotography.com.au"
TEAM_ID="JWR6FDB52H"
APP_PASSWORD="vfmy-vjcb-injx-guid"  # App-Specific Password

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================
# 辅助函数
# ============================================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================
# 步骤1：清理旧文件
# ============================================
log_info "步骤1：清理旧的build和dist目录..."
rm -rf build dist
mkdir -p dist
log_success "清理完成"

# ============================================
# 步骤2：使用PyInstaller打包
# ============================================
log_info "步骤2：使用PyInstaller打包应用..."
pyinstaller SuperPicky.spec --clean --noconfirm

if [ ! -d "dist/${APP_NAME}.app" ]; then
    log_error "打包失败！未找到 dist/${APP_NAME}.app"
    exit 1
fi
log_success "PyInstaller打包完成"

# ============================================
# 步骤3：代码签名（深度签名）
# ============================================
log_info "步骤3：对应用进行深度代码签名..."

# 签名所有嵌入的二进制文件和库
log_info "  签名嵌入的框架和库..."
find "dist/${APP_NAME}.app/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) -exec codesign --force --sign "${DEVELOPER_ID}" --timestamp --options runtime {} \; 2>/dev/null || true

# 签名主应用
log_info "  签名主应用..."
codesign --force --deep --sign "${DEVELOPER_ID}" \
    --timestamp \
    --options runtime \
    --entitlements entitlements.plist \
    "dist/${APP_NAME}.app"

# 验证签名
log_info "  验证代码签名..."
codesign --verify --deep --strict --verbose=2 "dist/${APP_NAME}.app"
log_success "代码签名完成"

# ============================================
# 步骤4：创建DMG安装包
# ============================================
log_info "步骤4：创建DMG安装包..."
DMG_NAME="${APP_NAME}_v${VERSION}.dmg"
DMG_PATH="dist/${DMG_NAME}"

# 删除旧的DMG
rm -f "${DMG_PATH}"

# 创建临时DMG文件夹
TEMP_DMG_DIR="dist/dmg_temp"
rm -rf "${TEMP_DMG_DIR}"
mkdir -p "${TEMP_DMG_DIR}"

# 复制应用到临时文件夹
cp -R "dist/${APP_NAME}.app" "${TEMP_DMG_DIR}/"

# 创建Applications快捷方式
ln -s /Applications "${TEMP_DMG_DIR}/Applications"

# 创建DMG（使用hdiutil）
log_info "  使用hdiutil创建DMG..."
hdiutil create -volname "${APP_NAME}" -srcfolder "${TEMP_DMG_DIR}" -ov -format UDZO "${DMG_PATH}"

# 清理临时文件夹
rm -rf "${TEMP_DMG_DIR}"
log_success "DMG创建完成: ${DMG_PATH}"

# ============================================
# 步骤5：签名DMG
# ============================================
log_info "步骤5：签名DMG文件..."
codesign --force --sign "${DEVELOPER_ID}" --timestamp "${DMG_PATH}"
codesign --verify --verbose=2 "${DMG_PATH}"
log_success "DMG签名完成"

# ============================================
# 步骤6：公证（Notarization）
# ============================================
log_info "步骤6：提交DMG到Apple公证服务..."

# 提交公证请求
log_info "  上传到Apple服务器..."
NOTARIZE_OUTPUT=$(xcrun notarytool submit "${DMG_PATH}" \
    --apple-id "${APPLE_ID}" \
    --password "${APP_PASSWORD}" \
    --team-id "${TEAM_ID}" \
    --wait 2>&1)

echo "${NOTARIZE_OUTPUT}"

# 检查公证结果
if echo "${NOTARIZE_OUTPUT}" | grep -q "status: Accepted"; then
    log_success "公证成功！"

    # 步骤7：装订公证票据（Staple）
    log_info "步骤7：装订公证票据到DMG..."
    xcrun stapler staple "${DMG_PATH}"

    # 验证装订
    xcrun stapler validate "${DMG_PATH}"
    log_success "公证票据装订完成"
else
    log_error "公证失败！请检查输出信息"

    # 提取RequestUUID并获取详细日志
    REQUEST_UUID=$(echo "${NOTARIZE_OUTPUT}" | grep "id:" | awk '{print $2}' | head -1)
    if [ -n "${REQUEST_UUID}" ]; then
        log_info "获取详细公证日志..."
        xcrun notarytool log "${REQUEST_UUID}" \
            --apple-id "${APPLE_ID}" \
            --password "${APP_PASSWORD}" \
            --team-id "${TEAM_ID}"
    fi
    exit 1
fi

# ============================================
# 完成
# ============================================
log_success "================================================"
log_success "🎉 全部完成！"
log_success "================================================"
log_info "应用路径: dist/${APP_NAME}.app"
log_info "DMG路径: ${DMG_PATH}"
log_info ""
log_info "现在你可以："
log_info "  1. 测试DMG安装包"
log_info "  2. 分发给用户"
log_success "================================================"
