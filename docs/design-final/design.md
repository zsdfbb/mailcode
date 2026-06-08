# MailCode Email-Agent Bridge 设计文档

> 目标:通过邮件远程操控 Claude Code AI 助手。用户发送邮件,MailCode 拉取后调用 `claude -p` 处理,回复自动发回用户邮箱,基于 `In-Reply-To` 邮件头做多轮 session 路由。

---

## 1. 核心架构概览

```
┌──────────────┐  SMTP 回复        ┌──────────────┐
│  Claude Code  │ ────────────────> │  用户邮箱     │
│  (claude -p)   │ <──────────────── │              │
└──────────────┘  IMAP 新邮件      └──────────────┘
       ▲                                   │
       │ subprocess.run                     │ 新邮件
       │ ["claude", "-p", prompt,           │
       │  "--dangerously-skip-permissions"] │
       │                                   ▼
       │                          ┌──────────────┐
       │                          │ IMAP 监听器  │
       └──────────────────────────│  + Handler  │
              stdout = 回复       │ (Conv/Stateless) │
                                  └──────────────┘
                                        │
                                        │ 读 / 写
                                        ▼
                                  ┌──────────────┐
                                  │  sessions/   │
                                  │ per-file +   │
                                  │ index.json   │
                                  └──────────────┘
```

### 设计哲学:dumb pipe

MailCode 只负责"邮件 ↔ Claude Code"的中转,不做任何智能决策:

- **上下文管理**:交给 Claude Code 自助 (用 `Read` 工具读 session 文件)
- **工作目录**:由用户在邮件正文首行写 `cwd: <path>` 指定,session 内粘性沿用
- **system prompt**:完全交给 `cwd/CLAUDE.md`,MailCode 不再内置
- **cwd 校验、body 剥离、错误兜底** 用最小原语 (`extract_cwd` / `strip_cwd` / `call_claude` / `send_error_email`)

### 工作流程

| 步骤 | 说明 |
|------|------|
| 1 | 用户发送 (或回复) 邮件到 bot 邮箱 |
| 2 | IMAP 监听器检测到新邮件,做反自循环 + 去重 + Auth-Results 校验 + 白名单 4 道关 |
| 3 | 路由: `session.enabled=true` 走 `ConversationHandler`, `false` 走 `StatelessHandler` (`--session`/`-S` 显式覆盖) |
| 4 | Handler 从 body 提取 `cwd: <path>` (首行正则, `~` 展开 + `is_dir` 校验), 剥离该行后入库 |
| 5 | `ConversationHandler`: 通过 `In-Reply-To` 查 `index.json` 定位 session; 找不到则扫描 `session_*.json` 兜底; 都没有则新建 (session_id = `uuid4().hex[:12]`) |
| 6 | 追加 incoming 邮件 → 原子写 session 文件 (tmp + replace) → 更新 session.cwd (新邮件指定则覆盖) |
| 7 | 构造最小 prompt: `"用户最新邮件已写入 {session_file}, 请用 Read 工具读取后回复"`, 调 `claude -p` (cwd = `session.cwd or Path.home()`) |
| 8 | `claude -p` 失败 / 返回空 → 写日志 + 调 `send_error_email` 通知用户, **不静默不重试** |
| 9 | 追加 outgoing 邮件到 session → 原子写 session → `send_reply` 发 SMTP 回信 (带 `In-Reply-To` + `References` 头) |
| 10 | 拿到 `our_msg_id` → 回填 outgoing.msg_id → 写回 session → 写入 `index.json` (outgoing.msg_id → session_id) |

---

## 2. 邮件通道设计

### 2.1 邮件回复发送 (SMTP)

**核心模块**:`mailcode/channels/email_channel.py` → `EmailChannel.send_reply()`

**关键特性**:
- `Content-Type: text/plain; charset=utf-8`
- 主题: Claude 返回的 stdout 直接作为邮件正文, 主题保持原邮件线程 (`Re: x` 不重复加)
- 线程追踪: 生成的 Message-ID 写入 `Message-ID` 头; 设置 `In-Reply-To`; `References` = 原 `References` + `In-Reply-To`
- 返回值: `(success: bool, message_id: Optional[str])` — message_id 用于回填 session 与更新 index

### 2.2 邮件回复接收 (IMAP)

**核心模块**:`mailcode/relay/email_listener.py` → `IMAPListener`

**4 道关**:

