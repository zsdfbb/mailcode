# Stateless Fallback + 启动预检 + 默认 Session 开启 修改报告

## 变更摘要

修了 3 个互相纠缠的 bug (静默丢邮件 / `--session` 死代码 / 默认值不合理)。新增 `StatelessHandler` (一封邮件 → 一次 `claude -p` → 一封回信) 走 stateless fallback; 新增 `validate_serve_config` 启动预检 (5 类必填, 失败 fail-fast exit 1); 默认 `session.enabled` 翻 `true` (4 处同步: `_ensure_user_config` / `SESSION_DEFAULTS` / `is_session_enabled` / `default.json`)。抽 4 个模块级符号 (`call_claude` / `extract_cwd` / `strip_cwd` / `send_error_email`) 给 `ConversationHandler` 和 `StatelessHandler` 共用。修 `--session/-S` 死代码 (`server.py:32-34` 透传 `args.session or None`)。修后新装用户开箱即多轮, 关闭 session 不再静默丢而是单次回, 缺配 fail-fast 不再"假活"。

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| A | mailcode/relay/stateless_handler.py (新) | `StatelessHandler` 类, 一封邮件一次 `claude -p` 一次回信 |
| M | mailcode/relay/conversation_handler.py | 4 符号抽到模块级 (`call_claude` / `extract_cwd` / `strip_cwd` / `send_error_email`), 删原 `_xxx` 私有方法, `handle_email` 内部调用更新 |
| M | mailcode/relay/email_listener.py | `__init__` 拆 lazy init (`_conv_handler` / `_stateless_handler` 独立), `process_email` 完全重写加 `force_session` 参数, 新增 `_handle_via_conversation` / `_handle_via_stateless` 私有方法 |
| M | mailcode/server.py | line 32-34 透传 `force_session=args.session or None`, 修 `--session` 死代码 |
| M | mailcode/cli.py | `cmd_serve` 顶部加 `validate_serve_config` 预检 (失败 `sys.exit(1)`, 在 `setup_logging` 之前), `_cmd_config_validate` 改调 `validate_serve_config` 复用, `mailcode session` help 文本改 |
| M | mailcode/config.py | 4 处默认翻 `true` (line 77/193/209 + `_merge_identity` 联动), 新增 `validate_serve_config() -> list[str]` |
| M | mailcode/resources/default.json | `"session.enabled": false` → `true` |
| A | tests/unit/test_stateless_handler.py (新) | 7 个 `TestStatelessHandler` 用例覆盖 prompt 拼接 / 成功 / 失败 / SMTP 异常 / subject 前缀 |
| M | tests/unit/test_conversation_handler.py | `TestCallClaude` 5 个用例改 patch 导入站 (`ch_module.call_claude` / `ch_module.extract_cwd` 等), 加 1 个新签名验证 |
| M | tests/unit/test_listener_lifecycle.py | +3 `TestProcessEmailRouting` 用例 (stateless / conversation / lazy_init) |
| M | tests/unit/test_config.py | +2 默认 `true` 测试 (`test_is_session_enabled_default_true` / `test_get_session_config_merges_user_over_default`) |
| M | tests/unit/test_cli.py | `TestServe` +3 预检测试 (`test_cmd_serve_exits_when_config_invalid` / `test_cmd_serve_proceeds_when_config_valid` / `test_cmd_serve_validates_before_logging_setup`) |
| M | README.md | line 112 措辞: "启用 `session.enabled = true` 后..." → "MailCode 默认按邮件主题维护多轮对话; 如需单次回复模式请设 `session.enabled = false`" |
| M | docs/design-final/design.md | §3.7 Stateless Fallback (新增) / §6.4 启动预检 (新增) / §10.1 `--session` 描述更新 / §12.1 Session 默认 true 注明 |

## 测试结果

