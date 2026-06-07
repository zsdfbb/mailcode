# MailCode Email-Agent Bridge 设计文档

> 目标：通过邮件远程操控 Claude Code AI 助手。用户发送邮件，MailCode 调用 `claude -p` 处理，回复自动发回用户邮箱。

---

## 1. 核心架构概览

```
┌──────────────┐  SMTP 回复        ┌──────────────┐
│  Claude Code  │ ────────────────> │  用户邮箱     │
│  (claude -p)   │ <──────────────── │              │
└──────────────┘  IMAP 新邮件      └──────────────┘
       ▲                                   │
       │ subprocess.run                     │ 新邮件/回复
       │ ["claude", "-p", prompt]           │
       │                                   ▼
       │                          ┌──────────────┐
       │                          │  IMAP 监听器  │
       └──────────────────────────│ + Conversation│
              stdout = 回复       │   Handler    │
                                  └──────────────┘
                                        │
                                        │ threads.json（已废弃，见 §12）
                                        ▼
                                  ┌──────────────┐
                                  │  对话历史     │
                                  │ (每个线程)    │
                                  └──────────────┘
```

### 工作流程

**对话模式（已废弃，见 §12 Session 管理）**：

| 步骤 | 说明 |
|------|------|
| 1 | 用户发送（或回复）邮件到 bot 邮箱 |
| 2 | IMAP 监听器检测到新邮件，进行安全检查（DKIM/SPF + 发件人白名单） |
| 3 | ConversationHandler 从 `threads.json`（已废弃）读取该线程的对话历史 |
| 4 | 拼接完整 prompt = 系统提示 + 对话历史 + 新邮件正文（已废弃，详见 §12.5） |
| 5 | 调用 `subprocess.run(["claude", "-p", prompt, "--dangerously-skip-permissions"], cwd=project_dir)`（`project_dir` 已废弃，详见 §12.4） |
| 6 | Claude Code 处理请求（可读写文件、执行命令），输出回复到 stdout |
| 7 | stdout = 干净回复文本，直接通过 SMTP 发送回复邮件 |
| 8 | 对话历史（含本次往来）保存到 `threads.json`（已废弃） |

---

## 2. 邮件通道设计

### 2.1 邮件回复发送（SMTP）

**技术栈**：Python `smtplib` + `email.mime`

**核心流程**：
1. ConversationHandler 调用 `_call_claude()` 得到 Claude Code 的 stdout 回复
2. 构造纯文本邮件（`Content-Type: text/plain`），主题保持原邮件线程
3. 通过 SMTP 发送，使用 In-Reply-To / References 头部关联邮件线程
4. 添加自定义头部 `X-MailCode-Conversation: true` 用于反自循环检测

**关键代码结构**：
```
mailcode/
└── channels/
    └── email_channel.py  # EmailChannel 类（SMTP 发送）
```

### 2.2 邮件回复接收（IMAP）

**技术栈**：Python `imaplib` + `email` 标准库

**核心流程**：
1. 连接 IMAP，每 30 秒轮询新邮件（或使用 IDLE 长连接）
2. 下载未读邮件，解析 MIME 内容
3. **反自循环**：检查 `state.json` 的 `sent_messages` 键，跳过系统发送的邮件
4. **去重**：基于邮件 UID / Message-ID 跳过已处理邮件
5. **白名单**：仅处理 `ALLOWED_SENDERS` 中列出的发件人
6. **域名认证**：解析 `Authentication-Results` 头部，验证 `dkim=pass` + `spf=pass`（默认警告模式，仅记录不拒绝；严格模式拒绝认证失败的邮件）
7. **提取 Token**：从主题 `[Remote #TOKEN]` 中正则提取（同时兼容旧格式 `[OpenCode-Remote #TOKEN]`）
8. **清理正文**：移除引用内容（`> ` 前缀、`----Original Message----` 块、签名等）
9. **安全验证**：域名认证（DKIM/SPF）+ 发件人白名单
10. 将邮件交给 ConversationHandler 处理

**关键代码结构**：
```
mailcode/
├── relay/
│   ├── email_listener.py      # IMAPListener 类（邮件轮询 + 正文清理 + 域名认证）
│   ├── security.py            # 域名认证（auth-results + 发件人白名单）
│   └── conversation_handler.py # ConversationHandler（claude -p 调用 + 对话历史管理）
```

**守护进程生命周期**：`mailcode serve` 作为长期运行的守护进程运行，可与 `launchd`（macOS）或 `systemd`（Linux）配合实现开机自启和崩溃后自动重启。关闭时：
- 断开 IMAP 连接（`logout`）
- 将已处理的 UID 集合持久化到 `state.json` 的 `processed_uids` 键
- 清理 `state.json` 中 `sent_messages` 超过 7 天的记录

---

## 3. 对话处理（Conversation Processing）

### 3.1 概述

