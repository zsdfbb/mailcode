# Stateless Fallback + 启动预检 + 默认 Session 开启 设计计划

## 背景

### 三个互相纠缠的 Bug

`mailcode serve` 当前在 `session.enabled = false`（**当前默认**）时表现为"假活"：IMAP 拉邮件 → 通过认证 → 通过白名单 → `process_email` 第一行就 `return False, "session_disabled"`，UID 被标记为已处理，**永不发回信**。服务打印 "🌐 中继已启动" 但实际啥也不干。日志里只有一行 `FAIL session_disabled`，用户没有任何可用信号。这是最严重的问题——静默丢邮件。

| # | 问题 | 影响 |
|---|---|---|
| 1 | `session.enabled=false` 静默丢邮件 | **最严重**：IMAP 接收成功，UID 标记，邮件不退回，无回信无警告 |
| 2 | `--session/-S` CLI 标志是死代码 | `mailcode/server.py:32` 调用 `listener.process_email(entry, dry_run=args.dry_run)`，**不传 `args.session`**；`IMAPListener.process_email` 也不读这个参数。文档说"覆盖 session.enabled"，实际无效 |
| 3 | `session.enabled` 默认 `false` | 新装用户开箱默认"假活"。session 是 MailCode 的核心价值（多轮对话），不该默认关闭 |

### 为什么需要这次变更

1. **静默丢邮件是 P0 阻塞 bug**——任何用户开箱即中招, 无人知道邮件被吃了
2. **`--session` 死代码误导用户**——CLI 提示说能用, 实际无效, 调试方向全错
3. **默认值反产品定位**——MailCode 是"邮件 ↔ Claude Code"连接器, 多轮对话是核心特性, 默认 false 等于把最有价值的功能埋了

### 预期效果

- 新装用户: `mailcode config init` 后 `session.enabled = true`, 开箱即用多轮对话
- 想用单次回复的进阶用户: 显式设 `session.enabled = false`, 触发 stateless fallback, 仍是"一封邮件 → 一次 claude -p → 一封回信", **不是静默丢**
- CLI 调试: `mailcode serve --once --session` 真的能临时开启多轮, 跑完即退
- 启动期: 配置缺失 (`mailcode_bot.email` 为空, 白名单为空等) 立即 fail-fast 打印, exit 1, 不再"假活"

---

## 设计

### 三个变更（独立可组合）

#### 变更 1: Stateless Fallback

`process_email` 在 `session.enabled = false` 时不再 `return False, "session_disabled"`, 而是走新路径: 调 `StatelessHandler.handle_email`（一封邮件 → 一次 `claude -p` → 一封回信）。

```
process_email 路由表:

  dry_run=True       → DRY RUN 日志, return (True, "Dry-run...")
  force_session=True → _handle_via_conversation (用 ConvHandler, 显式多轮)
  force_session=False→ _handle_via_stateless  (用 StatelessHandler, 显式单次)
  force_session=None + is_session_enabled()=True  → _handle_via_conversation
  force_session=None + is_session_enabled()=False → _handle_via_stateless
```

返回元组: `(success: bool, mode: str)`, `mode` ∈ `{"conversation", "stateless", "dry_run"}`, 方便调用方日志分类。

#### 变更 2: 启动预检 `validate_serve_config`

`mailcode/config.py` 新增 `validate_serve_config() -> list[str]`, `mailcode/cli.py:cmd_serve` 在 `setup_logging` 之前调, 失败时 print 错误列表 + `sys.exit(1)`。

校验项:
- `mailcode_bot.email` / `password` 非空
- SMTP/IMAP `host` / `user` / `pass` 非空 (依赖 `_merge_identity` 自动补全)
- `security.allowed_senders` 非空列表

复用: `_cmd_config_validate` (cli.py:189-228) 删本地 SMTP/IMAP/bot 重复检查, 改调 `validate_serve_config()`, 行为对齐。

#### 变更 3: 翻 `session.enabled` 默认为 `true`

四处默认值翻转, 行为变更:

