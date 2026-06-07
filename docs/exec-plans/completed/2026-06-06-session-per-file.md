# Session-Per-File 重构 执行计划

> 每个任务按目录划分, 同一目录下的所有变更由一个 subagent 完成。按依赖顺序执行。

## 上下文引用

参考设计计划: `docs/design-plans/2026-06-06-session-per-file.md`

## 任务清单

### Task 1: `mailcode/relay/` — 重写 conversation_handler.py 核心
- **涉及目录**: `mailcode/relay/`, `tests/unit/`
- **涉及文件**:
  - `mailcode/relay/conversation_handler.py` (完全重写, ~350 行)
  - `mailcode/relay/email_listener.py` (line 356-358 微调, 不传 project_dir)
- **描述**: 重写 `ConversationHandler`, 改 threads.json → session_xxx.json + index.json。实现 session 生命周期、cwd 提取、index 同步、TTL 清理
- **依赖**: 无 (核心模块, 第一个做)
- **验证标准**:
  - [ ] ✅ UT: `_new_session_id` 返回 12 位 hex
  - [ ] ✅ UT: `_load_session` / `_save_session` 读写正确 (含损坏文件回退)
  - [ ] ✅ UT: `_load_index` / `_save_index` / `_update_index` / `_remove_from_index` 全部场景
  - [ ] ✅ UT: `_find_session_by_msg_id` 走 index 优先, 扫描兜底
  - [ ] ✅ UT: `_extract_cwd` / `_strip_cwd` 覆盖: 有 cwd / 无 cwd / `cwd: ~` / `cwd: ./relative` / `cwd: /nonexistent` / 大小写
  - [ ] ✅ UT: `handle_email` 新对话流程
  - [ ] ✅ UT: `handle_email` 续接对话 (In-Reply-To 命中 index)
  - [ ] ✅ UT: `handle_email` claude 失败 (returncode != 0 / FileNotFoundError / TimeoutExpired) → 发错误邮件
  - [ ] ✅ UT: `handle_email` 空 response → 发错误邮件
  - [ ] ✅ UT: `handle_email` 发送失败 → 仍写 session, 返回 False
  - [ ] ✅ UT: `handle_email` cwd 提取后从 body 剥离
  - [ ] ✅ UT: `handle_email` session.cwd 粘性 (不重复设置)
  - [ ] ✅ UT: `_cleanup_expired_sessions` 按 TTL 删除 + 损坏 warn
  - [ ] ✅ UT: 现有 `_call_claude` 测试无回归
  - [ ] ✅ Lint: `ruff check mailcode/relay/conversation_handler.py` 通过

### Task 2: `mailcode/cli.py` — session 子命令
- **涉及目录**: `mailcode/`
- **涉及文件**:
  - `mailcode/cli.py` (line 29-39 重写 cmd_conversation → cmd_session, line 271-278 重写 subparser)
- **描述**: 顶层命令 `conversation` → `session`, 子命令 list / show / delete / cleanup 全部实现
- **依赖**: Task 1 (依赖 ConversationHandler 公开 API)
- **验证标准**:
  - [ ] ✅ UT: `mailcode session list` 调用 handler.list_conversations
  - [ ] ✅ UT: `mailcode session show <id>` 调用 handler.get_session_status
  - [ ] ✅ UT: `mailcode session delete <id>` 调用 handler.terminate_session + 同步 index
  - [ ] ✅ UT: `mailcode session cleanup [--dry-run]` 按 TTL 扫描删除
  - [ ] ✅ Lint: `ruff check mailcode/cli.py` 通过
  - [ ] ✅ Manual: `python3 -m mailcode session --help` 输出正确

### Task 3: `mailcode/config.py` + `mailcode/resources/default.json` — 配置清理
- **涉及目录**: `mailcode/`
- **涉及文件**:
  - `mailcode/config.py` (line 230-247 重写, CONVERSATION_DEFAULTS → SESSION_DEFAULTS, 改 get_conversation_config → get_session_config)
  - `mailcode/resources/default.json` (line 24-35 重写 conversation 段)
- **描述**: 删 `system_prompt` (走 CLAUDE.md), 加 `session_ttl_days` + `cleanup_on_startup`, 函数改名
- **依赖**: 无 (配置层独立, 可跟 Task 1 并发)
- **验证标准**:
  - [ ] ✅ UT: `get_session_config()` 读 `session.*` 段
  - [ ] ✅ UT: `get_session_config()` 默认值包含 `session_ttl_days: 90`
  - [ ] ✅ UT: `is_session_enabled()` 替代 `is_conversation_enabled()`
  - [ ] ✅ UT: 现有调用 `get_conversation_config()` / `is_conversation_enabled()` 的地方已全部更新
  - [ ] ✅ Lint: `ruff check mailcode/config.py` 通过

### Task 4: `tests/unit/` — 单元测试重写
- **涉及目录**: `tests/unit/`
- **涉及文件**:
  - `tests/unit/test_conversation_handler.py` (完全重写, 适配新 API, 覆盖 Task 1 所有验收点)
- **描述**: 重写 ConversationHandler 单元测试, 全面覆盖新 API (session IO, index, cwd, TTL, error paths)
- **依赖**: Task 1 (需要新 ConversationHandler 存在)
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_conversation_handler.py -v` 全过
  - [ ] ✅ UT: 覆盖 16+ 个测试用例 (见 Task 1 的 checklist)
  - [ ] ✅ Lint: `ruff check tests/unit/test_conversation_handler.py` 通过

### Task 5: `docs/design-final/design.md` + `CLAUDE.md` — 文档更新
- **涉及目录**: `docs/design-final/`, 项目根
- **涉及文件**:
  - `docs/design-final/design.md` (追加 "Session 管理" 章节, 替换原 threads.json 描述)
  - `CLAUDE.md` (更新 `mailcode session` 命令, 删 `mailcode conversation`)
- **描述**: 设计文档同步新架构; 项目说明同步 CLI 改名
- **依赖**: Task 1 + Task 2 (确保实现已落地再写文档)
- **验证标准**:
  - [ ] ✅ Manual: docs/design-final/design.md 中"Session 管理"章节描述文件结构、cwd 机制、index
  - [ ] ✅ Manual: CLAUDE.md 命令清单已从 `mailcode conversation` 改为 `mailcode session`
  - [ ] ✅ Manual: CLAUDE.md 目录结构图已更新

## 验证清单

完成后跑全量回归:

- [ ] 运行 `pytest tests/unit/ -v` — 全过
- [ ] 运行 `pytest tests/ -v` — 全过 (无回归)
- [ ] 运行 `ruff check mailcode/ tests/` — 通过
- [ ] 运行 `python3 -m mailcode session --help` — 输出正确
- [ ] 运行 `python3 -m mailcode session list` — 输出格式正确 (空时友好提示)
- [ ] Manual: 启动 `mailcode serve --once --dry-run` 跑通 IMAP → handler → SMTP 全流程

**拆分逻辑**:
- Task 1 (relay/) 和 Task 3 (config/) 涉及目录不同 → 可**并发**
- Task 2 (cli/) 显式依赖 Task 1
- Task 4 (tests/) 显式依赖 Task 1
- Task 5 (docs/) 显式依赖 Task 1 + Task 2 (实现 + CLI 都到位)
- 同批次内并发, 跨批次串行