对话模式（Conversation Mode）是 MailCode 的核心工作模式。用户发送邮件，MailCode 调用 `claude -p` 处理，回复自动发回用户邮箱，并通过邮件线程保持多轮对话。

与旧式的"命令注入 + tmux 会话"模式不同，当前架构完全基于 `subprocess.run(["claude", "-p", prompt])` 的即时调用：每封邮件独立触发一次 Claude Code 调用，无需持久化的 tmux 会话。

### 3.2 ConversationHandler

**核心模块**：`mailcode/relay/conversation_handler.py`

```
ConversationHandler 类（旧版 API，已废弃，见 §12 新架构）：
├── _ensure_dirs()                     # 确保数据目录存在
├── _read_threads() / _write_threads() # threads.json 读写（已废弃）
├── _get_system_prompt()               # 系统提示词（配置项，已废弃）
├── _find_thread_by_msg_id()           # 按 Message-ID / References 查找线程（已废弃）
├── _load_thread()                     # 加载线程历史（已废弃）
├── _build_prompt(history, body)       # 拼接：系统提示 + 历史 + 新消息（已废弃）
├── _call_claude(prompt, cwd)          # subprocess.run(["claude", "-p", prompt])
├── _save_thread()                     # 保存对话历史（含本次往来，已废弃）
├── handle_email(...)                  # 主入口：编排以上全部步骤
├── list_conversations()               # 列出活跃对话（已废弃 → list_sessions）
├── get_conversation_status(token)     # 查询对话状态（已废弃）
└── terminate_conversation(token)      # 终止对话（删除线程，已废弃 → delete_session）
```

**handle_email 流程（旧版，已废弃，见 §12.3 新版流程）**：

| 步骤 | 说明 |
|------|------|
| 1 | 解析 `References` / `In-Reply-To` 头部确定邮件线程 |
| 2 | 从 `threads.json`（已废弃）读取该线程的对话历史 |
| 3 | 拼接完整 prompt = 系统提示 + 对话历史 + 新邮件正文（已废弃，见 §12.5） |
| 4 | 调用 `subprocess.run(["claude", "-p", prompt, "--dangerously-skip-permissions"], cwd=project_dir)`（`project_dir` 已废弃，见 §12.4） |
| 5 | Claude Code 处理请求（可读写文件、执行命令），输出回复到 stdout |
| 6 | stdout 作为回复文本，通过 SMTP 发送回复邮件 |
| 7 | 对话历史（含本次往来）保存到 `threads.json`（已废弃） |

### 3.3 _call_claude 实现细节

```python
def _call_claude(self, prompt: str, cwd: str = "") -> Optional[str]:
    # 旧版 project_dir 逻辑, 已废弃; 新版走 session.cwd 粘性机制
    project_dir = cwd or self.project_dir or os.getcwd()
    result = subprocess.run(
        ["claude", "-p", prompt, "--dangerously-skip-permissions"],
        capture_output=True, text=True, timeout=300,
        cwd=project_dir,
    )
    return result.stdout.strip()
```

- **超时**：300 秒（5 分钟），可通过 `response_timeout_seconds` 配置
- **权限跳过**：`--dangerously-skip-permissions` 使 Claude Code 无需用户确认即可执行文件读写
- **工作目录（旧版，已废弃）**：邮件线程首次创建时确定的 `project_dir`；无指定时使用当前目录。新版走 session.cwd 粘性机制（§12.4）

### 3.4 线程追踪

邮件线程通过标准邮件头追踪：

- **Message-ID**：每封邮件唯一的 ID（`<uuid@host>`）
- **In-Reply-To**：回复时引用父邮件 ID
- **References**：整个线程的消息 ID 链

`_find_thread_by_msg_id()` 遍历 `threads.json`（已废弃）, 匹配任意线程中任意消息的 Message-ID 或 References 链中的 ID, 实现跨多轮的线程关联。新版改用 `index.json` msg_id 路由（详见 §12.2）。

### 3.5 系统配置

```json
{
  "conversation": {
    "enabled": true,
    "response_timeout_seconds": 300,
    "idle_timeout_hours": 24,
    "system_prompt": "你正在通过电子邮件与用户交流。请用自然语言、友好、完整地回复。回复内容直接就是邮件正文。"
  }
}
```

通过 `mailcode serve --no-conversation` 可关闭对话模式。

### 3.6 使用场景

用户向 bot 邮箱发邮件 → Claude Code 自动回复 → 用户回复邮件（保持在邮件线程中）→ Claude Code 继续对话。整个过程完全通过邮件完成，无需终端访问。

### 3.7 Stateless Fallback

`session.enabled = false` 时的回退路径——`IMAPListener.process_email` 不再 `return False, "session_disabled"` 静默丢邮件，而是走 `StatelessHandler` 完成"一封邮件 → 一次 `claude -p` → 一封回信"。

**核心模块**：`mailcode/relay/stateless_handler.py`

