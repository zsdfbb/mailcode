# 配置修复 执行计划

## 上下文引用

参考设计计划：`docs/design-plans/2026-05-31-config-fix.md`

## 任务清单

### Task 1: mailcode/resources/default.json
- **涉及目录**: `mailcode/resources/`
- **涉及文件**: `mailcode/resources/default.json`
- **描述**: 在 mailcode_bot 段增加 `agent_type`、`default_project_dir`、`from_name`、`check_interval`、`session_expiry_hours` 字段
- **验证标准**:
  - [ ] ✅ 文件 JSON 格式正确
  - [ ] ✅ 新加字段都有合理的默认值

### Task 2: mailcode/config.py（核心修复）
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/config.py`
- **描述**: 
  1. 修复 `_ensure_user_config()` 内联 fallback 增加各字段，删除重复 mkdir
  2. 修复 `get_email_config()` 从 `mailcode_bot` 读 `from_name`、`check_interval`、`session_expiry_hours`，向后兼容旧 `email` 段
  3. 修复 `get_agent_type()` 从 `mailcode_bot.agent_type` 读，回退到 `email.agent_type`
  4. 修复 `get_default_project_dir()` 从 `mailcode_bot.default_project_dir` 读，回退到 `email.default_project_dir`
- **验证标准**:
  - [ ] ✅ UT: mailbox_bot 段产生正确的 smtp/imap/email 配置
  - [ ] ✅ UT: 旧 `account` 段向后兼容仍有效
  - [ ] ✅ UT: `get_email_config()` 返回 `from_name`、`check_interval`、`session_expiry_hours`
  - [ ] ✅ UT: `get_agent_type()` 读 `mailcode_bot.agent_type`
  - [ ] ✅ UT: `get_agent_type()` 回退到 `email.agent_type`（旧格式）
  - [ ] ✅ UT: `get_default_project_dir()` 读 `mailcode_bot.default_project_dir`
  - [ ] ✅ UT: `get_default_project_dir()` 回退到 `email.default_project_dir`（旧格式）

### Task 3: mailcode/cli.py
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/cli.py`
- **描述**:
  1. 修复 `_cmd_config_validate()` 使用 `get_smtp_config()`/`get_imap_config()` 验证（包含 auto-detected 值）
  2. 成功输出也用 merged config 显示
  3. 移除未使用的 `_detect_provider`、`PROVIDER_PRESETS` 导入
- **验证标准**:
  - [ ] ✅ UT: 无 smtp/imap 段的简化配置通过 validate
  - [ ] ✅ Manual: `mailcode config validate` 显示 auto-detected 值

### Task 4: mailcode/channels/email_channel.py
- **涉及目录**: `mailcode/channels/`
- **涉及文件**: `mailcode/channels/email_channel.py`
- **描述**: 修复 L66 错误信息，引用 `mailcode_bot.email` 替代 `email.from`
- **验证标准**:
  - [ ] ✅ UT: 现有测试通过无变化

### Task 5: 测试文件更新
- **涉及目录**: `tests/unit/`
- **涉及文件**: `tests/unit/test_config.py`, `tests/unit/test_claude_code.py`, `tests/unit/conftest.py`
- **描述**:
  1. `conftest.py`: 新增 `mock_config_new_format` fixture（仅有 mailcode_bot + security 的新格式）
  2. `test_config.py`: 添加 mailcode_bot 段测试、get_email_config 富字段测试
  3. `test_claude_code.py`: 添加 mailcode_bot.agent_type 测试
- **验证标准**:
  - [ ] ✅ 全部测试通过: `pytest tests/unit/ -q`

## 验证清单
- [ ] 运行 `pytest tests/unit/ -q` — 全部通过
- [ ] 运行 `ruff check mailcode/ tests/` — 无问题