| 文件 | 行 | 改动 |
|---|---|---|
| `/Users/zs/Develop/MailCode/mailcode/config.py` | 77 | `"enabled": False` → `"enabled": True` (`_ensure_user_config` 硬编码 fallback)|
| `/Users/zs/Develop/MailCode/mailcode/config.py` | 193 | `"enabled": False` → `"enabled": True` (`SESSION_DEFAULTS` 模块常量)|
| `/Users/zs/Develop/MailCode/mailcode/config.py` | 209 | `get(...).get("enabled", False)` → `True` (`is_session_enabled` 默认值)|
| `/Users/zs/Develop/MailCode/mailcode/resources/default.json` | 32 | `"enabled": false` → `"enabled": true` |

**升级兼容性**: 有 `config.json` 但 `session.enabled` 未显式写 → 不被自动改, 行为沿用 (`false` 仍 false, 但 Phase 1 后是单次回复而非静默丢)。**新装用户** → 直接拿到多轮。

### 关键架构决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 抽 4 个 `_` 前缀方法到模块级 | `call_claude` / `extract_cwd` / `strip_cwd` / `send_error_email` 都提到 `mailcode/relay/conversation_handler.py` 模块级 | 4 个方法**已经是纯函数**（无 `self` 用法, `send_error_email` 收 `email_channel` 作参数）。`StatelessHandler` 不应继承 `ConversationHandler`（语义不同: session vs 单次）。提到模块级后可复用, 测试可直接调 `ch_module.call_claude()` |
| 是否留薄包装方法 | **不留** | 原 `_` 前缀方法在内部只有 `handle_email` 一处调用, 提模块级后改 `handle_email` 直接调模块函数, 不留 dead method |
| `StatelessHandler` 是否继承 `ConversationHandler` | **不继承** | 两者职责不同: `ConversationHandler` 管 session 文件生命周期; `StatelessHandler` 无状态。继承会引入不必要的实例属性 (`_conv_dir`, `_index_file`) |
| `process_email` 路由参数 | 新增 `force_session: Optional[bool] = None` | 不破坏现有调用方, `None` = 走 `is_session_enabled()` 默认逻辑; `True/False` = 显式覆盖 (用于 `--session` 标志) |
| `process_email` 返回元组 | `(success: bool, mode: str)` | 现有 `server.py:32` 打印 `message` 字段, 加 `mode` 便于区分 conversation / stateless / dry_run, 日志更清晰 |
| handler lazy init | `IMAPListener.__init__` 把 `self.conv_handler = None` 拆为 `self._conv_handler = None; self._stateless_handler = None`, 两个独立 lazy init | 不耦合, 测试可单独 mock 任一 |
| `validate_serve_config` 返回 | `list[str]` (错误消息列表) | 调用方控制输出格式 (cmd_serve 走"启动失败"前缀, _cmd_config_validate 走"配置校验失败"前缀), 复用不绑死 |
| `validate_serve_config` 失败时 `sys.exit(1)` | **不在该函数内** | 函数只返回错误列表, exit 决策由调用方做 (cmd_serve / _cmd_config_validate 各管各的退出码文案) |
| 翻默认后旧用户 `config.json` | **不改** | 行为保持: 旧 config 是 `enabled: false` → 走 stateless (Phase 1 保证不再静默丢)。新装用户 `mailcode config init` 生成 `enabled: true` |
| `--session` help 文本 | "按邮件主题维护多轮对话 session (覆盖 config 中 session.enabled)" | 已有, 只需验证不再误导 |
| `mailcode session` help 文本 | 删 "需在 config 中开启 session.enabled" | 翻默认后该限制不再存在, 措辞改为纯描述 |

### Prompt 模板 (`StatelessHandler`)

```python
prompt = (
    f"用户最新邮件:\n\n"
    f"主题: {subject}\n\n"
    f"{clean_body}\n\n"
    f"请直接回复这封邮件, 内容将作为邮件正文发送, 用纯文本格式。"
)
```

比 `ConversationHandler` 的 prompt 更直接——无 session 文件可读, 直接给邮件正文。`cwd` 仍走 `extract_cwd` / `strip_cwd` 解析 (复用变更 1 抽出的模块级函数)。

### 数据流

```
1. IMAPListener.fetch_unread_emails 返回 email_entry
2. fetch_unread_emails 已完成: 反自循环 / 去重 / SPF/DKIM / 白名单 / 清理 body
3. process_email(entry, dry_run, force_session):
   ├─ dry_run=True                       → return (True, "dry_run")
   ├─ force_session 决定路由:
   │   ├─ None + is_session_enabled()    → _handle_via_conversation
   │   └─ None + not is_session_enabled()→ _handle_via_stateless
   │   └─ True/False                      → 对应 handler
   ├─ 调用对应 handler.handle_email
   └─ return (success, "conversation" | "stateless")
4. server.py 打印日志: "✅ [token] conversation" / "✅ [token] stateless" / "❌ [token] stateless_failed"
```

