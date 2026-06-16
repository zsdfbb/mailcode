#!/usr/bin/env bash
# MailCode 安装脚本
# 默认安装到当前项目的 .venv（不存在则调用 prepare.sh 创建），不污染系统环境
# 用 --system 可恢复旧行为：安装到 user site-packages
set -euo pipefail

MAILCODE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${MAILCODE_DIR}/.venv"
LOCAL_WHEEL=""
USE_SYSTEM=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }

# ── 参数解析 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            if [[ -z "${2:-}" ]]; then
                err "--local 需要指定 wheel 文件路径"
                exit 1
            fi
            LOCAL_WHEEL="$2"
            shift 2
            ;;
        --system)
            USE_SYSTEM=1
            shift
            ;;
        -h|--help)
            echo "用法: bash install.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --local <wheel>  从本地 wheel 文件安装"
            echo "  --system         装到 user site-packages（不创建 venv，旧行为）"
            echo "  -h, --help       显示此帮助"
            echo ""
            echo "示例:"
            echo "  bash install.sh                                    # 默认装到 .venv（不存在则调用 prepare.sh）"
            echo "  bash install.sh --local dist/mailcode-0.3.0-py3-none-any.whl"
            echo "  bash install.sh --system                           # 旧行为：装到 ~/.local"
            exit 0
            ;;
        *)
            warn "未知参数: $1 (使用 --help 查看帮助)"
            shift
            ;;
    esac
done

echo ""
echo "  MailCode 安装"
echo "  ============="
echo ""

# ── 1. 检查 Python ──
if ! command -v python3 &>/dev/null; then
    err "未找到 python3，请先安装 Python 3"
    exit 1
fi
log "Python3: $(command -v python3)"

# ── 2. 选择安装目标 ──
if [ "$USE_SYSTEM" = 1 ]; then
    # 旧行为：pip + --user 装到 user site
    PIP_CMD="pip"
    if command -v pip3 &>/dev/null; then
        PIP_CMD="pip3"
    fi
    if ! command -v "${PIP_CMD}" &>/dev/null; then
        err "未找到 pip，请先安装 pip: python3 -m ensurepip"
        exit 1
    fi
    log "pip: $(command -v ${PIP_CMD})"
    INSTALL_DIR="$(python3 -c "import site; print(site.USER_BASE + '/bin')" 2>/dev/null || echo "${HOME}/.local/bin")"
    IN_VENV=$(python3 -c "import sys; print(sys.prefix != sys.base_prefix)")
    if [ "$IN_VENV" = "True" ]; then
        PIP_FLAGS=""
    else
        # --break-system-packages 在 PEP 668 环境是必需，在非 PEP 668 环境被忽略
        PIP_FLAGS="--user --break-system-packages"
    fi
    info "使用 --system：装到 ${INSTALL_DIR}"
else
    # 默认：.venv 优先；不存在则调用 prepare.sh（创建 venv + 装 dev 依赖 + editable mailcode）
    if [ ! -d "$VENV_DIR" ]; then
        info ".venv 不存在，调用 prepare.sh 创建..."
        if ! bash "${MAILCODE_DIR}/prepare.sh" 2>&1 | sed 's/^/  /'; then
            err "prepare.sh 失败，请检查错误信息后重试"
            exit 1
        fi
    fi
    if [ ! -x "$VENV_DIR/bin/pip" ]; then
        err ".venv/bin/pip 不存在或不可执行: ${VENV_DIR}"
        err "请删除后重试: rm -rf ${VENV_DIR} && bash ${MAILCODE_DIR}/prepare.sh --recreate"
        exit 1
    fi
    PIP_CMD="${VENV_DIR}/bin/pip"
    INSTALL_DIR="${VENV_DIR}/bin"
    PIP_FLAGS=""
    info "安装到: ${VENV_DIR} (mailcode: ${INSTALL_DIR}/mailcode)"
fi

# ── 3. 安装 mailcode 包 ──
if [ -n "${LOCAL_WHEEL}" ]; then
    if [ ! -f "${LOCAL_WHEEL}" ]; then
        ALT_PATH="${MAILCODE_DIR}/${LOCAL_WHEEL}"
        if [ -f "${ALT_PATH}" ]; then
            LOCAL_WHEEL="${ALT_PATH}"
        else
            err "wheel 文件不存在: ${LOCAL_WHEEL}"
            exit 1
        fi
    fi
    info "从本地 wheel 安装: ${LOCAL_WHEEL}"
    # shellcheck disable=SC2086
    if ! ${PIP_CMD} install ${PIP_FLAGS} "${LOCAL_WHEEL}" 2>&1 | sed 's/^/  /'; then
        err "pip 安装失败，请检查错误信息后重试"
        exit 1
    fi
    log "mailcode 已通过 pip 安装 (本地 wheel)"