**设计意图**：
- **escape hatch**：给"想要单次回复、不要 session 持久化"的进阶用户一条出路，且**不丢邮件**
- **静默丢 bug 修复**：旧版 `session.enabled=false` 表现为"假活"（UID 标记, 无回信, 无警告）——本节是 P0 修复
- **职责单一**：`StatelessHandler` 不继承 `ConversationHandler`（无 session 文件生命周期, 无 `_conv_dir` / `_index_file` 实例属性）。cwd 解析、`call_claude`、`send_error_email` 三个共用原语提到 `conversation_handler.py` 模块级（`extract_cwd` / `strip_cwd` / `call_claude` / `send_error_email`），两个 handler 都直接调模块函数

**`process_email` 路由表**：

| `dry_run` | `force_session` | `is_session_enabled()` | 路径 | 返回 `mode` |
|-----------|-----------------|------------------------|------|-------------|
| `True` | — | — | 打印 DRY RUN 日志 | `"dry_run"` |
| `False` | `True` | — | `_handle_via_conversation` | `"conversation"` |
| `False` | `False` | — | `_handle_via_stateless` | `"stateless"` |
| `False` | `None` | `True` | `_handle_via_conversation` | `"conversation"` |
| `False` | `None` | `False` | `_handle_via_stateless` | `"stateless"` |

`force_session` 由 CLI 标志 `mailcode serve --session/-S` 驱动（`server.py:33` 把 `args.session` 转为 `force_session=args.session or None`）。`None` = 走 `is_session_enabled()` 默认逻辑，`True/False` = 显式覆盖（用于 CLI 调试 / 临时切换模式）。

**与 `ConversationHandler` 对比**：

| 维度 | `ConversationHandler` | `StatelessHandler` |
|------|----------------------|--------------------|
| session 文件 | 写 `session_<uuid>.json` | 不写 |
| `index.json` | 读 + 写 | 不读不写 |
| `cwd` 粘性 | session.cwd 沿用 | 每次邮件独立 `extract_cwd`，无粘性 |
| `claude -p` 调用 | 每次独立 | 每次独立 |
| 错误处理 | `call_claude` 失败 / 空 response → `send_error_email` | 同上（共用模块级函数）|
| 返回值 | `True` / `False` | `True` / `False` |

**StatelessHandler 流程**（`handle_email`）：

```
1. 提取 cwd: extract_cwd(body) → Path 或 None
2. 剥离 cwd: strip_cwd(body) → 干净正文
3. 构建 prompt: "用户最新邮件:\n\n主题: {subject}\n\n{clean_body}\n\n请直接回复这封邮件, ..."
4. 调 claude: call_claude(prompt, cwd=extracted_cwd or Path.home())
5. 错误兜底: call_claude 返回 None / 空字符串 → send_error_email(channel, ...)
6. SMTP 发回复: subject 已是 "Re: x" 不再加, 否则加 "Re: "
7. 返回 True; SMTP 异常 / send_reply 返回 False → 返回 False
```

**关键不变量**：
- `StatelessHandler` 是无状态的：不写文件, 不读 session index, 每次调用独立
- cwd 解析在 stateless 和 conversation 路径完全一致（都调模块级 `extract_cwd` / `strip_cwd`）
- 错误邮件路径一致：`call_claude` 失败 → `send_error_email` 兜底，两个 handler 共用
- `force_session` 是用户级显式覆盖，不持久化到 config
- handler 是 lazy init：`_conv_handler` / `_stateless_handler` 首次使用时构造，二次调用复用同一实例（测试断言 `id()` 一致）

---

## 4. 安全管理

### 4.1 安全策略

| 策略 | 说明 |
|------|------|
| 发件人白名单 | 仅处理 `ALLOWED_SENDERS` 中列出的地址发来的邮件 |
| 域名认证 | 解析 `Authentication-Results` 头部，验证 `dkim=pass` + `spf=pass`；默认警告模式仅记录不拒绝，严格模式拒绝认证失败邮件；temperror/permerror 始终放行 |
| 去重机制 | 基于邮件 UID + Message-ID 的双重去重 |
| 定期清理 | `state.json` 中 `sent_messages` 保留最近 7 天 |

### 4.2 Authentication-Results 域名认证

解析邮件服务商（Gmail/QQ/Outlook/126）自动添加的 `Authentication-Results` 头部，验证 `dkim=pass` + `spf=pass`，与 `allowed_senders` 白名单形成多层防御。

| 攻击方式 | 仅白名单 | 增加 Auth-Results |
|---------|---------|------------------|
| 伪造 `From: admin@your-company.com` | ❌ 字符串包含匹配可能绕过 | ✅ 伪造域名的 DKIM/SPF 必然会失败 |
| 伪造 `From: your@email.com`（冒充你） | ⚠️ 白名单包含自己地址时可能通过 | ✅ DKIM/SPF 不匹配会拦截 |
| 同域名恶意注册（`attacker@gmail.com`） | ⚠️ 精确白名单可预防 | ⚠️ 同域名 DKIM 会通过，需白名单辅助 |

