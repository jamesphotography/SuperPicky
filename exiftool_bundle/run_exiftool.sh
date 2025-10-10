#!/bin/bash
# ExifTool 启动脚本 - 自动设置 Perl 库路径

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 设置 Perl 库路径，让 exiftool 能找到 Image::ExifTool 模块
export PERL5LIB="${SCRIPT_DIR}/lib:${PERL5LIB}"

# 运行 exiftool，传递所有命令行参数
exec "${SCRIPT_DIR}/exiftool" "$@"
