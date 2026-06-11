# MailCode

Python 邮件连接器，通过邮件远程操控 Claude Code。

```
收件箱 ──> IMAP 监听器 ──> claude -p 子进程 ──> SMTP 邮件通知
```

## 设计理念

MailCode 的核心理念是**轻量化的人与 Coding Agent 直连**。

市面上的 AI 工具链往往依赖飞书、钉钉等重型协作平台——建机器人、配 Webhook、在对话框里和 Agent 来回聊天。MailCode 反其道而行：直接用邮件，因为你本来就有一个邮箱。

**人与 Agent 直连，而不是和机器人对话。** 回复邮件就是下达指令，收件箱就是控制台,不需要打开任何第三方应用。

**轻量异步。** 不需要常驻复杂服务，不需要数据库，不需要消息队列。一个 Python 脚本 + 邮件协议，跑在任何能联网的机器上。Agent 在后台慢慢跑，你做别的事，完事了邮件通知你。

MailCode 不做大而全的平台，只做一件事：**让你用最习惯的方式（邮件）和  Agent 对话。**

## 邮箱账户架构

MailCode 需要 **两个邮箱账户**——一个当 Bot，一个当用户：

- **Bot 邮箱**（例：`mailcode_bot@xxx.com`）—— MailCode 监听它的收件箱，Claude 处理完任务后通过它把结果发回给你
- **用户邮箱**（你的私人邮箱，例：`your@qq.com`）—— 你从这个邮箱向 Bot 邮箱发邮件下达指令

工作流：

```
[用户邮箱]  ──发指令邮件──▶  [Bot 邮箱收件箱]
  your@qq.com                  mailcode_bot@xxx.com
                                      │
                                      ▼
                                 IMAP 监听
                                      │
                                      ▼
                                 Claude 处理
                                      │
                                      ▼
  [用户邮箱]  ◀──回复邮件──  [Bot 邮箱发件箱]
```

> **为什么是 Bot 邮箱而不是你自己的主邮箱？** MailCode 要登录一个邮箱才能收信和发信，所以需要一个专用 Bot 邮箱；它和你日常使用的邮箱是分开的，配置 `allowed_senders` 限制只有你自己的私人邮箱能给它发指令。

## 安装

### 系统依赖

- **python3**（≥3.9）
- **Claude Code**（`claude` 命令需在 `PATH` 中）

Python 零第三方依赖，全部使用标准库（`imaplib`、`smtplib`、`email`、`subprocess`、`json`、`secrets` 等）。

### pip 安装

```bash
pip install mailcode
```

### 源码安装

```bash
git clone <repo-url> && cd MailCode
bash install.sh
```

`install.sh` 自动完成：安装 mailcode 包、初始化配置、创建 `~/.mailcode` 软链接、自动添加 PATH。

从本地 wheel 安装：`bash install.sh --local dist/mailcode-*.whl`

## 配置

编辑 `~/.config/mailcode/config.json`，必填字段。**两个邮箱要分清楚**——`mailcode_bot.email` 是 Bot 邮箱，`security.allowed_senders` 是允许给它发指令的邮箱（通常是你自己的私人邮箱）：

```jsonc
{
  "mailcode_bot": {
    "email": "mailcode_bot@xxx.com",    // ← Bot 邮箱：MailCode 登录此邮箱收信/回信
    "password": "Bot 邮箱授权码",          // ← Bot 邮箱的授权码，不是登录密码
    "check_interval": 60                  // ← 轮询间隔(秒); 163/126 推荐 60-120
  },
  "security": {
    "allowed_senders": ["your@qq.com"]    // ← 允许发指令的邮箱（你的私人邮箱）
  }
}
```

SMTP 和 IMAP 配置由系统根据 Bot 邮箱的域名自动识别。支持：QQ 邮箱、163/126 邮箱、Gmail、Outlook/Hotmail。

如需手动覆盖 SMTP/IMAP（如自建邮箱），可添加 `smtp` / `imap` 段，手动设置的值会覆盖自动识别结果。

