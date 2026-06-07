#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"

usage() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  --unit          仅运行单元测试 (tests/unit/)"
    echo "  --integration   仅运行集成测试 (tests/integration/，需 test_config.json)"
    echo "  --binary        构建二进制并运行 smoke 测试 (tests/binary/)"
    echo "  --all           运行全部测试 (--unit --integration --binary)"
    echo ""
    echo "默认: --unit"
    exit 0
}

find_python() {
    if [ -f "$VENV_PYTHON" ]; then
        echo "$VENV_PYTHON"
    else
        echo "python3"
    fi
}

PYTHON=$(find_python)
cd "$SCRIPT_DIR"

run_unit() {
    echo "===== Unit 测试 ====="
    "$PYTHON" -m pytest unit/ -v "$@"
}

run_integration() {
    echo "===== Integration 测试 ====="
    CONFIG="$HOME/.config/mailcode/test_config.json"
    if [ ! -f "$CONFIG" ]; then
        echo "跳过: 缺少 $CONFIG"
        return 0
    fi
    "$PYTHON" -m pytest integration/ -v "$@"
}

run_binary() {
    echo "===== Binary 测试 ====="
    "$PYTHON" -m pytest binary/ -v -s "$@"
}

if [ $# -eq 0 ]; then
    run_unit
    exit $?
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h)
            usage
            ;;
        --unit)
            run_unit
            shift
            ;;
        --integration)
            run_integration
            shift
            ;;
        --binary)
            run_binary
            shift
            ;;
        --all)
            run_unit && run_integration && run_binary
            shift
            ;;
        *)
            echo "未知选项: $1"
            usage
            ;;
    esac
done
