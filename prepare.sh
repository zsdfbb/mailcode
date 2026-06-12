#!/usr/bin/env bash
# MailCode 开发环境准备脚本
# 用法:
#   bash prepare.sh              # 创建/复用 .venv 并安装依赖
#   bash prepare.sh --recreate   # 删除旧 .venv 后重建
#   bash prepare.sh --help       # 显示帮助
set -euo pipefail

MAILCODE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${MAILCODE_DIR}/.venv"
REQS_FILE="${MAILCODE_DIR}/requirements-dev.txt"

# 默认使用国内 PyPI 镜像（可通过环境变量覆盖）
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }

usage() {
    cat <<EOF

  MailCode 开发环境准备

  用法: bash prepare.sh [选项]

  选项:
    --recreate     删除已有 .venv 后重建
    -h, --help     显示此帮助

  说明:
    在当前仓库下创建 .venv 虚拟环境，并以可编辑模式安装 mailcode 包，
    随后安装 requirements-dev.txt 中的开发依赖。
    与 CLAUDE.md 中 \$source .venv/bin/activate 保持一致。

EOF
}

# ── 参数解析 ──
RECREATE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --recreate)
            RECREATE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "未知参数: $1（使用 --help 查看帮助）"
            exit 1
            ;;
    esac
done

echo ""
echo "  MailCode 开发环境准备"
echo "  ====================="
echo ""

# ── 1. 检查 Python ──
if ! command -v python3 &>/dev/null; then
    err "未找到 python3，请先安装 Python 3"
    exit 1
fi
PYTHON="${MAILCODE_PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    err "未找到 Python: ${PYTHON}（可通过 MAILCODE_PYTHON 指定）"
    exit 1
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log "Python: $("$PYTHON" --version) (${PY_VER})"

# 检测是否已在虚拟环境中
IN_VENV=$("$PYTHON" -c "import sys; print(sys.prefix != sys.base_prefix)")
if [ "$IN_VENV" = "True" ]; then
    err "当前已在虚拟环境中，请退出后重试（先执行 deactivate）"
    exit 1
fi

# ── 2. 处理旧 venv 目录（早期脚本使用 venv/，已废弃）──
OLD_VENV="${MAILCODE_DIR}/venv"
if [ -d "$OLD_VENV" ]; then
    warn "发现旧的 venv 目录: ${OLD_VENV}"
    warn "项目已统一使用 .venv/，请手动删除旧目录: rm -rf ${OLD_VENV}"
fi

# ── 3. 创建/复用 .venv ──
if [ "$RECREATE" = 1 ] && [ -d "$VENV_DIR" ]; then
    info "删除旧 .venv: ${VENV_DIR}"
    rm -rf "$VENV_DIR"
fi

if [ -d "$VENV_DIR" ]; then
    log ".venv 已存在: ${VENV_DIR}（复用，跳过创建）"
else
    "$PYTHON" -m venv "$VENV_DIR"
    log ".venv 已创建: ${VENV_DIR}"
fi

VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PY_VER=$("$VENV_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

# ── 4. 升级 pip 并安装依赖 ──
info "升级 pip..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
log "pip 已就绪"

info "以可编辑模式安装 mailcode..."
"$VENV_DIR/bin/pip" install -e "$MAILCODE_DIR" -q
log "mailcode 已安装（editable）"

if [ ! -f "$REQS_FILE" ]; then
    err "未找到开发依赖文件: ${REQS_FILE}"
    exit 1
fi
info "安装开发依赖（来自 requirements-dev.txt）..."
"$VENV_DIR/bin/pip" install -r "$REQS_FILE" -q
log "开发依赖已安装"

echo ""
log "开发环境准备完成！"
echo ""
info "激活虚拟环境:"
echo "  source ${VENV_DIR}/bin/activate"
echo ""
info "运行测试:"
echo "  ${VENV_DIR}/bin/python3 -m pytest tests/unit/ -q"
echo ""
info "代码检查:"
echo "  ${VENV_DIR}/bin/python3 -m ruff check mailcode/ tests/"
echo ""