### 关键不变量

- **handler 是无状态的 (`StatelessHandler`)**: 不写文件, 不读 session index, 每次调用是独立 `claude -p` 调用
- **cwd 解析在 stateless 和 conversation 路径完全一致**: 都用模块级 `extract_cwd` / `strip_cwd`
- **错误邮件路径一致**: `call_claude` 返回 `None` / 空字符串 → `send_error_email`, 两个 handler 共用
- **`force_session` 是用户级显式覆盖**, 不持久化到 config
- **预检只检查"必填缺失"**, 不检查 SMTP/IMAP 实际连通性 (那是 `mailcode health` 的事)

---

## 涉及文件

### 新增

- `/Users/zs/Develop/MailCode/mailcode/relay/stateless_handler.py` — `StatelessHandler` 类 (变更 1)
- `/Users/zs/Develop/MailCode/tests/unit/test_stateless_handler.py` — StatelessHandler 单元测试 (变更 1)

### 修改

- `/Users/zs/Develop/MailCode/mailcode/relay/conversation_handler.py`
  - line 225 `_extract_cwd` → 模块级 `extract_cwd` (变更 1)
  - line 248 `_strip_cwd` → 模块级 `strip_cwd` (变更 1)
  - line 272 `_call_claude` → 模块级 `call_claude` (变更 1)
  - line 303 `_send_error_email` → 模块级 `send_error_email` (参数显式收 `email_channel`) (变更 1)
  - `handle_email` 内部 4 处调用更新为模块级调用
- `/Users/zs/Develop/MailCode/mailcode/relay/email_listener.py`
  - line 56 附近 `self.conv_handler = None` → 拆为 `self._conv_handler = None; self._stateless_handler = None` (变更 1)
  - line 351-372 `process_email` 完全重写, 加 `force_session` 参数, 路由到 `_handle_via_conversation` / `_handle_via_stateless` (变更 1)
  - 新增私有方法 `_handle_via_conversation` / `_handle_via_stateless` 封装 lazy init + 返回元组翻译 (变更 1)
- `/Users/zs/Develop/MailCode/mailcode/server.py`
  - line 32 `listener.process_email(entry, dry_run=args.dry_run)` → `listener.process_email(entry, dry_run=args.dry_run, force_session=args.session or None)` (修 `--session` 死代码, 变更 1)
- `/Users/zs/Develop/MailCode/mailcode/cli.py`
  - `cmd_serve` (line 24-34) 顶部加 `validate_serve_config` 预检, 失败 print + `sys.exit(1)` (变更 2)
  - `_cmd_config_validate` (line 189-228) 改用 `validate_serve_config`, 删本地 SMTP/IMAP/bot 重复检查 (变更 2)
  - `--session` help 文本 (line 290-291) 保持, 验证不再误导 (变更 1)
  - `mailcode session` help 文本 (line 332) 删 "需在 config 中开启 session.enabled" 措辞 (变更 3)
- `/Users/zs/Develop/MailCode/mailcode/config.py`
  - line 77 `_ensure_user_config` 内 `"enabled": False` → `True` (变更 3)
  - line 193 `SESSION_DEFAULTS` 内 `"enabled": False` → `True` (变更 3)
  - line 209 `is_session_enabled` 默认 `False` → `True` (变更 3)
  - 新增 `validate_serve_config() -> list[str]` 函数 (变更 2)
- `/Users/zs/Develop/MailCode/mailcode/resources/default.json`
  - line 32 `"enabled": false` → `"enabled": true` (变更 3)
- `/Users/zs/Develop/MailCode/README.md`
  - line 78 "会话管理" 表格项描述保持
  - line 112 "启用 `session.enabled = true` 后..." 改写为 "MailCode 默认按邮件主题维护多轮对话; 如需单次回复模式请设 `session.enabled = false`" (变更 3)
- `/Users/zs/Develop/MailCode/tests/unit/test_conversation_handler.py`
  - line 62-119 `TestCallClaude` 5 个用例: `handler._call_claude("...")` → `ch_module.call_claude("...")` (变更 1)
