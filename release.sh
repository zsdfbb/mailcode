#!/usr/bin/env bash
# MailCode 发布脚本
# 使用: bash release.sh [<版本号>]
# 例子: bash release.sh 0.2.0
# 如果不指定版本号，默认读取 mailcode/__init__.py 中的版本
set -euo pipefail

MAILCODE_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION_FILE="${MAILCODE_DIR}/mailcode/__init__.py"
PYPROJECT_FILE="${MAILCODE_DIR}/pyproject.toml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }

# ── 读取/指定版本 ──
CURRENT_VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$VERSION_FILE")
NEW_VER="${1:-$CURRENT_VER}"
TAG="v${NEW_VER}"

echo ""
echo "  MailCode 发布"
echo "  ============="
echo ""

if [ "$NEW_VER" != "$CURRENT_VER" ]; then
    info "更新版本: ${CURRENT_VER} → ${NEW_VER}"
    # POSIX 可移植写法: GNU sed (Linux) 不支持 -i '', BSD sed (macOS) 需要 -i ''
    sed 's/__version__ = ".*"/__version__ = "'"${NEW_VER}"'"/' "$VERSION_FILE" > "${VERSION_FILE}.tmp" \
        && mv "${VERSION_FILE}.tmp" "$VERSION_FILE"
    sed 's/^version = ".*"/version = "'"${NEW_VER}"'"/' "$PYPROJECT_FILE" > "${PYPROJECT_FILE}.tmp" \
        && mv "${PYPROJECT_FILE}.tmp" "$PYPROJECT_FILE"
    git add "$VERSION_FILE" "$PYPROJECT_FILE"
    git commit -m "chore: bump version to ${NEW_VER}"
    log "版本已更新并提交"
else
    info "版本: ${NEW_VER}"
fi

# ── 检查 tag 是否已存在 ──
if git rev-parse "$TAG" &>/dev/null; then
    err "Tag ${TAG} 已存在"
    exit 1
fi

# ── 生成 release notes ──
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || true)
LOG_RANGE="${LAST_TAG}..HEAD"
[ -z "$LAST_TAG" ] && LOG_RANGE="HEAD"

generate_release_notes() {
    local range="$1"
    echo "# MailCode ${NEW_VER}"
    echo ""

    local has_content=false

    for type_info in "🚀 新功能:feat:" "🐛 修复:fix:" "📖 文档:docs:" "♻️ 重构:refactor:" "🧪 测试:test:" "🔧 其他:chore:"; do
        local label="${type_info%%:*}"
        local prefix="${type_info##*:}"
        local commits
        commits=$(git log "$range" --oneline --no-decorate 2>/dev/null | grep -iE "^[a-f0-9]* ${prefix}([^:]*)?:" || true)
        if [ -n "$commits" ]; then
            has_content=true
            echo "### ${label}"
            echo "$commits" | sed 's/^[a-f0-9]* //' | while read -r line; do
                echo "- ${line#*: }"
            done
            echo ""
        fi
    done

    # 未被上述分类覆盖的 commit
    local other
    other=$(git log "$range" --oneline --no-decorate 2>/dev/null | grep -ivE "^[a-f0-9]* (feat|fix|docs|refactor|test|chore)([^:]*)?:" || true)
    if [ -n "$other" ]; then
        has_content=true
        echo "### 其他"
        echo "$other" | sed 's/^[a-f0-9]* //' | while read -r line; do
            echo "- ${line}"
        done
        echo ""
    fi

    if [ "$has_content" = false ]; then
        echo "首次发布。"
    fi
}

NOTES=$(generate_release_notes "$LOG_RANGE")

# ── 确认 ──
echo ""
info "版本:   ${NEW_VER}"
info "Tag:    ${TAG}"
echo ""
info "变更日志:"
echo "---"
echo "$NOTES" | head -30
echo "---"
echo ""
read -p "  发布此版本? [y/N] " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    warn "已取消"
    exit 1
fi

# ── 打 tag ──
git tag "$TAG"
log "Tag 已创建: ${TAG}"

# ── push ──
git push origin "$TAG"
log "Tag 已推送，CI lint 已触发"

echo ""
log "打 tag 完成！"
echo ""
info "CI 进度:"
echo "  https://github.com/zsdfbb/mailcode/actions"