**三级策略**：

| 策略 | 行为 | 场景 |
|------|------|------|
| `warn`（默认） | 认证失败仅记录日志，不拒绝 | 向后兼容，对用户透明 |
| `strict` | 认证失败（fail/softfail/none/neutral/missing）跳过该邮件 | 安全优先，需要邮件服务商支持 Auth-Results |
| `off` | 完全跳过此检查 | 自建邮箱或无 Authentication-Results 头部时 |

**结果值处理**：

| 值 | strict | warn | off |
|-------|--------|------|-----|
| `pass` | ✅ | ✅ | — |
| `fail` / `softfail` / `none` / `neutral` | ❌ 拒绝 | ⚠️ 放行 + 记录 | — |
| `temperror` / `permerror` | ✅ 放行 | ✅ 放行 | — |
| 无头部 / 空值 | ❌ 拒绝 | ✅ 放行 | — |

**服务商头部格式参考**：

```
# Gmail（折叠头部）
Authentication-Results: mx.google.com;
       dkim=pass header.i=@gmail.com header.s=20230601 header.b=xxx;
       spf=pass (google.com: domain of user@gmail.com designates 1.2.3.4 ...);
       dmarc=pass (p=NONE) header.from=gmail.com

# QQMail
Authentication-Results: mx.qq.com; dkim=pass header.i=@qq.com; spf=pass

# Outlook
Authentication-Results: spf=pass smtp.mailfrom=outlook.com; dkim=pass; dmarc=pass
```

**处理流程**：
1. 提取 `Authentication-Results` 头部值（多个时取最后一个）
2. 合并折叠头部（`\n[ \t]+` → 空格）
3. 正则提取 `dkim=` 和 `spf=` 的结果值
4. 根据策略判断放行/拒绝
5. 验证插入点：在 `_is_already_sent()` 之后、`is_sender_allowed()` 之前

---

## 5. 对话管理（Conversation Management）

### 5.1 数据结构：threads.json（**已废弃**，详见 §12）

旧版对话历史存储在 `~/.config/mailcode/threads/threads.json`（已废弃）, 按邮件线程组织。新版改用 per-session 文件 + `index.json` 索引（§12.2）。

```json
{
  "threads": [
    {
      "id": "thread-uuid",
      "from_email": "user@example.com",
      "subject": "帮我重构 user.py",
      "created_at": "2026-06-01T10:30:00Z",
      "last_active": "2026-06-01T10:35:00Z",
      "token": "A3B7KM9Q",
      "project_dir": "/path/to/project（已废弃，详见 §12.4）",
      "messages": [
        {
            "msg_id": "<msg1@example.com>",
            "role": "user",
            "content": "请帮我重构 mailcode/utils/helper.py",
            "timestamp": "2026-06-01T10:30:00Z"
        },
        {
            "msg_id": "<msg2@example.com>",
            "role": "assistant",
            "content": "已重构完成，主要改动：...",
            "timestamp": "2026-06-01T10:35:00Z"
        }
      ]
    }
  ]
}
```

**已发送消息**（`state.json` 中的 `sent_messages` 字段, 已废弃）：
```json
{
  "messages": [
    {
      "message_id": "<abc123@mail.example.com>",
      "thread_id": "thread-uuid",
      "sent_at": "2026-06-01T10:35:00Z"
    }
  ]
}
```

### 5.2 对话生命周期（**已废弃**）

1. **创建（已废弃）**：收到不在任何现有线程中的新邮件时，`handle_email()` 创建新线程
2. **消息关联（已废弃）**：通过 `References` / `In-Reply-To` 邮件头匹配现有线程
3. **历史拼接（已废弃）**：每次调用 `_build_prompt()` 将完整历史拼入 prompt，保持上下文
4. **保存（已废弃）**：每次 Claude Code 回复后，`_save_thread()` 追加往来消息
5. **查询**：`mailcode conversation list` / `status` 查看活跃对话（已废弃 → `mailcode session list` / `show`，详见 §12.8）
6. **终止**：`mailcode conversation terminate <token>` 删除线程记录（已废弃 → `mailcode session delete <id>`）

### 5.3 工作目录（**已废弃**）

每个线程可关联一个 `project_dir`（已废弃, 详见 §12.4 cwd 机制）, 在首次创建时确定：
- 当前工作目录（`mailcode serve` 启动时的目录, 已废弃 → `Path.home()`）
- 或通过 `--project-dir` 参数指定（已废弃 → 邮件正文 `cwd: <path>` 行）

对话中所有 `claude -p` 调用均在此目录下执行。

---

## 6. 配置设计

### 6.1 用户级配置（`~/.config/mailcode/config.json`）

统一配置，包含所有行为参数和密钥。