1. **反自循环** (`_is_own_message`): 检查 `X-MailCode-Remote-Token` / `X-OpenCode-Remote-Token` 头, 跳过自身发出邮件
2. **去重** (`_is_duplicate`): UID 在 `state.json.processed_uids` 集合内, 或 Message-ID 在 `state.json.sent_messages` 中, 跳过
3. **Auth-Results 校验** (`SecurityChecker.verify_auth_results`): 解析 `Authentication-Results` 头, 验证 `dkim=pass` + `spf=pass`; `temperror/permerror` 放行; 按 `auth_policy` (warn/strict/off) 决定是否拒绝
4. **发件人白名单** (`SecurityChecker.is_sender_allowed`): 全邮箱精确匹配或 `@<domain>` 后缀匹配

**正文处理**:
- `_extract_body()`: 多部分邮件取 `text/plain`, 单部分直接取 payload, 编码按 `get_content_charset()` (默认 `utf-8`)
- `_clean_body()`: 移除 `> ` 引用前缀 / `Original Message` 分隔 / `-- ` 签名 / `On ... wrote:` 引用 / 客套结尾语 / 多余空行

**状态文件** (`~/.config/mailcode/state.json`):
```json
{
  "processed_uids": ["123", "124", ...],   // 已处理 UID 集合, 启动时基线
  "sent_messages": [                       // 已发邮件记录, 7 天滚动清理
    {"message_id": "<abc@example.com>", "thread_id": "...", "sent_at": "..."}
  ]
}
```

上限保护: `processed_uids` 超过 10000 时整体清空 (防止无限增长)。

**守护进程生命周期**:
- `mailcode serve` 作为长期运行的守护进程, 可与 `launchd` (macOS) / `systemd` (Linux) 配合实现开机自启
- 启动时建基线: 现有 UNSEEN 邮件全部加到 `processed_uids`, 不处理历史邮件
- 关闭时: 断开 IMAP 连接 (`logout`), 原子写 `state.json`, 清理 7 天前的 `sent_messages`

---

## 3. 配置设计

### 3.1 用户级配置 (`~/.config/mailcode/config.json`)

**3 个顶层段** (与 `mailcode/resources/default.json` 一致):

```json
{
  "mailcode_bot": {
    "email": "your@email.com",
    "password": "your-app-password",
    "from_name": "Mailcode Remote",
    "check_interval": 5
  },
  "security": {
    "allowed_senders": ["your@email.com", "@yourcompany.com"],
    "auth_policy": "warn"
  },
  "session": {
    "enabled": true,
    "response_timeout_seconds": 180,
    "idle_timeout_hours": 4,
    "session_ttl_days": 90,
    "cleanup_on_startup": true
  }
}
```

**字段语义**:

| 段 | 字段 | 用途 |
|----|------|------|
| `mailcode_bot` | `email` | bot 邮箱, 也是 SMTP 发信账号 |
| | `password` | 授权码 / 应用专用密码 (非登录密码) |
| | `from_name` | 发件人显示名 |
| | `check_interval` | 轮询间隔秒数 (默认 5) |
| `security` | `allowed_senders` | 白名单; 支持全邮箱或 `@<domain>` 后缀 |
| | `auth_policy` | `warn` / `strict` / `off` |
| `session` | `enabled` | 默认走 conversation 还是 stateless |
| | `response_timeout_seconds` | `claude -p` 子进程超时 (默认 180) |
| | `idle_timeout_hours` | 空闲超时 (小时, 默认 4) |
| | `session_ttl_days` | session 过期天数, ≤0 = 不清理 (默认 90) |
| | `cleanup_on_startup` | 启动时自动清理 (默认 true) |

### 3.2 配置加载优先级

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 (最高) | `--config / -c PATH` (CLI 参数) | 运行时通过 `set_config_path()` 覆盖 |
| 2 | `MAILCODE_CONFIG` 环境变量 | 同上, 模块级导入时读取 |
| 3 | `~/.config/mailcode/config.json` | 用户级默认配置 |
| 4 | `mailcode/resources/default.json` | 内置模板, 首次运行自动复制到用户目录 |

**邮件服务商自动识别** (`mailcode/provider_presets.py`):
- 根据 `mailcode_bot.email` 域名匹配 `DOMAIN_PROVIDER_MAP` (qq / 126 / 163 / gmail / outlook)
- 命中则填入 `PROVIDER_PRESETS` 中的 SMTP/IMAP host + port + secure
- 用户的 `smtp` / `imap` 段会覆盖预设值 (manual > preset)
- 邮箱或密码缺失时, `_merge_identity()` 从 `mailcode_bot` 段补全

