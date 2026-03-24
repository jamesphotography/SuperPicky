#!/bin/bash
# SuperPicky - 打包、签名和公证脚本
# 作者: James Zhen Yu
# 版本: 1.0
#
# 用法:
#   ./build_release.sh --test      # 仅打包和签名（跳过公证）
#   ./build_release.sh --release   # 完整流程：打包、签名、公证
#   ./build_release.sh --help      # 显示帮助

set -e  # 遇到错误立即退出

# ============================================
# 配置参数
# ============================================
APP_NAME="SuperPicky"
BUNDLE_ID="com.jamesphotography.superpicky"
DEVELOPER_ID="Developer ID Application: James Zhen Yu (JWR6FDB52H)"
APPLE_ID="james@jamesphotography.com.au"
TEAM_ID="JWR6FDB52H"
KEYCHAIN_ITEM="SuperPicky-Notarize"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
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

log_step() {
    echo -e "\n${CYAN}========================================${NC}"
    echo -e "${CYAN}步骤$1: $2${NC}"
    echo -e "${CYAN}========================================${NC}"
}

show_help() {
    echo "SuperPicky 构建脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  --test      仅打包和签名，跳过公证（用于快速测试）"
    echo "  --release   完整流程：打包、签名、公证、装订"
    echo "  --help      显示此帮助信息"
    echo ""
    echo "首次使用前，需要配置 Keychain:"
    echo "  security add-generic-password -a \"${APPLE_ID}\" \\"
    echo "    -s \"${KEYCHAIN_ITEM}\" -w \"你的App-Specific-Password\""
    echo ""
}

# ============================================
# 参数解析
# ============================================
MODE=""
if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

case "$1" in
    --test)
        MODE="test"
        ;;
    --release)
        MODE="release"
        ;;
    --help|-h)
        show_help
        exit 0
        ;;
    *)
        log_error "未知选项: $1"
        show_help
        exit 1
        ;;
esac

# ============================================
# 步骤0: 环境检查
# ============================================
log_step "0" "环境检查"

# 检查开发者证书
log_info "检查开发者证书..."
if ! security find-identity -v -p codesigning | grep -q "${DEVELOPER_ID}"; then
    log_error "未找到开发者证书: ${DEVELOPER_ID}"
    log_info "请确保已在 Keychain 中安装有效的开发者证书"
    exit 1
fi
log_success "开发者证书已就绪"

# 检查 Keychain 密码（仅 release 模式）
if [ "$MODE" = "release" ]; then
    log_info "检查 Keychain 中的 App-Specific Password..."
    if ! security find-generic-password -a "${APPLE_ID}" -s "${KEYCHAIN_ITEM}" -w &>/dev/null; then
        log_error "未在 Keychain 中找到 App-Specific Password"
        log_info "请运行以下命令添加密码:"
        echo ""
        echo "  security add-generic-password -a \"${APPLE_ID}\" \\"
        echo "    -s \"${KEYCHAIN_ITEM}\" -w \"你的密码\""
        echo ""
        exit 1
    fi
    log_success "Keychain 密码已配置"
fi

# 检查 PyInstaller
log_info "检查 PyInstaller..."
if ! command -v pyinstaller &>/dev/null; then
    # 尝试从虚拟环境
    if [ -f ".venv/bin/pyinstaller" ]; then
        PYINSTALLER=".venv/bin/pyinstaller"
    else
        log_error "未找到 PyInstaller，请先安装: pip install pyinstaller"
        exit 1
    fi
else
    PYINSTALLER="pyinstaller"
fi
log_success "PyInstaller 已就绪"

# 检查 entitlements.plist
if [ ! -f "entitlements.plist" ]; then
    log_error "未找到 entitlements.plist 文件"
    exit 1
fi

# ============================================
# 步骤1: 提取版本号
# ============================================
log_step "1" "提取版本号"

VERSION=$(grep 'APP_VERSION' constants.py | grep -oE '"[0-9]+\.[0-9]+\.[0-9]+"' | tr -d '"' | head -1)
if [ -z "$VERSION" ]; then
    log_error "无法从 constants.py 提取版本号"
    exit 1