```json
{
  "smtp": {
    "host": "smtp.qq.com",
    "port": 465,
    "secure": true,
    "user": "your@email.com",
    "pass": "your-app-password"
  },
  "imap": {
    "host": "imap.qq.com",
    "port": 993,
    "secure": true,
    "user": "your@email.com",
    "pass": "your-app-password"
  },
  "email": {
    "from": "your@email.com",
    "from_name": "MailCode",
    "to": "your@email.com",
    "check_interval": 30
  },
  "security": {
    "allowed_senders": ["your@email.com"],
    "auth_policy": "warn"
  },
  "conversation": {
    "enabled": true,
    "response_timeout_seconds": 300,
    "idle_timeout_hours": 24,
    "system_prompt": "你正在通过电子邮件与用户交流。请用自然语言、友好、完整地回复。回复内容直接就是邮件正文。"
  }
}
```

### 6.2 配置加载优先级

| 优先级 | 路径 | 用途 |
|--------|------|------|
| 1（最高） | `~/.config/mailcode/config.json` | 用户级完整配置 |
| 2 | `config/default.json` | 项目默认模板（首次运行时自动复制到用户配置目录） |

> 首次运行时，如果未找到 `~/.config/mailcode/config.json`，自动将 `config/default.json` 模板复制到用户目录。

### 6.3 配置初始化流程

```
1. 检查 ~/.config/mailcode/config.json 是否存在
2. 如果不存在：
   a. 创建 ~/.config/mailcode/ 目录
   b. 从 config/default.json 复制模板
   c. 提示用户编辑配置
3. 加载配置，合并默认值
```

### 6.4 启动预检

`mailcode config.py` 提供 `validate_serve_config() -> list[str]`，在 `mailcode serve` 启动**最早期**（`setup_logging` 之前）调用，杜绝"假活"——配置缺失时直接打印 + `sys.exit(1)`，不构造 `IMAPListener`，不写 `relay.log`。

**5 类必填检查项**：

| # | 检查项 | 来源 | 错误消息 |
|---|--------|------|----------|
| 1 | `mailcode_bot.email` 非空 | `_get_bot_config(config)` | `mailcode_bot.email 未设置` |
| 2 | `mailcode_bot.password` 非空 | `_get_bot_config(config)` | `mailcode_bot.password 未设置` |
| 3 | SMTP `host` / `user` / `pass` 非空 | `get_smtp_config()`（含 `_merge_identity`） | `SMTP host 未设置（自动识别失败）` / `SMTP 用户或 mailcode_bot.email 未设置` / `SMTP 密码或 mailcode_bot.password 未设置` |
| 4 | IMAP `host` / `user` / `pass` 非空 | `get_imap_config()`（含 `_merge_identity`） | `IMAP host 未设置（自动识别失败）` / `IMAP 用户或 mailcode_bot.email 未设置` / `IMAP 密码或 mailcode_bot.password 未设置` |
| 5 | `security.allowed_senders` 非空列表 | `config.get("security", {})` | `security.allowed_senders 为空（至少应包含自己的邮箱）` |

**调用流程**：

```
mailcode serve:
  1. 解析 args
  2. validate_serve_config() → list[str]
  3. 若 errors 非空:
       print("❌ MailCode 中继启动失败:")
       for e in errors: print(f"  - {e}")
       sys.exit(1)
  4. setup_logging(log_file)        # 仅在预检通过后
  5. IMAPListener()                 # 仅在预检通过后
  6. listener.listen(...)
```

**复用 `mailcode config validate`**：
`_cmd_config_validate`（`cli.py:189-228`）改用 `validate_serve_config()` 替换本地 SMTP/IMAP/bot 重复检查。两者错误消息、退出码一致——`mailcode serve` 失败时打印 `"MailCode 中继启动失败:"` 前缀，`mailcode config validate` 失败时打印 `"配置校验失败:"` 前缀，但底层 5 类检查完全相同（避免双份代码漂移）。

**关键不变量**：
- `validate_serve_config` 只检查"必填缺失"，**不检查实际连通性**（SMTP/IMAP 连接是 `mailcode health` 的事，不应阻塞 `serve` 启动——网络抖动时也能起服务）
- 配置读取失败（文件不存在 / JSON 损坏）→ 返回 `["无法读取配置: ..."]`，不抛异常
- 函数本身**不调 `sys.exit`**——返回错误列表，调用方各自决定退出码文案（`cmd_serve` vs `_cmd_config_validate`）

---

## 7. 邮件验证与清理

### 7.1 邮件处理概览

```
邮件到达收件箱
    │
    ├─ 1. 反自循环（_is_own_message）
    ├─ 2. 去重（_is_already_sent）
    ├─ 3. 域名认证（verify_auth_results）
    ├─ 4. 发件人白名单（is_sender_allowed）
    ├─ 5. 正文提取（_extract_body）
    └─ 6. 正文清理（_clean_body）
         → 对话处理（ConversationHandler）
```

> Authentication-Results 验证在正文提取之前进行，基于邮件头部而非正文内容，因此独立于正文清理。