### 3.3 配置初始化流程

```
1. 检查 ~/.config/mailcode/config.json 是否存在
2. 不存在:
   a. 创建 ~/.config/mailcode/ 目录
   b. 从 mailcode/resources/default.json 复制模板
   c. 提示用户编辑 (填入 email + password)
3. 加载配置 (有缓存, force_reload=True 可重载)
4. 合并默认值 (get_*_config 系列函数)
```

### 3.4 启动预检 (`validate_serve_config`)

`mailcode/config.py:validate_serve_config() -> list[str]` 在 `mailcode serve` 启动**最早期** (`setup_logging` 之前) 调用, 杜绝"假活"——配置缺失时直接打印 + `sys.exit(1)`, 不构造 `IMAPListener`, 不写 `relay.log`。

**5 类必填检查项**:

| # | 检查项 | 来源 | 错误消息 |
|---|--------|------|----------|
| 1 | `mailcode_bot.email` 非空 | `_get_bot_config(config)` | `mailcode_bot.email 未设置` |
| 2 | `mailcode_bot.password` 非空 | `_get_bot_config(config)` | `mailcode_bot.password 未设置` |
| 3 | SMTP `host` / `user` / `pass` 非空 | `get_smtp_config()` (含 `_merge_identity`) | `SMTP host 未设置 (自动识别失败)` / `SMTP 用户或 mailcode_bot.email 未设置` / `SMTP 密码或 mailcode_bot.password 未设置` |
| 4 | IMAP `host` / `user` / `pass` 非空 | `get_imap_config()` (含 `_merge_identity`) | 同 SMTP 模式 |
| 5 | `security.allowed_senders` 非空列表 | `config.get("security", {})` | `security.allowed_senders 为空 (至少应包含自己的邮箱)` |

**调用流程**:

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

**复用 `mailcode config validate`**:
`_cmd_config_validate` (cli.py) 改用 `validate_serve_config()` 替换本地 SMTP/IMAP/bot 重复检查。两者错误消息、退出码一致——`mailcode serve` 失败时打印 `"MailCode 中继启动失败:"` 前缀, `mailcode config validate` 失败时打印 `"配置校验失败:"` 前缀, 但底层 5 类检查完全相同 (避免双份代码漂移)。

**关键不变量**:
- `validate_serve_config` 只检查"必填缺失", **不检查实际连通性** (SMTP/IMAP 连接是 `mailcode health` 的事, 不应阻塞 `serve` 启动)
- 配置读取失败 (文件不存在 / JSON 损坏) → 返回 `["无法读取配置: ..."]`, 不抛异常
- 函数本身**不调 `sys.exit`**——返回错误列表, 调用方各自决定退出码文案 (`cmd_serve` vs `_cmd_config_validate`)

---

## 4. 安全管理

### 4.1 安全策略

| 策略 | 说明 |
|------|------|
| 发件人白名单 | 仅处理 `allowed_senders` 中列出的地址发来的邮件 (全邮箱精确匹配或 `@<domain>` 后缀) |
| 域名认证 | 解析 `Authentication-Results` 头, 验证 `dkim=pass` + `spf=pass`; 按 `auth_policy` 决定放行 |
| 去重机制 | 基于邮件 UID + Message-ID 的双重去重 (`state.json`) |
| 定期清理 | `state.json` 中 `sent_messages` 保留最近 7 天; `processed_uids` 上限 10000 |

### 4.2 Authentication-Results 域名认证

解析邮件服务商 (Gmail/QQ/Outlook/126/163) 自动添加的 `Authentication-Results` 头, 验证 `dkim=pass` + `spf=pass`, 与 `allowed_senders` 白名单形成多层防御。

| 攻击方式 | 仅白名单 | 增加 Auth-Results |
|---------|---------|------------------|
| 伪造 `From: admin@your-company.com` | ❌ 字符串包含匹配可能绕过 | ✅ 伪造域名的 DKIM/SPF 必然会失败 |
| 伪造 `From: your@email.com` (冒充你) | ⚠️ 白名单包含自己地址时可能通过 | ✅ DKIM/SPF 不匹配会拦截 |
| 同域名恶意注册 (`attacker@gmail.com`) | ⚠️ 精确白名单可预防 | ⚠️ 同域名 DKIM 会通过, 需白名单辅助 |

**三级策略**:

| 策略 | 行为 | 场景 |
|------|------|------|
| `warn` (默认) | 认证失败仅记录日志, 不拒绝 | 向后兼容, 对用户透明 |
| `strict` | 认证失败 (fail/softfail/none/neutral/missing) 跳过该邮件 | 安全优先, 需要邮件服务商支持 Auth-Results |
| `off` | 完全跳过此检查 | 自建邮箱或无 Authentication-Results 头时 |

**结果值处理**:

| 值 | strict | warn | off |
|-------|--------|------|-----|
| `pass` | ✅ | ✅ | — |
| `fail` / `softfail` / `none` / `neutral` | ❌ 拒绝 | ⚠️ 放行 + 记录 | — |
| `temperror` / `permerror` | ✅ 放行 | ✅ 放行 | — |
| 无头部 / 空值 | ❌ 拒绝 | ✅ 放行 | — |

**处理流程**:
1. 提取 `Authentication-Results` 头值 (多个时取最后一个)
2. 合并折叠头部 (`\n[ \t]+` → 空格)
3. 正则提取 `dkim=` 和 `spf=` 的结果值
4. 根据策略判断放行 / 拒绝
5. 验证插入点: 在 `_is_own_message()` 之后, `is_sender_allowed()` 之前

---

## 5. 邮件验证与清理

### 5.1 处理流水线

```
邮件到达收件箱
    │
    ├─ 1. 反自循环 (_is_own_message)        # X-MailCode-Remote-Token / X-OpenCode-Remote-Token
    ├─ 2. 去重 (_is_duplicate)               # UID + Message-ID 双重
    ├─ 3. Auth-Results (verify_auth_results) # dkim + spf
    ├─ 4. 发件人白名单 (is_sender_allowed)   # 全邮箱 / @<domain>
    ├─ 5. 正文提取 (_extract_body)           # text/plain, charset 解码
    └─ 6. 正文清理 (_clean_body)             # 移除引用/签名/客套
         → 路由 (process_email)
         → ConversationHandler 或 StatelessHandler
```

> Auth-Results 验证在正文提取之前进行, 基于邮件头而非正文, 因此独立于正文清理。

### 5.2 正文清理

`_clean_body()` 步骤:

1. **行级截断**: 遇到 `-+ ?Original Message` / `On ... wrote:` / `-- ` 签名分隔, 截断后续
2. **去除引用**: 行首 `>` 整行跳过
3. **客套过滤**: 包含 `sent from` / `best regards` / `thanks` / `regards` / `sincerely` 且行长度 < 50 时截断
4. **空行归一**: 3+ 连续 `\n` 压成 2 个

清理后的纯文本作为邮件 body 传递给 Handler。

### 5.3 反自循环机制

**头部检测** (`_is_own_message`):
- 检查 `X-MailCode-Remote-Token` 头
- 检查 `X-OpenCode-Remote-Token` 头 (兼容旧版)
- 任一存在 → 标记为自身发出, 跳过

**发送端**: `EmailChannel.send()` 在 token 不为空时自动添加 `X-MailCode-Remote-Token` 头。`send_reply()` 不带此头 (它是给收件人回信的, 不需要标识自身 token)。

---

## 6. Session 管理 (核心)

### 6.1 设计目标

让 MailCode 真正变成 "dumb pipe":

- 收邮件 → 存盘 → 通知 Claude → 发邮件
- 不参与任何智能决策 (上下文管理、cwd 设置、system prompt)
- 这些事全部交给 Claude 自己的机制 (`cwd/CLAUDE.md`、`Read` 工具、文件系统)

> **默认值与回退**: `session.enabled` 默认 `true` (开箱即多轮对话); 如关闭, 则走 stateless fallback (详见 §7)——一封邮件一次 `claude -p` 一封回信, **不静默丢邮件**。

### 6.2 数据存储

`~/.config/mailcode/` 根目录下扁平布局:

```
~/.config/mailcode/
├── config.json              # 用户级配置
├── test_config.json         # 集成测试配置 (init-test 生成)
├── relay.log                # 中继日志 (RotatingFileHandler, 5MB × 3)
├── conversations/           # session 存储
│   ├── index.json           # outgoing msg_id → session_id 索引
│   ├── session_<uuid>.json  # 一个 session 一份文件
│   └── ...
└── state.json               # processed_uids (永久) + sent_messages (7 天滚动)
```

