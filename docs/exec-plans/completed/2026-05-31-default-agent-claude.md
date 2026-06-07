# 默认 Agent 切换为 Claude Code 执行计划

> 参考设计计划：`docs/design-plans/2026-05-31-default-agent-claude.md`

## 任务清单

### Task 1: 源代码默认值修改
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/resources/default.json`, `mailcode/config.py`, `mailcode/relay/session_launcher.py`
- **描述**: 将 4 处默认值从 "opencode" 改为 "claude"
- **验证标准**:
  - [ ] ✅ UT: 测试断言通过
  - [ ] ✅ Lint: ruff check 通过

### Task 2: 测试文件更新
- **涉及目录**: `tests/`
- **涉及文件**: `tests/unit/test_claude_code.py`, `tests/unit/test_config.py`, `tests/unit/conftest.py`
- **描述**: 更新 6 处测试断言 + 1 处夹具默认值 + 3 处注释
- **验证标准**:
  - [ ] ✅ UT: pytest tests/unit/ 全部通过
  - [ ] ✅ Lint: ruff check 通过

### Task 3: 全量验证
- **描述**: 运行完整测试套件和 lint
- **验证标准**:
  - [ ] ✅ `pytest tests/unit/ -q` 通过
  - [ ] ✅ `ruff check mailcode/ tests/` 通过

## 验证清单
- [ ] 运行 `pytest tests/unit/ -q` — 通过
- [ ] 运行 `ruff check mailcode/ tests/` — 通过