### 7.2 清理流程

原始邮件正文 → 经过以下清理步骤 → 干净文本：

1. **移除引用块**：从 `-----Original Message-----` 到文件末尾全部删除
2. **移除邮件引用**：删除以 `On ... wrote:` 开头的行
3. **移除引用前缀**：去除行首的 `> ` 前缀
4. **移除签名**：删除以 `-- ` 开头的签名分隔行及其后所有内容
5. **移除客套语**：过滤 `Sent from`、`Best regards`、`Sincerely` 等结尾用语
6. **移除问候语**：跳过 `hi`、`hello`、`thanks`、`ok`、`yes` 等单行问候
7. **去重**：检测并移除自我重复文本

清理后的纯文本直接作为邮件内容传递给 ConversationHandler。

### 7.3 反自循环机制

**两步检测法**：
1. **已发记录匹配**：检查 `state.json` 的 `sent_messages` 键，跳过系统发送的具有匹配 Message-ID 的邮件
2. **自定义头部检测**：检查 `X-MailCode-Conversation: true` 头部，跳过自发送邮件

---

## 8. 目录结构规划

```
MailCode/
├── mailcode/                           # 包目录
│   ├── __init__.py
│   ├── cli.py                     # 统一 CLI 入口 + 命令路由
│   ├── config.py                  # 配置加载
│   ├── health.py                  # 连通性检查
│   ├── provider_presets.py        # 邮件服务商预设（SMTP/IMAP 默认值 + 域名检测）
│   ├── server.py                  # 监听服务入口（IMAPListener 生命周期）
│   ├── session_cli.py             # session 子命令的 CLI 呈现
│   ├── channels/
│   │   └── email_channel.py       # EmailChannel 类（SMTP 发送）
│   ├── relay/
│   │   ├── email_listener.py       # IMAPListener（邮件轮询 + 正文清理 + 域名认证）
│   │   ├── security.py             # 域名认证 + 发件人白名单
│   │   └── conversation_handler.py # ConversationHandler（claude -p 调用 + 对话历史）
│   ├── utils/
│   │   └── logging.py             # 结构化日志
│   └── resources/
│       └── default.json           # 默认配置
├── build.sh                       # 构建脚本
├── install.sh                     # 一键安装脚本
├── prepare.sh                     # 开发环境准备
├── docs/
│   ├── design-final/
│   │   └── design.md              # 本文档
│   └── plans/
│       └── ...
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── binary/
│   └── run_tests.sh
├── pyproject.toml                 # 包配置（零第三方依赖）
└── requirements-dev.txt           # 开发依赖
```

**构建说明**：
- 构建：`bash build.sh` → `dist/` 目录生成 `.whl` 安装包
- 安装：`bash install.sh --local dist/mailcode-*.whl`
- 包内资源文件（`resources/`、`templates/`）通过 `importlib.resources.files("src")` 加载并打包到 wheel 中
- 运行时数据目录统一在 `~/.config/mailcode/` 下 (含 config 和 data)
- 纯 Python 实现，零第三方运行时依赖（仅需系统安装 `claude` CLI）

---

## 9. 关键技术依赖

### Python 标准库（零第三方依赖）
- `imaplib` — IMAP 协议客户端
- `smtplib` — SMTP 协议客户端
- `email` — MIME 邮件解析与构造
- `subprocess` — 调用 `claude -p`
- `json` — 对话数据持久化
- `threading` — IDLE 长连接并发
- `re` — 邮件清理与提取
- `secrets` — Token 生成

### 外部工具
- `claude` — Claude Code CLI（必需，通过 `claude -p` 调用）
- `launchd` / `systemd` — 系统服务管理（macOS / Linux 守护进程）

---

## 10. 用户界面

MailCode 是纯 CLI 工具，提供以下 5 个命令（**`mailcode conversation` 已废弃，已重命名为 `mailcode session`，详见 §12.8**）：

| 命令 | 说明 |
|------|------|
| `mailcode serve` | 启动 IMAP 监听守护进程 |
| `mailcode conversation` | **已废弃** → `mailcode session` 管理对话（list / show / delete / cleanup） |
| `mailcode config` | 配置管理（init / show / validate / init-test） |
| `mailcode health` | 连通性检查 |

### 10.1 `mailcode serve` 关键标志

| 标志 | 含义 |
|------|------|
| `--once` | 单次轮询后退出（不进入 IDLE 长连接） |
| `--idle` | IDLE 长连接模式（IMAP IDLE 推送新邮件） |
| `--dry-run` | 干跑：仅打印邮件，不调 `claude -p`、不发 SMTP 回信 |
| `--session` / `-S` | **临时覆盖 config 中 `session.enabled`**，等价于在 `process_email` 调用时 `force_session=True`。`--session` 强制走多轮对话；不传则 `force_session=None`，走 `is_session_enabled()` 默认逻辑（详见 §3.7 路由表） |

