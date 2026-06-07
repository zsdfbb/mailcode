# Stateless Fallback + 启动预检 + 默认 Session 开启 执行计划

> 每个任务按目录划分, 同一目录下的所有变更由一个 subagent 完成。按依赖顺序执行。

## 上下文引用

参考设计计划: `docs/design-plans/2026-06-06-stateless-fallback.md`

## 任务清单

### Task 1: `mailcode/relay/` — 抽 4 符号到模块级 + 新建 stateless_handler.py + 重写 process_email

- **涉及目录**: `mailcode/relay/`
- **涉及文件**:
  - `/Users/zs/Develop/MailCode/mailcode/relay/conversation_handler.py` — 4 个 `_` 前缀方法提到模块级 (line 225/248/272/303), `handle_email` 内部调用更新
  - `/Users/zs/Develop/MailCode/mailcode/relay/stateless_handler.py` (新) — `StatelessHandler` 类, `handle_email` 方法
  - `/Users/zs/Develop/MailCode/mailcode/relay/email_listener.py` — `__init__` 拆 lazy init (line 56 附近), `process_email` 完全重写 (line 351-372), 新增 `_handle_via_conversation` / `_handle_via_stateless` 私有方法
- **描述**:
  - 抽 4 个已经是纯函数的方法到 `conversation_handler.py` 模块级: `call_claude(prompt, cwd)` / `extract_cwd(body)` / `strip_cwd(body)` / `send_error_email(email_channel, from_email, subject, body, references, in_reply_to)`。**删除**原 4 个 `_` 前缀方法, 不留薄包装
  - `handle_email` 内部对 4 个方法的调用从 `self._xxx(...)` 改为 `xxx(...)`
  - 新建 `stateless_handler.py`: `StatelessHandler(email_channel)` 类 + `handle_email(from_email, subject, body, references, in_reply_to)` 方法, 内部调 `extract_cwd` / `strip_cwd` / `call_claude` / `send_error_email`
  - `email_listener.py:__init__` 把 `self.conv_handler = None` 拆为 `self._conv_handler = None; self._stateless_handler = None`
  - 重写 `process_email(self, email_entry, dry_run=False, force_session=None)`: 新增 `force_session` 参数, 路由表按设计文档, 私有方法 `_handle_via_conversation` / `_handle_via_stateless` 封装 lazy init + 返回元组翻译, 返回 `(success: bool, mode: str)`, `mode` ∈ `{"conversation", "stateless", "dry_run"}`
- **依赖**: 无 (核心模块, 第一个做)
- **验证标准**:
  - [ ] ✅ UT: `from mailcode.relay.stateless_handler import StatelessHandler` 导入成功
  - [ ] ✅ UT: `from mailcode.relay.conversation_handler import call_claude, extract_cwd, strip_cwd, send_error_email` 全部可独立导入
  - [ ] ✅ UT: 原 4 个 `_` 前缀方法在 `ConversationHandler` 上已删除 (`hasattr(ConversationHandler, "_call_claude") is False`)
  - [ ] ✅ UT: `IMAPListener()._conv_handler is None and _stateless_handler is None` 初始为 None
  - [ ] ✅ UT: `process_email(force_session=True)` 强制走 conversation (`mode == "conversation"`)
  - [ ] ✅ UT: `process_email(force_session=False)` 强制走 stateless (`mode == "stateless"`)
  - [ ] ✅ UT: `process_email(force_session=None, is_session_enabled=True)` 走 conversation
  - [ ] ✅ UT: `process_email(force_session=None, is_session_enabled=False)` 走 stateless
  - [ ] ✅ UT: `process_email(dry_run=True)` 走 dry_run, 不调 handler
  - [ ] ✅ UT: 二次调用 `process_email` 复用同一 handler 实例 (断言 `id()` 一致)
  - [ ] ✅ Lint: `ruff check mailcode/relay/conversation_handler.py mailcode/relay/email_listener.py mailcode/relay/stateless_handler.py` 通过

### Task 2: `mailcode/server.py` + `mailcode/cli.py` — 修 --session 死代码 + 加启动预检 + 改 help 文本

- **涉及目录**: `mailcode/`
- **涉及文件**:
  - `/Users/zs/Develop/MailCode/mailcode/server.py` — line 32 `listener.process_email(...)` 加 `force_session=args.session or None` 参数
  - `/Users/zs/Develop/MailCode/mailcode/cli.py`
    - `cmd_serve` (line 24-34) 顶部加 `validate_serve_config` 预检, 失败 `print("❌ MailCode 中继启动失败:")` + `sys.exit(1)`, 预检在 `setup_logging` 之前
    - `_cmd_config_validate` (line 189-228) 改用 `validate_serve_config()` 替换本地 SMTP/IMAP/bot 重复检查
    - `--session` help 文本 (line 290-291) 保持, 验证描述准确
    - `mailcode session` help 文本 (line 332) 删 "需在 config 中开启 session.enabled" 措辞
- **描述**:
  - server.py: 一行修改, 把 `args.session` 接入 `process_email`
  - cli.py: `cmd_serve` 顶部加预检, 复用 `_cmd_config_validate` 的 `validate_serve_config` 函数, 错误前缀用 "MailCode 中继启动失败", exit 1; help 文本微调