> 授权码获取：QQ 邮箱 → 设置 → 账户 → POP3/IMAP → 生成授权码；Gmail → Google 账户 → 安全性 → 应用密码。

## 使用

### CLI 概览

| 子命令 | 用途 |
|--------|------|
| `mailcode serve` | 启动 IMAP 监听中继 |
| `mailcode config <动作>` | 配置管理（`show` / `init` / `init-test` / `path` / `validate`）|
| `mailcode health` | 邮件连通性检查（SMTP/IMAP）|
| `mailcode session <动作>` | 会话管理（`list` / `show` / `delete` / `cleanup`）|
| `mailcode --version` | 显示版本号 |

### 启动中继

```bash
# 前台运行（默认 IMAP IDLE 长连接, 单连接撑全场, 实时收信）
mailcode serve

# 干跑模式（仅打印邮件, 不调用 claude）
mailcode serve --dry-run

# 强制走轮询（不用 IDLE; 部分老旧邮箱要求）
mailcode serve --no-idle

# 单次轮询后退出
mailcode serve --once
```

**IMAP IDLE 支持按邮箱而异**——MailCode 启动时检测 `IMAP CAPABILITY`, 没有 IDLE 就自动回退到轮询:

| 邮箱 | IDLE | 行为 | 推荐 `check_interval` |
|------|------|------|----------------------|
| QQ 邮箱 (`imap.qq.com`) | ✅ | 实时推送, 秒级响应 | 60s（轮询时）|
| 163/126 邮箱 (`imap.163.com` / `imap.126.com`) | ❌ | 自动回退到轮询, warning 日志告知 | **60-120s**（频率过高会被反滥用限速, 严重时封 IP）|
| Gmail / Outlook | ✅ | 实时推送 | 60s（轮询时）|

网易系邮箱**不支持 IDLE 扩展**, 频繁 IMAP 登录会触发反滥用。Bot 邮箱若用 163/126, 务必把 `mailcode_bot.check_interval` 调到 60-120 秒, 否则几小时内可能被临时封禁。

查看日志：

```bash
tail -f ~/.config/mailcode/relay.log
```

### 配置管理

```bash
mailcode config show          # 查看当前配置（密码脱敏）
mailcode config path          # 显示配置文件路径
mailcode config init          # 初始化配置（已存在则跳过）
mailcode config init --force  # 强制重新生成
mailcode config validate      # 校验配置完整性
```

### 会话管理

MailCode 默认按邮件主题维护多轮对话; 如需单次回复模式请设 `session.enabled = false`。

```bash
mailcode session list                # 列出所有 session
mailcode session show <session_id>   # 查看单个 session 详情
mailcode session delete <session_id> # 删除 session
mailcode session cleanup             # 按 TTL 清理过期 session
mailcode session cleanup --dry-run   # 仅预览，不实际删除
```

### 工作目录 (cwd 指令)

在邮件正文**第一行**写 `cwd: <path>`，Claude 子进程会在该目录启动——适合「让 Claude 操作指定项目」。Session 模式下 cwd **粘性**：同一 session 内的后续邮件会沿用该目录，直到新邮件重新指定。

```
cwd: ~/Projects/my-app
帮我看看 src/auth.py 里那段 JWT 校验逻辑
```

**路径解析规则**：

- `~` / `~/foo` 走用户目录展开
- 相对路径（`./foo`、`foo`）以 `Path.cwd()` 为基准
- 路径必须存在且是目录（`is_dir()` 校验），否则忽略并回退默认（`$HOME`）
- 写法不区分大小写，`Cwd:` / `CWD:` 等价

**两种模式差异**：

- **Session 模式**（`session.enabled = true` 默认）：cwd 粘性，整个 session 沿用；`mailcode session show <id>` 可查当前 cwd
- **单次回复模式**（`session.enabled = false`）：cwd 不粘性，每封邮件独立解析

cwd 行会在调用 Claude 前从 body 中剥离，不会污染 prompt。

### 健康检查

```bash
mailcode health    # 检查 SMTP/IMAP 配置与连通性
```

检查项：SMTP 连接 / 登录 / 发信、IMAP 连接 / 登录 / 收件箱。