**注意**：`--session` 不影响 `--dry-run`——`dry_run=True` 优先于 `force_session` 路由，永远走 DRY RUN 分支。

所有操作（配置管理、服务启动、健康检查等）均通过命令行子命令完成。无需图形界面或终端 UI 框架。

---

## 12. Session 管理

> 自 2026-06 重构起，对话/线程模型已从单一 `threads.json` 重写为 per-session 文件 + 索引的"dumb pipe" 架构。本章描述当前线上架构，旧设计相关概念（`threads.json`、`project_dir`、MailCode 内置 `system_prompt`）已全部废弃，不再维护。

### 12.1 设计目标

让 MailCode 真正变成 "dumb pipe"：

- 收邮件 → 存盘 → 通知 Claude → 发邮件
- 不参与任何智能决策（上下文管理、cwd 设置、system prompt）
- 这些事全部交给 Claude 自己的机制（`cwd/CLAUDE.md`、`Read` 工具、文件系统）

> **默认值与回退**：`session.enabled` 默认 `true`（开箱即多轮对话）；如关闭，则走 stateless fallback（详见 §3.7）——一封邮件一次 `claude -p` 一封回信，**不静默丢邮件**。

### 12.2 数据存储

`~/.config/mailcode/` 根目录下扁平布局：

```
~/.config/mailcode/
├── config.json              # 用户级配置
├── relay.log                # 中继日志
├── conversations/           # session 存储
│   ├── index.json           # msg_id → session_id 索引（O(1) 查找）
│   ├── session_<uuid>.json  # 一个 session 一份文件
│   └── ...
└── state.json               # IMAP listener 状态: processed_uids (已处理 UID 集合, 永久) + sent_messages (已发送邮件记录, 7 天滚动清理)
```

**conversations/ 子目录结构**：

```
conversations/
├── index.json              # msg_id → session_id 索引（O(1) 查找）
├── session_<uuid>.json     # 一个 session 一份文件
├── session_<uuid>.json
└── ...
```

**session JSON 结构**：

```json
{
  "session_id": "a1b2c3d4e5f6",
  "cwd": "/Users/zs/Projects/MyApp",
  "created_at": 1717500000.0,
  "last_interaction": 1717500000.0,
  "emails": [
    {
      "direction": "incoming",
      "from": "user@example.com",
      "to": "bot@example.com",
      "subject": "项目咨询",
      "body": "你好, 我想咨询...",
      "msg_id": "<abc@user.com>",
      "in_reply_to": null,
      "references": null,
      "date": "2026-06-01T10:23:00Z"
    },
    {
      "direction": "outgoing",
      "from": "bot@example.com",
      "to": "user@example.com",
      "subject": "Re: 项目咨询",
      "body": "你好!",
      "msg_id": "<xyz@mailcode.com>",
      "in_reply_to": "<abc@user.com>",
      "references": null,
      "date": "2026-06-01T10:25:00Z"
    }
  ]
}
```

**index.json 结构**：

```json
{
  "version": 1,
  "updated_at": 1717500000.0,
  "msg_to_session": {
    "<any_incoming_or_outgoing_msg_id>": "<session_id>"
  }
}
```

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| session_id 生成 | `uuid4().hex[:12]` | 跟邮件头解耦, 不受客户端差异影响, 12 位 hex 足够 |
| 创建时机 | 收到第一封邮件立即创建 | 失败可重试不丢历史, 实现简单 |
| 查找机制 | index.json 优先, 全量扫描兜底 | 1000+ session 时 index 仍 O(1), 扫描不依赖索引 |
| 文件格式 | JSON, 每条记录是真实邮件 (`from`/`subject`/`body`/`msg_id`/`in_reply_to`/`date`) | email-centric, 不是 chat-centric; Claude 读起来自然 |
| 旧 `threads.json` | **已废弃, 不迁移** | 项目无生产用户, 干净落地, 不留兼容代码 |
| 旧 `project_dir` 概念 | **完全废弃**（per-instance / per-session / per-config 三级 fallback 全删） | 跟"自然语言对话"产品定位不匹配 |

### 12.3 主流程 `handle_email`

```
1. 收邮件 (from IMAPListener)
2. 提取 cwd: 从 body 匹配 ^cwd:\s*(.+)$ (大小写不敏感, ~ 展开, is_dir 验证)
3. 剥离 cwd: 把该行从 body 移除后再存
4. 查找/创建 session:
   - 查 index[msg_to_session][in_reply_to] → 找到则 load
   - 找不到则扫描 session_*.json 兜底
   - 都没有则创建空 session (session_id = uuid4().hex[:12])
5. 如果新邮件带 cwd → session.cwd = extracted_cwd
6. 追加 incoming 邮件到 session.emails
7. 保存 session 文件
8. 更新 index: 把 incoming.msg_id → session_id
9. 构建最小 prompt: "用户最新邮件已写入 {session_file}, 请用 Read 工具读取后回复"
10. 调 claude -p, cwd=session.cwd or Path.home()
11. 处理错误:
    - returncode != 0 / FileNotFoundError / TimeoutExpired → 写日志 + 发邮件通知用户
    - response 空字符串 / 全空白 → 写日志 + 发邮件通知用户
12. 追加 outgoing 邮件到 session.emails
13. 保存 session 文件
14. 更新 index: 把 outgoing.msg_id → session_id
15. 发回复邮件 (用 SMTP), 带 In-Reply-To + References 头
```