fi
log_success "检测到版本: v${VERSION}"

# ============================================
# 步骤1.5: 检测 CPU 架构
# ============================================
log_info "检测 CPU 架构..."
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    ARCH_SUFFIX="arm64"
    log_success "检测到 Apple Silicon (arm64)"
elif [ "$ARCH" = "x86_64" ]; then
    ARCH_SUFFIX="intel"
    log_success "检测到 Intel (x86_64)"
else
    ARCH_SUFFIX="$ARCH"
    log_warning "未知架构: $ARCH"
fi

# 设置输出文件名（包含架构信息）
if [ "$MODE" = "test" ]; then
    DMG_NAME="${APP_NAME}_v${VERSION}_${ARCH_SUFFIX}_test.dmg"
else
    DMG_NAME="${APP_NAME}_v${VERSION}_${ARCH_SUFFIX}.dmg"
fi
DMG_PATH="dist/${DMG_NAME}"

# ============================================
# 步骤2: 清理旧文件
# ============================================
log_step "2" "清理旧文件"

rm -rf build dist
mkdir -p dist
log_success "清理完成"

# ============================================
# 步骤2.5: 注入 Git Commit Hash
# ============================================
log_step "2.5" "注入构建信息"

# 从 Python 代码读取 Commit Hash（保证跨平台一致）
COMMIT_HASH=$(python3 -c "
try:
    from core.build_info_local import COMMIT_HASH
except ImportError:
    from core.build_info import COMMIT_HASH
print(COMMIT_HASH or 'unknown')
")
log_info "Commit Hash: ${COMMIT_HASH}"

# 备份原始 build_info.py
BUILD_INFO_FILE="core/build_info.py"
BUILD_INFO_BACKUP="${BUILD_INFO_FILE}.backup"
cp "${BUILD_INFO_FILE}" "${BUILD_INFO_BACKUP}"

# 注入 commit hash
sed -i.tmp "s/COMMIT_HASH = None/COMMIT_HASH = \"${COMMIT_HASH}\"/" "${BUILD_INFO_FILE}"
rm -f "${BUILD_INFO_FILE}.tmp"  # macOS sed 的临时文件

log_success "构建信息已注入"

# ============================================
# 步骤3: PyInstaller 打包
# ============================================
log_step "3" "PyInstaller 打包"

log_info "正在打包应用..."
${PYINSTALLER} SuperPicky.spec --clean --noconfirm

# 恢复原始 build_info.py
if [ -f "${BUILD_INFO_BACKUP}" ]; then
    mv "${BUILD_INFO_BACKUP}" "${BUILD_INFO_FILE}"
    log_info "已恢复原始 build_info.py"
fi

if [ ! -d "dist/${APP_NAME}.app" ]; then
    log_error "打包失败！未找到 dist/${APP_NAME}.app"
    exit 1
fi
log_success "打包完成"

# ============================================
# 步骤4: 深度代码签名
# ============================================
log_step "4" "深度代码签名"

# 签名所有嵌入的二进制文件和库
log_info "签名嵌入的框架和库..."
find "dist/${APP_NAME}.app/Contents" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 | while IFS= read -r -d '' file; do
    codesign --force --sign "${DEVELOPER_ID}" --timestamp --options runtime "$file" 2>/dev/null || true
done

# 签名可执行文件
find "dist/${APP_NAME}.app/Contents/MacOS" -type f -perm +111 -print0 | while IFS= read -r -d '' file; do
    codesign --force --sign "${DEVELOPER_ID}" --timestamp --options runtime "$file" 2>/dev/null || true
done

# 签名主应用
log_info "签名主应用..."
codesign --force --deep --sign "${DEVELOPER_ID}" \
    --timestamp \
    --options runtime \
    --entitlements entitlements.plist \
    "dist/${APP_NAME}.app"

# 验证签名
log_info "验证代码签名..."
codesign --verify --deep --strict --verbose=2 "dist/${APP_NAME}.app"
log_success "代码签名完成"

# ============================================
# 步骤5: 创建 DMG 安装包
# ============================================
log_step "5" "创建 DMG 安装包"

# 创建临时 DMG 文件夹
TEMP_DMG_DIR="dist/dmg_temp"
rm -rf "${TEMP_DMG_DIR}"
mkdir -p "${TEMP_DMG_DIR}"

# 复制应用到临时文件夹
cp -R "dist/${APP_NAME}.app" "${TEMP_DMG_DIR}/"

# 创建 Applications 快捷方式
ln -s /Applications "${TEMP_DMG_DIR}/Applications"

# 创建 DMG
log_info "使用 hdiutil 创建 DMG..."
hdiutil create -volname "${APP_NAME}" -srcfolder "${TEMP_DMG_DIR}" -ov -format UDZO "${DMG_PATH}"

# 清理临时文件夹
rm -rf "${TEMP_DMG_DIR}"
log_success "DMG 创建完成: ${DMG_PATH}"

# ============================================
# 步骤6: 签名 DMG
# ============================================
log_step "6" "签名 DMG"

codesign --force --sign "${DEVELOPER_ID}" --timestamp "${DMG_PATH}"
codesign --verify --verbose=2 "${DMG_PATH}"
log_success "DMG 签名完成"

# ============================================
# 步骤7: 公证（仅 release 模式）
# ============================================
if [ "$MODE" = "release" ]; then
    log_step "7" "Apple 公证"

    # 从 Keychain 获取密码
    APP_PASSWORD=$(security find-generic-password -a "${APPLE_ID}" -s "${KEYCHAIN_ITEM}" -w)

    log_info "提交到 Apple 公证服务..."
    log_info "（这可能需要几分钟时间）"

    NOTARIZE_OUTPUT=$(xcrun notarytool submit "${DMG_PATH}" \
        --apple-id "${APPLE_ID}" \
        --password "${APP_PASSWORD}" \
        --team-id "${TEAM_ID}" \
        --wait \
        --output-format json 2>&1)

    echo "${NOTARIZE_OUTPUT}"

    # 检查公证结果
    if echo "${NOTARIZE_OUTPUT}" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"Accepted"'; then
        log_success "公证成功!"

        # 步骤8: 装订公证票据
        log_step "8" "装订公证票据"
        xcrun stapler staple "${DMG_PATH}"
        xcrun stapler validate "${DMG_PATH}"
        log_success "票据装订完成"
    else
        log_error "公证失败!"

        # 提取 RequestUUID 并获取详细日志
        REQUEST_UUID=$(echo "${NOTARIZE_OUTPUT}" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
        if [ -n "${REQUEST_UUID}" ]; then
            log_info "获取详细公证日志..."
            xcrun notarytool log "${REQUEST_UUID}" \
                --apple-id "${APPLE_ID}" \
                --password "${APP_PASSWORD}" \
                --team-id "${TEAM_ID}"
        fi
        exit 1
    fi
else
    log_info "测试模式：跳过公证步骤"
fi

# ============================================
# 完成报告
# ============================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}构建完成!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "应用: ${CYAN}dist/${APP_NAME}.app${NC}"
echo -e "DMG:  ${CYAN}${DMG_PATH}${NC}"
echo -e "架构: ${CYAN}${ARCH_SUFFIX}${NC}"
echo ""

if [ "$MODE" = "release" ]; then
    echo -e "状态: ${GREEN}已公证，可分发${NC}"
    echo ""
    echo "下一步:"
    echo "  1. 测试 DMG 安装包"
    echo "  2. 上传到 GitHub Releases"
    echo ""
    echo "注意: 如需构建其他架构版本，请在对应架构的 Mac 上重新运行此脚本"
else
    echo -e "状态: ${YELLOW}已签名（未公证）${NC}"
    echo ""
    echo "注意: 测试模式下未进行公证，用户首次打开需要右键菜单"
    echo "发布正式版本请使用: ./build_release.sh --release"
fi

echo -e "${GREEN}========================================${NC}"