- **依赖**: Task 1 (依赖 `process_email` 的新签名)
- **验证标准**:
  - [ ] ✅ UT: `server.run_serve` 调 `process_email` 时 `force_session=args.session or None` 参数正确传递
  - [ ] ✅ UT: `cmd_serve(args)` 在 `validate_serve_config` 返回 `["err"]` 时 `SystemExit.code == 1`
  - [ ] ✅ UT: 预检失败时 stdout 含 "MailCode 中继启动失败" 和 "err"
  - [ ] ✅ UT: 预检失败时 `IMAPListener` 未构造 (`patch("mailcode.relay.email_listener.IMAPListener")` 不被调)
  - [ ] ✅ UT: 预检失败时 `setup_logging` 未被调 (`patch("mailcode.utils.logging.setup_logging")` 不被调)
  - [ ] ✅ UT: 预检通过时进入原 serve 流程
  - [ ] ✅ UT: `_cmd_config_validate` 改用 `validate_serve_config`, 不再含本地 SMTP/IMAP/bot 重复检查
  - [ ] ✅ Manual: `python3 -m mailcode serve --help` 输出含 `--session/-S` 且 help 文本准确
  - [ ] ✅ Lint: `ruff check mailcode/server.py mailcode/cli.py` 通过

### Task 3: `mailcode/config.py` + `mailcode/resources/default.json` — 翻 4 处默认 + 加 validate_serve_config

- **涉及目录**: `mailcode/`
- **涉及文件**:
  - `/Users/zs/Develop/MailCode/mailcode/config.py`
    - line 77 `_ensure_user_config` 内 `"enabled": False` → `True`
    - line 193 `SESSION_DEFAULTS` 内 `"enabled": False` → `True`
    - line 209 `is_session_enabled` 内 `get(...).get("enabled", False)` → `True`
    - 新增 `validate_serve_config() -> list[str]` 函数 (放在 `is_session_enabled` 之后)
  - `/Users/zs/Develop/MailCode/mailcode/resources/default.json` — line 32 `"enabled": false` → `"enabled": true`
- **描述**:
  - 4 处默认值同步翻转 (保证 `_ensure_user_config` / `SESSION_DEFAULTS` / `is_session_enabled` / `default.json` 全部一致)
  - `validate_serve_config` 函数: 复用 `_get_bot_config` / `get_smtp_config` / `get_imap_config` / `get_security_config`, 校验 5 类必填项, 返回错误消息列表
- **依赖**: 无 (配置层独立, 可与 Task 1 / Task 2 并发)
- **验证标准**:
  - [ ] ✅ UT: `test_is_session_enabled_default_true` — 写入无 session 段的最小 config, 断言 `is_session_enabled() is True`
  - [ ] ✅ UT: `test_get_session_config_merges_user_over_default` — 用户 `session.enabled: false` 覆盖默认 `True`, 返回 `False`
  - [ ] ✅ UT: `test_validate_serve_config_missing_email` — 返回 ["mailcode_bot.email 未设置"]
  - [ ] ✅ UT: `test_validate_serve_config_missing_password` — 返回 ["mailcode_bot.password 未设置"]
  - [ ] ✅ UT: `test_validate_serve_config_empty_allowed_senders` — 返回 ["security.allowed_senders 为空..."]
  - [ ] ✅ UT: `test_validate_serve_config_smtp_host_missing` — provider 不可识别时返回错误
  - [ ] ✅ UT: `test_validate_serve_config_valid` — 完整 config 返回 `[]`
  - [ ] ✅ UT: `test_validate_serve_config_load_error` — 配置文件损坏/不存在时返回 `["无法读取配置: ..."]`
  - [ ] ✅ Lint: `ruff check mailcode/config.py` 通过

### Task 4: `tests/unit/` — 改 4 个测试文件 + 新建 test_stateless_handler

- **涉及目录**: `tests/unit/`
- **涉及文件**:
  - `/Users/zs/Develop/MailCode/tests/unit/test_conversation_handler.py` — `TestCallClaude` 5 个用例 (line 62-119) 改 `handler._call_claude("...")` → `ch_module.call_claude("...")`, 加 `test_module_level_call_claude_signature` 验证新签名
  - `/Users/zs/Develop/MailCode/tests/unit/test_listener_lifecycle.py` — 加 3 个 `TestProcessEmailRouting` 用例: stateless / conversation / lazy_init
  - `/Users/zs/Develop/MailCode/tests/unit/test_config.py` — 加 2 个 `test_is_session_enabled_default_true` / `test_get_session_config_merges_user_over_default`
  - `/Users/zs/Develop/MailCode/tests/unit/test_cli.py` — `TestServe` 加 3 个 `test_cmd_serve_exits_when_config_invalid` / `test_cmd_serve_proceeds_when_config_valid` / `test_cmd_serve_validates_before_logging_setup`
  - `/Users/zs/Develop/MailCode/tests/unit/test_stateless_handler.py` (新) — 7 个 `TestStatelessHandler` 用例 (见设计文档)
