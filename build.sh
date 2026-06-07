#!/usr/bin/env bash
# MailCode 构建脚本 — 构建 pip wheel 包
# 用法:
#   bash build.sh              # 构建 wheel 到 dist/
#   bash build.sh --help       # 显示用法
set -euo pipefail

MAILCODE_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }

usage() {
    echo ""
    echo "  用法: bash build.sh [选项]"
    echo ""
    echo "  选项:"
    echo "    --help       显示此帮助信息"
    echo ""
    echo "  说明:"
    echo "    构建 pip wheel 到 dist/ 目录，供 install.sh 安装使用。"
    echo ""
}

# ── 检查 Python ──
PYTHON="${MAILCODE_PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    err "未找到 Python: ${PYTHON}"
    echo "  可通过环境变量 MAILCODE_PYTHON 指定 Python 路径"
    exit 1
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

# 检查是否已在虚拟环境
IN_VENV=$("$PYTHON" -c "import sys; print(sys.prefix != sys.base_prefix)")

# ── 解析参数 ──
case "${1:-}" in
    --help|-h)
        usage
        exit 0
        ;;
    -*)
        err "未知选项: $1"
        usage
        exit 1
        ;;
esac

echo ""
echo "  MailCode 构建（wheel）"
echo "  ====================="
echo ""

log "Python: $("$PYTHON" --version) (${PY_VER})"

# 确保 build 模块可用（必须是 PyPI 上的 build 包，不能是本地 ./build/ 命名空间包）
if ! "$PYTHON" -c "from build.__main__ import main" &>/dev/null; then
    info "安装 build..."
    PIP_FLAGS=""
    if [ "$IN_VENV" != "True" ]; then
        PIP_FLAGS="--break-system-packages"
    fi
    # shellcheck disable=SC2086
    "$PYTHON" -m pip install build -q $PIP_FLAGS 2>&1 | tail -3
fi
log "build 模块已就绪"

# 清理掉本地的 ./build/ 目录 — 它是 distutils 中间产物，但会和 PyPI 的 build 包同名
# 导致 `python -m build` 把这个空目录当作 build 包执行（没有 __main__.py 而失败）。
shopt -s nullglob
rm -rf "$MAILCODE_DIR/build"

# 构建 wheel
info "构建 wheel..."
"$PYTHON" -m build --wheel "$MAILCODE_DIR" 2>&1
log "构建完成"

# 清理 distutils 中间产物（wheel 已在 dist/，build/ 仅为缓存）
rm -rf "$MAILCODE_DIR/build"

# 验证产物
shopt -s nullglob
WHEELS=("$MAILCODE_DIR"/dist/mailcode-*.whl)
if [ ${#WHEELS[@]} -eq 0 ]; then
    err "wheel 未生成: ${MAILCODE_DIR}/dist/mailcode-*.whl"
    exit 1
fi

WHEEL_FILE="${WHEELS[${#WHEELS[@]}-1]}"
WHEEL_SIZE=$(du -sh "$WHEEL_FILE" | awk '{print $1}')
log "wheel: ${WHEEL_FILE} (${WHEEL_SIZE})"

echo ""
log "构建成功！"
echo ""
info "安装: bash install.sh --local ${WHEEL_FILE}"
echo ""
