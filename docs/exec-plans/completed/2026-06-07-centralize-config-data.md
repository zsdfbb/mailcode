# 集中配置和数据文件 执行计划

> 每个任务按目录划分,同一目录的所有变更由一个 agent 完成。按依赖顺序执行。

## 上下文引用
参考设计计划: `docs/design-plans/2026-06-07-centralize-config-data.md`

## 任务清单

### Task 1: mailcode/config.py + resources/default.json + cli.py (配置层清理)
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/config.py`, `mailcode/resources/default.json`, `mailcode/cli.py`
- **描述**: 删除 `get_default_project_dir()` 和 `get_projects_dir()` 函数;删除内联 `default_project_dir` 字段
- **验证标准**:
  - [ ] ✅ `mailcode/config.py` 不再含 `get_default_project_dir` 或 `get_projects_dir`
  - [ ] ✅ `mailcode/resources/default.json` 不再含 `default_project_dir` 键
  - [ ] ✅ `mailcode/cli.py` 的测试配置模板不再含 `default_project_dir` 键
  - [ ] ✅ `mailcode --version` 能正常 import 不报错

### Task 2: mailcode/relay/{conversation_handler, email_listener}.py (handler 层)
- **涉及目录**: `mailcode/relay/`
- **涉及文件**: `mailcode/relay/conversation_handler.py`, `mailcode/relay/email_listener.py`
- **描述**: 路径常量改 `~/.local/share/mailcode/data` → `~/.config/mailcode`;`email_listener.py` 全面重命名(属性、4 个方法、2 个 JSON 键)
- **验证标准**:
  - [ ] ✅ `conversation_handler.py` 中 `_DATA_DIR` 改为 `_MAILCODE_HOME` 指向 `~/.config/mailcode`
  - [ ] ✅ `conversation_handler.py` docstring 同步更新
  - [ ] ✅ `email_listener.py` 中无 `sent_messages_path` 残留
  - [ ] ✅ `email_listener.py` 中无 `sent-messages.json` 字面量
  - [ ] ✅ `email_listener.py` 含 `state_path` / `_load_state` / `_save_state` / `_prune_old_sent_messages` / `_is_duplicate` / `processed_uids` / `sent_messages`
  - [ ] ✅ `grep -rn "sent-messages\|sent_messages_path" mailcode/` 无结果

### Task 3: install.sh + uninstall.sh (脚本层)
- **涉及目录**: 项目根
- **涉及文件**: `install.sh`, `uninstall.sh`
- **描述**: `install.sh` 删 `default_project_dir` 写入块;`uninstall.sh` 删 `DATA_DIR` 相关逻辑(数据已合入 CONFIG_DIR)
- **验证标准**:
  - [ ] ✅ `install.sh` 不再含 `default_project_dir` 字面量
  - [ ] ✅ `uninstall.sh` 不再含 `DATA_DIR` 变量
  - [ ] ✅ `bash -n install.sh` 语法通过
  - [ ] ✅ `bash -n uninstall.sh` 语法通过

### Task 4: tests/ (测试层)
- **涉及目录**: `tests/unit/`
- **涉及文件**: `tests/unit/conftest.py`, `tests/unit/test_config.py`
- **描述**: 删 `get_default_project_dir` 相关 3 个测试 + 1 个 import;改 conftest.py docstring;删 fixture 里残留的 `default_project_dir`
- **验证标准**:
  - [ ] ✅ `test_config.py` 不再 import `get_default_project_dir`
  - [ ] ✅ `test_config.py` 不再含 `test_get_default_project_dir` 测试函数(3 个)
  - [ ] ✅ `conftest.py` docstring 反映新路径
  - [ ] ✅ `conftest.py` fixture 不再含 `default_project_dir`
  - [ ] ✅ `pytest tests/unit/test_config.py -q` 通过

### Task 5: docs/design-final/design.md (设计文档)
- **涉及目录**: `docs/design-final/`
- **涉及文件**: `docs/design-final/design.md`
- **描述**: 替换 `sent-messages.json` → `state.json`;替换 `~/.local/share/mailcode/data/` → `~/.config/mailcode/`
- **验证标准**:
  - [ ] ✅ `grep -n "sent-messages\.json\|data/sent-messages" docs/design-final/design.md` 无结果
  - [ ] ✅ `grep -n "\.local/share/mailcode" docs/design-final/design.md` 无结果
  - [ ] ✅ `state.json` 引用正确(注明内含 `processed_uids` + `sent_messages` 键)

### Task 6: 整体验证 + 报告
- **涉及目录**: 项目根
- **描述**: 全量测试 + lint + 派 subagent 写报告
- **验证标准**:
  - [ ] ✅ `pytest tests/unit/ -q` 全部通过
  - [ ] ✅ `ruff check mailcode/ tests/` 无错误
  - [ ] ✅ `mailcode --version` 可执行
  - [ ] ✅ `docs/reports/2026-06-07-centralize-config-data-report.md` 已生成

## 验证清单
- [ ] 运行 `pytest tests/unit/ -q` — 通过
- [ ] 运行 `ruff check mailcode/ tests/` — 通过
- [ ] 运行 `bash -n install.sh uninstall.sh` — 通过
- [ ] grep 验证: 无 `sent-messages` / `~/.local/share/mailcode` / `default_project_dir` 残留(除 completed/ 历史文档外)

## 依赖关系
- Task 1 / 2 / 3 / 4 / 5 涉及不同目录,可并发
- Task 6 必须在 1-5 全部完成后串行执行