- **描述**:
  - 镜像 `test_conversation_handler.py` 风格, 复用现有 fixtures
  - `test_listener_lifecycle.py` patch `mailcode.relay.email_listener.is_session_enabled` (导入站, 非 `mailcode.config.is_session_enabled`)
- **依赖**: Task 1 + Task 2 + Task 3 (需要被测代码先到位)
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_conversation_handler.py -v` 全过 (含模块级 call)
  - [ ] ✅ UT: `pytest tests/unit/test_listener_lifecycle.py -v` 全过 (含 3 个新路由测试)
  - [ ] ✅ UT: `pytest tests/unit/test_config.py -v` 全过 (含 2 个新默认测试)
  - [ ] ✅ UT: `pytest tests/unit/test_cli.py -v` 全过 (含 3 个新预检测试)
  - [ ] ✅ UT: `pytest tests/unit/test_stateless_handler.py -v` 全过 (7 个用例)
  - [ ] ✅ Lint: `ruff check tests/unit/test_conversation_handler.py tests/unit/test_listener_lifecycle.py tests/unit/test_config.py tests/unit/test_cli.py tests/unit/test_stateless_handler.py` 通过

### Task 5: `README.md` + `docs/design-final/design.md` + `mailcode/cli.py` help 文本 — 文档更新

- **涉及目录**: 项目根, `docs/design-final/`
- **涉及文件**:
  - `/Users/zs/Develop/MailCode/README.md`
    - line 78 "会话管理" 表格项描述保持 (命令本身不变)
    - line 112 措辞: "启用 `session.enabled = true` 后..." → "MailCode 默认按邮件主题维护多轮对话; 如需单次回复模式请设 `session.enabled = false`"
  - `/Users/zs/Develop/MailCode/docs/design-final/design.md`
    - §3 "对话处理": 新增子节 "3.7 Stateless Fallback"
    - §6 "配置设计": 新增子节 "6.4 启动预检"
    - §10 "用户界面": 更新 `mailcode serve --session/-S` 描述, 删除 "需在 config 中开启 session.enabled" 措辞
    - §12 "Session 管理": §12.1 设计目标加一句 "session.enabled 默认 true; 关闭时走 stateless fallback (§3.7)"
  - `/Users/zs/Develop/MailCode/mailcode/cli.py` — `mailcode session` help 文本 (line 332) 同步修
- **描述**:
  - 设计文档同步新架构
  - README 同步默认行为变更
  - CLI help 同步默认行为变更
- **依赖**: Task 1 + Task 2 + Task 3 (实现 + CLI + 默认值都到位)
- **验证标准**:
  - [ ] ✅ Manual: `docs/design-final/design.md` §3.7 描述 stateless fallback 路由表
  - [ ] ✅ Manual: `docs/design-final/design.md` §6.4 描述 `validate_serve_config` 5 类检查项
  - [ ] ✅ Manual: `docs/design-final/design.md` §10 `--session` 描述准确 (提到 force_session 覆盖)
  - [ ] ✅ Manual: `docs/design-final/design.md` §12.1 注明默认 true + stateless fallback 链接
  - [ ] ✅ Manual: `README.md` line 112 措辞反映默认多轮
  - [ ] ✅ Manual: `python3 -m mailcode session --help` help 文本不再含 "需在 config 中开启 session.enabled"

## 验证清单

完成后跑全量回归:

- [ ] 运行 `source .venv/bin/activate && python3 -m pytest tests/unit/ -q` — 全过, 无回归
- [ ] 运行 `source .venv/bin/activate && python3 -m ruff check mailcode/ tests/` — 通过
- [ ] 运行 `python3 -c "from mailcode.relay.stateless_handler import StatelessHandler"` — 导入成功
- [ ] 运行 `python3 -c "from mailcode.config import validate_serve_config; print(validate_serve_config())"` — 输出 `[]` (在配齐 config 的环境)
- [ ] 运行 `python3 -m mailcode serve --help` — help 文本含 `--session/-S`
- [ ] 运行 `python3 -m mailcode session --help` — help 文本不含 "需在 config 中开启 session.enabled"
- [ ] Manual: 删 `~/.config/mailcode/config.json`, 跑 `mailcode config init`, 验证生成文件 `session.enabled` 为 `true`
- [ ] Manual: 临时把 `mailcode_bot.email` 改空, 跑 `mailcode serve`, 验证打印 "MailCode 中继启动失败" + exit 1
- [ ] Manual: 配齐 config, 跑 `mailcode serve --once --dry-run`, 验证走通

**拆分逻辑**:
- Task 1 (relay/) 第一个做 (核心抽象)
- Task 2 (server.py + cli.py) 显式依赖 Task 1
- Task 3 (config.py + default.json) 独立, 可与 Task 1 / Task 2 **并发**
- Task 4 (tests/) 显式依赖 Task 1 + Task 2 + Task 3
- Task 5 (docs/) 显式依赖 Task 1 + Task 2 + Task 3 (实现 + CLI + 默认值都到位)
- 同批次内并发, 跨批次串行