- `/Users/zs/Develop/MailCode/tests/unit/test_listener_lifecycle.py`
  - 加 3 个 `process_email` 路由测试: stateless / conversation / lazy init (变更 1)
- `/Users/zs/Develop/MailCode/tests/unit/test_config.py`
  - 加 2 个 case: `test_is_session_enabled_default_true` / `test_get_session_config_merges_user_over_default` (变更 3)
- `/Users/zs/Develop/MailCode/tests/unit/test_cli.py`
  - `TestServe` 加 3 个 case: 预检失败 exit 1 / 预检通过进入 serve / 预检在 setup_logging 之前 (变更 2)
- `/Users/zs/Develop/MailCode/docs/design-final/design.md`
  - §3 "对话处理" 章节加 stateless fallback 子节
  - §6 "配置设计" 章节加启动预检小节
  - §10 "用户界面" 章节更新 `--session` 描述
  - §12 "Session 管理" 章节注明 "session.enabled 默认 true, 关闭走 stateless fallback"

### 删除

- 无 (4 个原 `_` 前缀方法在 conversation_handler.py 内删除, 不留薄包装)

---

## 测试策略

### 测试范围

- **单元测试** (主要): 5 个测试文件改动
  - `test_conversation_handler.py`: 5 个 `TestCallClaude` 用例适配模块级调用
  - `test_listener_lifecycle.py`: 加 3 个 `process_email` 路由测试
  - `test_stateless_handler.py` (新): 7 个 `StatelessHandler` 用例
  - `test_config.py`: 加 2 个默认 true 用例
  - `test_cli.py`: `TestServe` 加 3 个启动预检用例
- **手动验证** (必要): `mailcode serve --once --dry-run` 走通, 缺配置时 `mailcode serve` 失败打印

### 验收标准 (可自动化)

变更 1 (stateless fallback):
- [ ] `from mailcode.relay.stateless_handler import StatelessHandler` 导入成功
- [ ] `ch_module.call_claude("...")` 直接调模块函数可工作 (测试改用)
- [ ] `ch_module.extract_cwd("cwd: /tmp")` / `strip_cwd` 行为不变
- [ ] `ch_module.send_error_email(channel, ...)` 行为不变
- [ ] `IMAPListener.process_email` 在 `is_session_enabled()=False` 时返回 `("stateless", bool)`
- [ ] `IMAPListener.process_email` 在 `is_session_enabled()=True` 时返回 `("conversation", bool)`
- [ ] `IMAPListener.process_email(force_session=True)` 强制走 conversation
- [ ] `IMAPListener.process_email(force_session=False)` 强制走 stateless
- [ ] 二次调用复用同一 handler 实例 (lazy init 幂等)
- [ ] `StatelessHandler.handle_email` 成功路径: 调 call_claude, 发 reply, return True
- [ ] `StatelessHandler.handle_email` 调失败: 发错误邮件, return False
- [ ] `StatelessHandler.handle_email` 空 response: 发错误邮件, return False
- [ ] `StatelessHandler.handle_email` SMTP 失败: return False, 不抛异常
- [ ] `StatelessHandler.handle_email` subject 已是 "Re: x" 不再加
- [ ] `StatelessHandler.handle_email` subject 无前缀自动加 "Re: "

变更 2 (启动预检):
- [ ] `mailcode.config.validate_serve_config()` 返回错误消息列表
- [ ] `mailcode serve` 缺 `mailcode_bot.email` → 打印 "MailCode 中继启动失败" + 错误, exit 1
- [ ] `mailcode serve` `allowed_senders` 为空 → 打印 + exit 1
- [ ] `mailcode config validate` 与 `serve` 同样配置时同样失败
- [ ] 预检失败时 `setup_logging` 未被调 (不写 relay.log)
- [ ] 预检失败时 `IMAPListener` 未构造

变更 3 (翻默认):
- [ ] 删 `~/.config/mailcode/config.json`, 跑 `mailcode config init` 生成文件 `session.enabled` 为 `true`
- [ ] 旧 config (无 session 段) 加载后 `is_session_enabled()` 返回 `True`
- [ ] 用户显式 `session.enabled: false` 覆盖默认, 返回 `False`

跨变更:
- [ ] 现有 200+ 单元测试无回归
- [ ] `ruff check mailcode/ tests/` 全过

### 验收标准 (需人工验证)