### 12.4 cwd 机制

核心新特性：用户在邮件正文**首行**写 `cwd: <path>` 即可指定该 session 的工作目录。

| 行为 | 说明 |
|---|---|
| 提取规则 | 正则 `^cwd:\s*(.+?)\s*$`（行首匹配, 大小写不敏感, `~` 自动展开） |
| 校验 | 必须是已存在的目录 (`is_dir()`), 否则忽略并 fallback 到 `Path.home()` |
| 剥离 | 匹配到的 `cwd:` 行从 body 中删除后再存, 不会污染对话历史 |
| 粘性 | session.cwd 一旦设置, 整个 session 沿用; 后续邮件不指定则保持 |
| 覆盖 | 新邮件再次带 `cwd:` 时会覆盖（允许切换工作目录） |
| 默认值 | 未指定时 `cwd = Path.home()`, 永远有效, 跟 systemd/launchd CWD 解耦 |

**示例**：

```
Subject: 看下这个项目

cwd: ~/Projects/MyApp
请帮我看下 src/main.py 有没有内存泄漏。
```

MailCode 收到后会把第一行剥离、验证 `~/Projects/MyApp` 存在、把 `claude -p` 的 `cwd` 设为该路径。

### 12.5 System Prompt

| 旧设计 | 新设计 |
|---|---|
| MailCode config 里塞一段长 `system_prompt` 字符串 | 走 Claude Code 原生 `cwd/CLAUDE.md` 机制 |
| MailCode 拼接 `system_prompt + history + 新邮件` 后调 `claude -p` | MailCode 只传最小指令, Claude 用 `Read` 工具自助读 session 文件 + `cwd/CLAUDE.md` |

单一职责：

- MailCode 管邮件
- Claude Code 管 system prompt / 上下文管理 / 工具选择

### 12.6 TTL 清理

| 项 | 值 |
|---|---|
| 默认 TTL | 90 天（`config.session_ttl_days`） |
| 触发时机 | 启动 `mailcode serve` 时 + `mailcode session cleanup` 手动触发 |
| 判定依据 | `last_interaction` 距今超过 TTL |
| 损坏文件 | warn 但保留（不删）, 给用户恢复机会 |
| index 同步 | 删除时同时清理 `index.json` 中的 `msg_to_session` 条目 |
| Dry-run | `mailcode session cleanup --dry-run` 预览将清理的 session, 不实际删除 |

### 12.7 错误处理

| 场景 | 行为 |
|---|---|
| `claude -p` returncode != 0 | 写日志 + 发邮件通知用户, **不重试**（重试可能再失败） |
| `FileNotFoundError` (claude 未安装) | 同上 |
| `TimeoutExpired` (超过 `response_timeout_seconds`) | 同上 |
| response 空字符串 / 全空白 | 同上 |
| SMTP 发送失败 | session 仍写入, 返回 `False` 触发 IMAP 未读回滚 |
| session 文件 JSON 损坏 | warn + 返回空 session, 不会丢失其他 session |
| index.json 损坏 | warn + 扫描兜底, 启动时**不重建**索引 |

**设计原则**：错误不静默（用户必须知道）, 也不重试（避免重复失败）; 数据一致性优先（即使 claude 失败, incoming 邮件仍入 session, 给用户手动恢复的机会）。

### 12.8 CLI

顶层命令：`mailcode session`（替代旧的 `mailcode conversation`）。

| 子命令 | 说明 |
|---|---|
| `mailcode session list` | 列出所有 session（ID / cwd / 创建时间 / 最后交互 / 邮件数） |
| `mailcode session show <id>` | 查看单个 session 详情（含完整邮件列表） |
| `mailcode session delete <id>` | 删除指定 session（含 index 清理） |
| `mailcode session cleanup [--dry-run]` | 按 TTL 清理过期 session, `--dry-run` 仅预览 |

### 12.9 不变量

- **session.cwd 一旦设置, 整个 session 沿用**（除非新邮件再次带 `cwd:` 覆盖）
- **outgoing 邮件总是写到 session 文件再 SMTP 发**（失败可重发, 不重调 claude）
- **index 写入失败不影响正确性**（扫描是兜底）
- **损坏的 session 文件不删, 只 warn**（用户可手动恢复）
- **claude 失败 / 空 response 都发通知邮件**（不静默, 不重试）
- **多 session 路由严格 In-Reply-To 匹配**, 无匹配建新 session（不做"短时间同 from 合并"这种猜测）