else
    # 默认流程：先 build 出 wheel，再从 wheel 安装（与 PyPI 用户拿到的产物一致）
    info "构建 wheel（首次约 30-60 秒）: bash ${MAILCODE_DIR}/build.sh"
    if ! bash "${MAILCODE_DIR}/build.sh" 2>&1 | sed 's/^/  /'; then
        err "wheel 构建失败，请检查错误信息后重试"
        exit 1
    fi
    # 找到 build.sh 产出的 wheel
    BUILT_WHEEL=$(ls -t "${MAILCODE_DIR}"/dist/mailcode-*.whl 2>/dev/null | head -1)
    if [ -z "${BUILT_WHEEL}" ]; then
        err "build.sh 未产出 wheel: ${MAILCODE_DIR}/dist/"
        exit 1
    fi
    info "从构建产物安装: ${BUILT_WHEEL}"
    # shellcheck disable=SC2086
    if ! ${PIP_CMD} install ${PIP_FLAGS} --force-reinstall "${BUILT_WHEEL}" 2>&1 | sed 's/^/  /'; then
        err "pip 安装失败，请检查错误信息后重试"
        exit 1
    fi
    log "mailcode 已通过 pip 安装 (本地构建的 wheel)"
fi

# 确保刚安装的 mailcode 在 PATH 中（脚本内）
export PATH="${INSTALL_DIR}:${PATH}"
hash -r 2>/dev/null || true

# ── 3.5 检查 Claude Code ──
if command -v claude &>/dev/null; then
    log "Claude Code 已安装: $(claude --version 2>/dev/null || echo '版本未知')"
else
    warn "未检测到 Claude Code"
    echo "  MailCode 需要 Claude Code 来处理邮件命令。请访问以下地址安装:"
    echo "  https://docs.anthropic.com/en/docs/claude-code/overview"
    echo ""
fi

# ── 4. 初始化/升级配置 ──
mailcode config init
log "配置已就绪"
warn "请编辑配置填入邮箱和密码: ~/.config/mailcode/config.json"
echo ""

# ── 5. 项目级 symlink: ~/.mailcode → MAILCODE_DIR ──
if [ ! -d "${HOME}/.mailcode" ]; then
    ln -sf "${MAILCODE_DIR}" "${HOME}/.mailcode"
    log "已创建: ~/.mailcode → ${MAILCODE_DIR}"
fi

# ── 6. 路径设置 ──
if [ "$USE_SYSTEM" = 1 ]; then
    # 旧行为：自动把 INSTALL_DIR 写入 shell rc
    if ! echo "${PATH}" | tr ':' '\n' | grep -qxF "${INSTALL_DIR}"; then
        case "${SHELL:-}" in
            *zsh*)   RC_FILE="${HOME}/.zshrc" ; LINE="export PATH=\"${INSTALL_DIR}:\${PATH}\"" ;;
            *bash*)  RC_FILE="${HOME}/.bashrc" ; LINE="export PATH=\"${INSTALL_DIR}:\${PATH}\"" ;;
            *fish*)  RC_FILE="${HOME}/.config/fish/config.fish" ; LINE="fish_add_path ${INSTALL_DIR}" ;;
            *)       RC_FILE="" ;;
        esac
        if [ -n "${RC_FILE}" ] && [ -f "${RC_FILE}" ]; then
            if [ "${SHELL##*/}" = "fish" ]; then
                if ! grep -qxF "fish_add_path ${INSTALL_DIR}" "${RC_FILE}" 2>/dev/null; then
                    echo "" >> "${RC_FILE}"
                    echo "# MailCode" >> "${RC_FILE}"
                    echo "${LINE}" >> "${RC_FILE}"
                    log "已自动添加 PATH 到 ${RC_FILE}"
                    info "执行以下命令立即生效: source ${RC_FILE}"
                fi
            else
                if ! grep -qxF "export PATH=\"${INSTALL_DIR}" "${RC_FILE}" 2>/dev/null; then
                    echo "" >> "${RC_FILE}"
                    echo "# MailCode" >> "${RC_FILE}"
                    echo "${LINE}" >> "${RC_FILE}"
                    log "已自动添加 PATH 到 ${RC_FILE}"
                    info "执行以下命令立即生效: source ${RC_FILE}"
                fi
            fi
        else
            warn "${INSTALL_DIR} 不在 PATH 中，请手动添加到 shell rc 文件:"
            echo "  ${LINE}"
        fi
    fi
else
    # venv 模式：不写入 shell rc（项目本地路径），提示用户激活
    info "使用 mailcode 前先激活虚拟环境（本脚本已自动加入 PATH，仅本会话生效）:"
    echo "  source ${VENV_DIR}/bin/activate"
fi

echo ""
# ── 7. 创建符号链接 ──
ln -sf "${HOME}/.config/mailcode/config.json" "${MAILCODE_DIR}/user_config.json"
log "已创建: user_config.json → ${HOME}/.config/mailcode/config.json"
ln -sf "${HOME}/.config/mailcode/test_config.json" "${MAILCODE_DIR}/test_config.json"
log "已创建: test_config.json → ${HOME}/.config/mailcode/test_config.json"

# ── 8. 初始化测试配置文件（如不存在） ──
if [ ! -f "${HOME}/.config/mailcode/test_config.json" ]; then
    if command -v mailcode &>/dev/null; then
        mailcode config init-test 2>/dev/null || true
    fi
fi

log "安装完成！"
echo ""
info "启动中继:"
echo "  mailcode serve"
echo ""