**session JSON 结构**:

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
      "msg_id": "",
      "in_reply_to": "<previous@example.com>",
      "references": "<...> <...>",
      "date": ""
    },
    {
      "direction": "outgoing",
      "from": "bot@example.com",
      "to": "user@example.com",
      "subject": "Re: 项目咨询",
      "body": "你好!",
      "msg_id": "<xyz@mailcode.com>",
      "in_reply_to": "<previous@example.com>",
      "references": "<...> <previous@example.com>",
      "date": ""
    }
  ]
}
```

**index.json 结构**:

```json
{
  "version": 1,
  "updated_at": 1717500000.0,
  "msg_to_session": {
    "<bot_outgoing_msg_id>": "<session_id>"
  }
}
```

> **注意**: 当前实现下, index 只索引 **outgoing** 邮件的 msg_id (incoming 的 msg_id 在 IMAP 监听层未透传)。`In-Reply-To` 路由仍然有效, 因为新邮件引用的就是 bot 上一次发出去的 msg_id。

**关键决策**:

| 决策点 | 选择 | 理由 |
|---|---|---|
| session_id 生成 | `uuid4().hex[:12]` | 跟邮件头解耦, 不受客户端差异影响, 12 位 hex 足够 |
| 写入时机 | 收到第一封邮件立即创建 | 失败可重试不丢历史, 实现简单 |
| 查找机制 | index.json 优先, 全量扫描兜底 | 1000+ session 时 index 仍 O(1), 扫描不依赖索引 |
| 文件格式 | JSON, 每条记录是真实邮件 (`from`/`subject`/`body`/`msg_id`/`in_reply_to`/`date`) | email-centric, 不是 chat-centric; Claude 读起来自然 |
| 旧 `threads.json` | 已废弃, 不迁移 | 项目无生产用户, 干净落地, 不留兼容代码 |
| 旧 `project_dir` 概念 | 完全废弃 (per-instance / per-session / per-config 三级 fallback 全删) | 跟"自然语言对话"产品定位不匹配 |

### 6.3 主流程 `handle_email`

`ConversationHandler.handle_email()` 步骤:

```
1. 收邮件 (从 IMAPListener 传入 email_entry)
2. 提取 cwd: extract_cwd(body) → 验证为已存在目录, 否则 None
3. 剥离 cwd: strip_cwd(body) → 干净正文
4. 查找 / 创建 session:
   - 查 index[msg_to_session][in_reply_to] → 找到则 load
   - index 命中但 session 文件丢失 → 清理 index 条目后继续
   - 扫描 session_*.json 兜底 (按 email 列表里 msg_id 匹配)
   - 都没有则创建空 session (session_id = uuid4().hex[:12])
5. session.cwd 粘性: 新邮件指定则覆盖
6. 追加 incoming 邮件到 session.emails
7. 原子写 session 文件 (tmp + replace)
8. 构建最小 prompt: "用户最新邮件已写入 {session_file}, 请用 Read 工具读取后回复"
9. 调 claude -p, cwd = session.cwd or Path.home()
10. 错误处理 (见 §6.7):
    - returncode != 0 / FileNotFoundError / TimeoutExpired → 发通知邮件
    - response 空字符串 / 全空白 → 发通知邮件
11. 追加 outgoing 邮件到 session.emails
12. 原子写 session 文件 (在 SMTP 之前, 失败可重发)
13. send_reply 发 SMTP 回信
14. 拿到 our_msg_id → 回填 outgoing.msg_id → 重存 session → 写 index
```

### 6.4 cwd 机制

核心特性: 用户在邮件正文**首行**写 `cwd: <path>` 即可指定该 session 的工作目录。

| 行为 | 说明 |
|---|---|
| 提取规则 | 正则 `^cwd:\s*(.+?)\s*$` (行首匹配, 大小写不敏感, `~` 自动展开) |
| 校验 | 必须是已存在的目录 (`is_dir()`), 否则忽略并 fallback 到 `Path.home()` |
| 剥离 | 匹配到的 `cwd:` 行从 body 中删除后再存, 不会污染对话历史 |
| 粘性 | session.cwd 一旦设置, 整个 session 沿用; 后续邮件不指定则保持 |
| 覆盖 | 新邮件再次带 `cwd:` 时会覆盖 (允许切换工作目录) |
| 默认值 | 未指定时 `cwd = Path.home()`, 永远有效, 跟 systemd/launchd CWD 解耦 |

**示例**:

```
Subject: 看下这个项目