| 类型 | 命令 | 结果 |
|------|------|------|
| UT (新) | `pytest tests/unit/test_stateless_handler.py` | 7 passed |
| UT (改) | `pytest tests/unit/test_conversation_handler.py` | 81 passed |
| UT (改) | `pytest tests/unit/test_listener_lifecycle.py` | 10 passed (含 3 个新路由测试) |
| UT (改) | `pytest tests/unit/test_config.py` | 16 passed (含 2 个新默认测试) |
| UT (改) | `pytest tests/unit/test_cli.py` | 36 passed (含 3 个新预检测试) |
| 全量 | `pytest tests/unit/` | 212 passed |
| Lint | `ruff check mailcode/ tests/` | All checks passed |

## 关键决策与实现

1. **抽 4 符号到模块级**: `call_claude` / `extract_cwd` / `strip_cwd` / `send_error_email` 原本就是无 `self` 用法的纯函数 (`send_error_email` 显式收 `email_channel` 作参数), 提到模块级后 `ConversationHandler` 和 `StatelessHandler` 共用, 测试可直接调 `ch_module.call_claude(...)`。原 `_` 前缀私有方法在内部只有 `handle_email` 一处调用, **删除不留薄包装** (避免 dead method)。
2. **`process_email` 路由表**: 新增 `force_session: Optional[bool] = None` 参数, 路由优先级 `dry_run` > `force_session` > `is_session_enabled()`。`force_session=True` 走 conversation, `False` 走 stateless, `None` 走 `is_session_enabled()` 决定。返回 `(success: bool, mode: str)`, `mode ∈ {"conversation", "stateless", "dry_run"}` 方便 `server.py` 日志分类。
3. **`--session` 死代码修复**: `server.py:32-34` 改 `listener.process_email(entry, dry_run=args.dry_run, force_session=args.session or None)`, `True → True` (强制多轮), `False → None` (走 config, 不强制)。`args.session or None` 巧妙处理 `False or None == None` 的语义——`--no-session` 显式单次目前 CLI 不暴露, 但走 config 的"未开 session" 也能拿到 stateless fallback。
4. **启动预检 `validate_serve_config`**: 校验 5 类必填 (`mailcode_bot.email` / `password` / SMTP `host`/`user`/`pass` / IMAP `host`/`user`/`pass` / `security.allowed_senders` 非空), 双重 `try/except` (外层包 `load_config` 失败, 内层包校验异常防崩), **纯函数返回错误列表不 `sys.exit`**——退出决策由 `cmd_serve` / `_cmd_config_validate` 调用方做, 错误前缀文案各自定 ("MailCode 中继启动失败" vs "配置校验失败")。
5. **默认翻 `true` 同步 4 处**: `_ensure_user_config` line 77 硬编码 fallback / `SESSION_DEFAULTS` line 193 模块常量 / `is_session_enabled` line 209 `get(...).get("enabled", default)` / `default.json` line 32 资源文件。**全部同步翻**——任何路径加载配置都拿到一致默认。`_ensure_user_config` 配合 `_merge_identity` 保证 `mailcode_bot` 缺失字段也能被 provider preset 补全后才校验。
6. **测试 patch 导入站**: `is_session_enabled` / `call_claude` / `validate_serve_config` 在使用模块都用 `from ... import` 拉本地 binding, 必须 patch **导入点** (如 `mailcode.relay.email_listener.is_session_enabled`) 而非源模块 (`mailcode.config.is_session_enabled`)——本地 binding 不变, patch 不到。这是 `test_listener_lifecycle.py` 三个路由测试最关键的正确性保证。
7. **`StatelessHandler` 设计**: 复用 `extract_cwd` / `strip_cwd` 行为对齐 `ConversationHandler` (邮件首部 `cwd:` 指令照样支持), 但**不写 session 文件** (无 `_conv_dir` / `_index_file` 概念), **无 cwd 粘性** (单次邮件独立), `call_claude` 返回 `None`/空串 → `send_error_email` 兜底, SMTP 失败直接 `return False` 不抛异常。**不继承 `ConversationHandler`**——避免引入 session 生命周期的实例属性, 语义更清晰。
8. **escape hatch 保留**: 关闭 `session.enabled` 后走 stateless fallback, 而不是回到静默丢邮件的旧 P0 bug。这是"反向操作"——关掉多轮还有单次, 不会"全关就没了"。`mailcode config show` 输出可见, README 明确指示。
9. **预检早于 `setup_logging`**: `cmd_serve` 顺序是 `parse → validate → setup_logging → listener`, 预检失败时不污染 `relay.log`, 不构造 `IMAPListener`, 纯 fail-fast。测试断言 `patch("mailcode.utils.logging.setup_logging")` 不被调 + `patch("mailcode.relay.email_listener.IMAPListener")` 不被调来验证顺序。
10. **升级兼容性**: 旧 `config.json` 有 `session.enabled: false` 的用户不被自动改, 行为沿用 (`false` 仍 false, 但 Phase 1 后是单次回而非静默丢)。**新装用户** `mailcode config init` 直接拿到 `enabled: true`。**release note 必须明示**——是行为变更。