- [ ] `mailcode serve --once --dry-run` 走通, 打印 DRY RUN 路径
- [ ] `mailcode serve --once --dry-run --session` 走通, 打印 DRY RUN (--session 不影响 dry_run)
- [ ] 删 `mailcode_bot.email`, 跑 `mailcode serve` → 打印启动失败 + email 未设置, exit 1
- [ ] 配齐配置, 跑 `mailcode serve --once` → 正常启动并打印 "中继已启动"

### 关键 patch 点

`is_session_enabled` 在 `email_listener.py:15` 是 `from mailcode.config import ... is_session_enabled`, 所以测试必须 patch **`mailcode.relay.email_listener.is_session_enabled`** (导入站), patch `mailcode.config.is_session_enabled` 无效 (本地 binding 不变)。

---

## 波及文档

需要更新 `/Users/zs/Develop/MailCode/docs/design-final/design.md` 的以下章节:

- **§3 对话处理 (Conversation Processing)**: 新增子节 "3.7 Stateless Fallback", 描述 `session.enabled=false` 时 `process_email` 走 `StatelessHandler` 而非静默丢
- **§6 配置设计 (Configuration Design)**: 新增子节 "6.4 启动预检", 描述 `validate_serve_config` 检查项和 fail-fast 行为
- **§10 用户界面 (User Interface)**: 更新 `mailcode serve --session/-S` 描述, 删除"需在 config 中开启 session.enabled"误导性表述
- **§12 Session 管理**: 在 §12.1 设计目标加一句"session.enabled 默认 true; 关闭时走 stateless fallback (§3.7) 而非静默丢"

需要更新 `/Users/zs/Develop/MailCode/README.md`:
- line 78 "会话管理" 描述保持 (命令本身不变)
- line 112 措辞: "启用 `session.enabled = true` 后..." → "MailCode 默认按邮件主题维护多轮对话; 如需单次回复模式请设 `session.enabled = false`"

---

## 风险与注意事项

| 风险 | 概率 | 缓解 |
|---|---|---|
| 抽模块级破坏现有 conversation 测试 | 中 | 5 个 `TestCallClaude` 用例同步改; 跑 `pytest tests/unit/test_conversation_handler.py` 验证 |
| 翻默认后用户投诉"我想要单次" | 低 | README/release note 明确指示 `session.enabled = false`; `mailcode config show` 输出可见 |
| 预检规则太严屏蔽合法配置 | 中 | 发版前用 `mailcode config validate` 走查多种真实配置; 新装用户跑 `mailcode config init` 自动起, 不会预检失败 |
| `force_session` 与 `--session` 行为不符 | 极低 | Phase 1.4 一并修; 测试覆盖 4 种组合 |
| `validate_serve_config` 与 `_cmd_config_validate` 行为分裂 | 低 | 子任务统一调 `validate_serve_config`; 测试断言两者在同一坏配置下退出码和错误消息一致 |
| IMAPListener `__init__` 改动影响现有测试 | 中 | `test_listener_lifecycle.py` 现有 6 个用例不依赖 `conv_handler`/`stateless_handler` 实例属性, 不需改; 跑全量验证 |
| StatelessHandler 调 `call_claude` 失败时静默 | 极低 | `call_claude` 返回 `None` → `send_error_email` 兜底, 测试覆盖 |
| 升级用户 `config.json` 中 `session.enabled: false` 仍存在 | 低 | 行为保持: 走 stateless fallback (Phase 1 已修), 不再静默丢; README 指示用户手动改 `true` 启用多轮 |
| `_ensure_user_config` 硬编码 `enabled: True` 与 `SESSION_DEFAULTS` 不同步 | 中 | 4 处默认值同时翻转, 测试 `test_is_session_enabled_default_true` 覆盖两个路径; code review 检查 |

### 实施顺序 (关键)

**严格按 Phase 1 → 2 → 3 执行**:

- Phase 1 完成后, `session.enabled=false` 的现有用户从"静默丢"变为"单次回"——**纯 bug 修复, 无人更差**
- Phase 2 翻默认是行为变更, 需 release note
- Phase 3 启动预检独立, 可与前两 Phase 任意组合

**如果反过来** (先翻默认再写 stateless): 旧默认 false → 静默丢; 翻默认 true → 强加多轮; 写 stateless → 单次回 (但用户已被默认锁到多轮)。用户失去"关掉 session 退回单次"这条反悔路。