cwd: ~/Projects/MyApp
请帮我看下 src/main.py 有没有内存泄漏。
```

MailCode 收到后会把第一行剥离、验证 `~/Projects/MyApp` 存在、把 `claude -p` 的 `cwd` 设为该路径。

### 6.5 System Prompt

| 旧设计 | 新设计 |
|---|---|
| MailCode config 里塞一段长 `system_prompt` 字符串 | 走 Claude Code 原生 `cwd/CLAUDE.md` 机制 |
| MailCode 拼接 `system_prompt + history + 新邮件` 后调 `claude -p` | MailCode 只传最小指令, Claude 用 `Read` 工具自助读 session 文件 + `cwd/CLAUDE.md` |

单一职责:

- MailCode 管邮件
- Claude Code 管 system prompt / 上下文管理 / 工具选择

### 6.6 TTL 清理

| 项 | 值 |
|---|---|
| 默认 TTL | 90 天 (`config.session.session_ttl_days`) |
| 触发时机 | `mailcode serve` 启动时 (若 `cleanup_on_startup=true`) + `mailcode session cleanup` 手动触发 |
| 判定依据 | `last_interaction` 距今超过 TTL |
| 损坏文件 | warn 但保留 (不删), 给用户恢复机会 |
| index 同步 | 删除时同时清理 `index.json` 中所有映射到该 session 的 msg_id |
| Dry-run | `mailcode session cleanup --dry-run` 预览将清理的 session, 不实际删除 |

### 6.7 错误处理

| 场景 | 行为 |
|---|---|
| `claude -p` returncode != 0 | 写日志 + 发邮件通知用户, **不重试** (重试可能再失败) |
| `FileNotFoundError` (claude 未安装) | 同上 |
| `TimeoutExpired` (超过 `response_timeout_seconds`, 默认 180s) | 同上 |
| response 空字符串 / 全空白 | 同上 |
| SMTP 发送失败 | session 仍写入 (含 outgoing 占位), 返回 `False` 触发 IMAP 未读回滚 |
| session 文件 JSON 损坏 | warn + 返回空 session, 不会丢失其他 session |
| index.json 损坏 | warn + 扫描兜底, 启动时**不重建**索引 |

**设计原则**: 错误不静默 (用户必须知道), 也不重试 (避免重复失败); 数据一致性优先 (即使 claude 失败, incoming 邮件仍入 session, 给用户手动恢复的机会)。

### 6.8 不变量

- **session.cwd 一旦设置, 整个 session 沿用** (除非新邮件再次带 `cwd:` 覆盖)
- **outgoing 邮件总是写到 session 文件再 SMTP 发** (失败可重发, 不重调 claude)
- **index 写入失败不影响正确性** (扫描是兜底)
- **损坏的 session 文件不删, 只 warn** (用户可手动恢复)
- **claude 失败 / 空 response 都发通知邮件** (不静默, 不重试)
- **多 session 路由严格 In-Reply-To 匹配**, 无匹配建新 session (不做"短时间同 from 合并"这种猜测)

---

## 7. Stateless Fallback

`session.enabled = false` 时的回退路径——`IMAPListener.process_email` 不再静默丢邮件, 而是走 `StatelessHandler` 完成"一封邮件 → 一次 `claude -p` → 一封回信"。

**核心模块**:`mailcode/relay/stateless_handler.py`

**设计意图**:
- **escape hatch**: 给"想要单次回复、不要 session 持久化"的进阶用户一条出路, 且**不丢邮件**
- **静默丢 bug 修复**: 旧版 `session.enabled=false` 表现为"假活" (UID 标记, 无回信, 无警告)——本节是 P0 修复
- **职责单一**: `StatelessHandler` 不继承 `ConversationHandler` (无 session 文件生命周期, 无 `_conv_dir` / `_index_file` 实例属性)。cwd 解析、`call_claude`、`send_error_email` 三个共用原语提到 `conversation_handler.py` 模块级 (`extract_cwd` / `strip_cwd` / `call_claude` / `send_error_email`), 两个 handler 都直接调模块函数

### 7.1 路由表 (`process_email`)

| `dry_run` | `force_session` | `is_session_enabled()` | 路径 | 返回 `mode` |
|-----------|-----------------|------------------------|------|-------------|
| `True` | — | — | 打印 DRY RUN 日志 | `"dry_run"` |
| `False` | `True` | — | `_handle_via_conversation` | `"conversation"` |
| `False` | `False` | — | `_handle_via_stateless` | `"stateless"` |
| `False` | `None` | `True` | `_handle_via_conversation` | `"conversation"` |
| `False` | `None` | `False` | `_handle_via_stateless` | `"stateless"` |

`force_session` 由 CLI 标志 `mailcode serve --session/-S` 驱动 (`server.py` 把 `args.session` 转为 `force_session=args.session or None`)。`None` = 走 `is_session_enabled()` 默认逻辑, `True/False` = 显式覆盖 (用于 CLI 调试 / 临时切换模式)。

### 7.2 与 `ConversationHandler` 对比

| 维度 | `ConversationHandler` | `StatelessHandler` |
|------|----------------------|--------------------|
| session 文件 | 写 `session_<uuid>.json` | 不写 |
| `index.json` | 读 + 写 | 不读不写 |
| `cwd` 粘性 | session.cwd 沿用 | 每次邮件独立 `extract_cwd`, 无粘性 |
| `claude -p` 调用 | 每次独立 | 每次独立 |
| 错误处理 | `call_claude` 失败 / 空 response → `send_error_email` | 同上 (共用模块级函数) |
| 返回值 | `True` / `False` | `True` / `False` |

### 7.3 `StatelessHandler.handle_email` 流程

```
1. 提取 cwd: extract_cwd(body) → Path 或 None
2. 剥离 cwd: strip_cwd(body) → 干净正文
3. 构建 prompt: "用户最新邮件:\n\n主题: {subject}\n\n{clean_body}\n\n请直接回复这封邮件, ..."
4. 调 claude: call_claude(prompt, cwd=extracted_cwd or Path.home())
5. 错误兜底: call_claude 返回 None / 空字符串 → send_error_email(channel, ...)
6. SMTP 发回复: subject 已是 "Re: x" 不再加, 否则加 "Re: "
7. 返回 True; SMTP 异常 / send_reply 返回 False → 返回 False
```

### 7.4 关键不变量

- `StatelessHandler` 是无状态的: 不写文件, 不读 session index, 每次调用独立
- cwd 解析在 stateless 和 conversation 路径完全一致 (都调模块级 `extract_cwd` / `strip_cwd`)
- 错误邮件路径一致: `call_claude` 失败 → `send_error_email` 兜底, 两个 handler 共用
- `force_session` 是用户级显式覆盖, 不持久化到 config
- handler 是 lazy init: `_conv_handler` / `_stateless_handler` 首次使用时构造, 二次调用复用同一实例

---

## 8. CLI

`mailcode` 是纯 CLI 工具, 5 个顶层命令:

| 命令 | 说明 |
|------|------|
| `mailcode serve` | 启动 IMAP 监听守护进程 |
| `mailcode config` | 配置管理 (init / show / validate / init-test / path) |
| `mailcode health` | 连通性检查 (SMTP + IMAP) |
| `mailcode session` | session 管理 (list / show / delete / cleanup) |

### 8.1 `mailcode serve` 关键标志

| 标志 | 含义 |
|------|------|
| `--once` | 单次轮询后退出 (不进入 IDLE 长连接) |
| `--idle` | IDLE 长连接模式 (IMAP IDLE 推送新邮件, 推荐生产环境) |
| `--dry-run` | 干跑: 仅打印邮件, 不调 `claude -p`、不发 SMTP 回信 |
| `--session` / `-S` | **临时覆盖 config 中 `session.enabled`**, 等价于在 `process_email` 调用时 `force_session=True`。`--session` 强制走多轮对话; 不传则 `force_session=None`, 走 `is_session_enabled()` 默认逻辑 (详见 §7.1 路由表) |

**注意**: `--session` 不影响 `--dry-run`——`dry_run=True` 优先于 `force_session` 路由, 永远走 DRY RUN 分支。

### 8.2 `mailcode config` 子命令

| 子命令 | 说明 |
|--------|------|
| `init` | 首次部署: 生成默认配置到 `~/.config/mailcode/config.json` (已存在则跳过, `--force` 强制重建) |
| `show` | 打印当前配置 (密码字段自动脱敏为 `***`) |
| `path` | 打印配置文件绝对路径 |
| `validate` | 校验配置完整性 (复用 `validate_serve_config`) |
| `init-test` | 生成集成测试配置 `~/.config/mailcode/test_config.json` (与正式配置完全隔离) |

### 8.3 `mailcode session` 子命令

| 子命令 | 说明 |
|--------|------|
| `list` | 列出所有 session (ID / 发件人 / 主题 / 最近活动 / 邮件数 / cwd) |
| `show <id>` | 查看单个 session 详情 (含完整邮件流, in/out 交替) |
| `delete <id>` | 删除指定 session (会先打印详情并提示确认, `-y` / `--yes` 跳过) |
| `cleanup [--dry-run]` | 按 TTL 清理过期 session, `--dry-run` 仅预览 |

### 8.4 全局选项

| 选项 | 含义 |
|------|------|
| `--version` | 打印版本号 |
| `--config / -c PATH` | 指定配置文件路径 (覆盖 `MAILCODE_CONFIG` 环境变量和默认值) |

### 8.5 环境变量

| 变量 | 用途 |
|------|------|
| `MAILCODE_CONFIG` | 指定配置文件路径 (优先级低于 `--config`, 高于默认) |
| `MAILCODE_LOG_LEVEL` | 日志级别 (DEBUG/INFO/WARNING/ERROR, 默认 INFO) |

所有操作 (配置管理、服务启动、健康检查、session 维护) 均通过命令行子命令完成, 无需图形界面或终端 UI 框架。

---

## 9. 目录结构规划

```
MailCode/
├── mailcode/                           # 包目录
│   ├── __init__.py
│   ├── cli.py                     # 统一 CLI 入口 + 命令路由
│   ├── config.py                  # 配置加载 / 预检 / 合并默认值
│   ├── health.py                  # 连通性检查 (SMTP + IMAP)
│   ├── provider_presets.py        # 邮件服务商预设 (SMTP/IMAP 默认值 + 域名检测)
│   ├── server.py                  # 监听服务入口 (IMAPListener 生命周期 + 信号处理)
│   ├── session_cli.py             # session 子命令的 CLI 呈现
│   ├── channels/
│   │   └── email_channel.py       # EmailChannel 类 (SMTP 发送 + Message-ID 生成)
│   ├── relay/
│   │   ├── email_listener.py       # IMAPListener (邮件轮询 / IDLE / 4 道关 / 路由)
│   │   ├── security.py             # SecurityChecker (白名单 + Auth-Results)
│   │   ├── conversation_handler.py # ConversationHandler (session 路由 + claude -p)
│   │   └── stateless_handler.py    # StatelessHandler (单次回复 fallback)
│   ├── utils/
│   │   └── logging.py             # 结构化日志 (RotatingFileHandler + stderr)
│   └── resources/
│       └── default.json           # 默认配置模板
├── build.sh                       # 构建脚本 (python -m build → dist/)
├── install.sh                     # 一键安装脚本
├── prepare.sh                     # 开发环境准备
├── release.sh                     # 发布脚本
├── uninstall.sh                   # 卸载脚本
├── docs/
│   ├── design-final/
│   │   └── design.md              # 本文档
│   └── plans/
│       └── ...
├── tests/
│   ├── unit/                      # 单元测试
│   ├── run_tests.sh               # 测试运行脚本
│   ├── pyproject.toml             # 包配置
│   └── requirements-dev.txt       # 开发依赖
```

**构建说明**:
- 构建: `bash build.sh` → `dist/` 目录生成 `.whl` 安装包
- 安装: `bash install.sh --local dist/mailcode-*.whl`
- 包内资源文件 (`resources/`) 通过 `importlib.resources.files("mailcode")` 加载并打包到 wheel 中
- 运行时数据目录统一在 `~/.config/mailcode/` 下 (含 config 和 data)
- 纯 Python 实现, 零第三方运行时依赖 (仅需系统安装 `claude` CLI)

---

## 10. 关键技术依赖

### Python 标准库 (零第三方依赖)

| 模块 | 用途 |
|------|------|
| `imaplib` | IMAP 协议客户端 (IDLE 长连接) |
| `smtplib` | SMTP 协议客户端 (SMTP_SSL / STARTTLS) |
| `email` | MIME 邮件解析与构造 (含 `email.utils.make_msgid`) |
| `subprocess` | 调用 `claude -p` (含 `TimeoutExpired`) |
| `json` | session / index / state / config 持久化 |
| `threading` | IDLE 长连接并发 (`_wait_for_idle`) |
| `re` | Auth-Results 解析 / cwd 提取 / 正文清理 |
| `uuid` | session_id 生成 (`uuid4().hex[:12]`) |
| `pathlib` | 路径处理 (cwd 展开, is_dir 校验) |
| `logging` + `logging.handlers` | 结构化日志 (RotatingFileHandler) |
| `importlib.resources` | 加载包内默认配置 |

### 外部工具

- `claude` — Claude Code CLI (必需, 通过 `claude -p ... --dangerously-skip-permissions` 调用)
- `launchd` / `systemd` — 系统服务管理 (macOS / Linux 守护进程, 可选)