## 实施流程

按 exec-plan 5 任务串行 + 局部并发执行:

- **Phase 1 → 2 → 3 顺序不可乱**: stateless fallback 先于默认翻转, 保住 escape hatch (关 session 退单次) 这条反悔路。如果反过来, 旧默认 false 静默丢 → 翻默认 true 强加多轮 → 写 stateless 单次回 (用户已被默认锁到多轮, 失去"关掉退回单次"路径)
- **Task 1 (relay)**: 抽 4 符号 + 新建 `stateless_handler.py` + 重写 `process_email`, 第一个做
- **Task 3 (config)**: 翻 4 处默认 + 加 `validate_serve_config`, **独立**, 可与 Task 1 / Task 2 并发
- **Task 2 (server+cli)**: 修 `--session` 死代码 + `cmd_serve` 预检 + `_cmd_config_validate` 复用 + help 文本, 显式依赖 Task 1 (`process_email` 新签名)
- **Task 4 (tests)**: 改 4 测试文件 + 新建 `test_stateless_handler.py`, 依赖 Task 1+2+3
- **Task 5 (docs)**: README + design.md 4 章节 + cli.py help 文本, 依赖 Task 1+2+3
- 5 个 subagent 各负责一个 Task, 全部完成, 无中断

## 没做的事 (后续可加)

- `--session/-S` 默认值现在还是 `False` (用户必须显式传); 可考虑加 `--no-session` 显式单次 (语义对齐 "覆盖 config" 的另一种方向)
- `validate_serve_config` 当前只检查空值, 没检查格式 (email 格式 / URL 格式 / port 数值范围); 可加正则校验防低级错配
- 启动时自动调用 `_cleanup_expired_sessions` (config 字段已加 `cleanup_on_startup`, 但 `server.py` 未挂上, 留待后续, 与本次 `validate_serve_config` 入口同一处加最自然)
- error 邮件发送失败时重试 (`StatelessHandler.send_reply` 失败直接 `return False`, 没尝试重试 / 落本地日志) — 当前已写 ERROR 日志, 但邮件真发不出时用户无信号
- `StatelessHandler` prompt 模板可参数化 (目前是硬编码中文, 国际化时需抽出)
- `process_email` 返回元组 `mode` 当前只在 `server.py` 打印, 没纳入结构化日志; 可考虑入 relay.log
- 端到端测试: `tests/integration/` 跑真实 IMAP/SMTP + claude CLI 的 happy path (本次只到 UT 层, 留作后续)

## 手动验证

- [x] `source .venv/bin/activate && python3 -m pytest tests/unit/ -q` → 212 passed
- [x] `source .venv/bin/activate && python3 -m ruff check mailcode/ tests/` → All checks passed
- [x] `python3 -c "from mailcode.relay.stateless_handler import StatelessHandler; from mailcode.config import validate_serve_config"` → 导入成功
- [x] `python3 -m mailcode serve --help` → 含 `--session/-S`
- [x] `python3 -m mailcode session --help` → 不含 "需在 config 中开启 session.enabled"
- [x] `grep "默认按邮件主题" README.md line 112` → 命中
- [x] `grep "^### 3.7\|^### 6.4\|^### 10.1" docs/design-final/design.md` → 3 个章节命中
- [ ] 端到端: 真实发邮件 → 收到回信 (需要 IMAP/SMTP 配齐 + claude CLI, 留作手动)
