# Design Plan: MailCode 交互改进

## 背景

MailCode 当前使用 session 文件（`session_<id>.json` + `index.json`）模拟对话上下文，每次调用 `claude -p` 都启动新进程，Claude 需要 Read session 文件才能"回忆"上下文。浪费 token、维护复杂、易损坏。

### 当前架构的问题

1. **上下文管理错位**：MailCode 负责拼 prompt、管理历史文件，Claude 每次重新读取。本应 Claude 原生管理的上下文，变成由 MailCode 手搓文件系统实现。
2. **每次调用"冷启动"**：`claude -p` 每次启动新进程，没有增量上下文。Claude 需要从头 Read session 文件，大 session 下 token 浪费严重。
3. **session 文件复杂**：`session_<id>.json` 记录了 incoming/outgoing 邮件两条方向，且 Msg-Id 在 IMAP 层不向下透传，导致 `msg_id: ""` 空值普遍存在，index 同步复杂。
4. **无实时反馈**：CLI 只能事后 `mailcode session list` 查看状态，没有实时事件流。调试依赖翻日志。
5. **邮件体验粗糙**：用户收到回复邮件时，无上下文脚注、无系统命令支持（如 `/help`、`/status`）、长时间处理无已收悉通知。

## 目标

1. 改用 `claude --session-id` / `--resume` 让 Claude 原生管理上下文
2. 保留 transcript 作为邮件存档（不喂给 Claude）
3. 邮件交互增强（会话脚注、系统命令、已收悉通知）
4. CLI 体验提升（实时事件流、chat 模式、session 管理增强）
5. 快速修复已知 bug

## 架构

### 三层存储

```
映射文件 (claude_sessions.json)    → msg-id → Claude UUID 查找表
   ↓
Transcript 文件 (transcripts/<uuid>.json) → 邮件记录存档
   ↓
Claude 内部状态                     → AI 上下文（Claude 原生管理）
```

### 数据流（新 vs 旧）

**Old**:
```
email → session_*.json → prompt("请读文件") → claude -p → 写回 session → SMTP
```

**New**:
```
email → 查映射 → claude --resume -p "正文" → 追加 transcript → SMTP
```

### 组件交互

```
┌─────────────────────────────────────────────────────────┐
│                     email_listener.py                     │
│   收邮件 → 安全检查 → 提取 in_reply_to → 路由到 Handler    │
└──────────┬─────────────────────────────────────┬──────────┘
           └─────────┐ ┌──────────────────────────┘
                     ▼ ▼
           ┌─────────────────────┐
           │   ResumeHandler      │  ← NEW: resume_handler.py
           │                     │
           │ 1. 查映射文件         │
           │ 2. 映射存在 → resume  │
           │ 3. 映射不存在 → 新建   │
           │ 4. 追加 transcript    │
           │ 5. (可选) 脚注注入     │
           └─────────┬───────────┘
                     │
                     ▼
           ┌─────────────────────┐
           │  claude_runner.py    │
           │  --session-id / resume │
           └─────────────────────┘
```

## 关键设计决策

### 1. 向后兼容

- 旧 `ConversationHandler` 保留不动，通过 `--handler resume` 参数切换
- 默认 handler 保持不变（避免破坏现有用户），新用户可通过配置或 CLI 参数 opt-in
- `index.json` + `session_*.json` 格式与 `claude_sessions.json` + `transcripts/` 格式共存，互不干扰

### 2. 映射文件

- 单 JSON 文件：`~/.config/mailcode/claude_sessions.json`
- 结构：`threads` 字典，key 为 email `msg_id`（去尖括号的裸 ID），value 为 Claude session UUID
- 每次 resume 时查表，新建时写表

```json
{
  "version": 1,
  "updated_at": 1717500000.0,
  "threads": {
    "<原邮件 msg_id>": "<claude-session-uuid>",
    "<回复邮件 msg_id>": "<claude-session-uuid>"
  }
}
```

### 3. Transcript

- 按 `claude_session_id` 命名的独立文件，纯 append
- 目录：`~/.config/mailcode/transcripts/<claude_session_uuid>.json`
- 仅记录邮件方向、时间、摘要（不做完整 prompt），纯存档用途
- Claude 不读 transcript 文件，只读 Claude 内部状态

```json
{
  "claude_session_id": "<uuid>",
  "created_at": 1717500000.0,
  "entries": [
    {
      "direction": "incoming",
      "from": "user@example.com",
      "subject": "项目咨询",
      "body_preview": "你好, 我想咨询...",
      "timestamp": 1717500000.0
    },
    {
      "direction": "outgoing",
      "to": "user@example.com",
      "subject": "Re: 项目咨询",
      "response_length": 1024,
      "timestamp": 1717500100.0
    }
  ]
}
```

### 4. 会话脚注

- 在每次邮件正文末尾自动追加标准脚注，帮助用户理解当前会话状态
- 格式：

```
---
会话 ID: <12 位 hex>
消息计数: N
工作目录: /path/to/cwd
输入 /help 查看可用命令
```

- 通过 `--handler resume` 启用，默认关闭
- 可配置开关 `session.footnote: true/false`

### 5. 系统命令

- 在 `process_email()` 入口路由，不经 Claude
- 目前支持：
  - `/help` — 返回可用命令列表
  - `/status` — 返回当前会话状态（msg count、cwd、uptime）
  - `/cancel` — 放弃当前未完成的 Claude 调用（若支持）
- 系统命令路由在 `ResumeHandler` 中实现，方法签名：

```python
def _try_system_command(self, body: str) -> Optional[str]:
    """检查是否是系统命令。是则返回响应文本，否则返回 None。"""
```

### 6. 已收悉通知

- 对于估计处理时间超过 30s 的请求，在收到邮件后立即发送"已收悉，正在处理..."邮件
- 仅在 `--handler resume` 模式下生效，因为旧 handler 没有超时估算能力
- 实现：在 `handle_email()` 开头检查当前 Claude session 是否繁忙，是则触发通知

### 7. CLI 事件流

- `mailcode serve --events` 参数，启动后实时打印事件
- 事件通过回调函数从 `email_listener` 透传到 `server.py`，再到 stdout
- 事件类型：
  - `email.received` — 收到新邮件
  - `session.resumed` — 续接 Claude session
  - `session.created` — 新建 Claude session
  - `claude.started` — Claude 开始处理
  - `claude.finished` — Claude 返回回复
  - `email.sent` — 回复邮件已发送
  - `error.*` — 各类错误

### 8. Chat 模式

- `mailcode chat` — 交互式对话模式，不走 IMAP，直接命令行输入
- 与 ResumeHandler 集成，自动维持 Claude session
- 支持会话列表、切换、查看历史

## 波及文件

### Phase 1: 核心 ResumeHandler + Claude Runner 改造

| 文件 | 操作 | 变更说明 |
|------|------|----------|
| `mailcode/relay/resume_handler.py` | **NEW** | ResumeHandler 类：映射文件管理 + transcript 存档 + claude --session-id 调用 |
| `mailcode/utils/claude_runner.py` | MODIFY | 新增 `call_claude_with_session(session_id, prompt, cwd)` 接口，支持 `--resume` 参数 |
| `mailcode/relay/email_listener.py` | MODIFY | `process_email()` 新增 handler 路由分支，支持 `ResumeHandler` |
| `mailcode/server.py` | MODIFY | `run_serve()` 解析 `--handler` 参数，传递给 `IMAPListener` |
| `mailcode/cli.py` | MODIFY | `serve` 子命令添加 `--handler resume` 参数；`_build_session_handler()` 支持 ResumeHandler |

### Phase 2: 邮件交互增强

| 文件 | 操作 | 变更说明 |
|------|------|----------|
| `mailcode/relay/resume_handler.py` | MODIFY | 添加脚注注入、`_try_system_command()` 路由、超时已收悉通知 |
| `mailcode/relay/email_listener.py` | MODIFY | 确认通知 SMTP 调用 |

### Phase 3: CLI 体验提升

| 文件 | 操作 | 变更说明 |
|------|------|----------|
| `mailcode/server.py` | MODIFY | 事件回调注册，`--events` 参数 |
| `mailcode/relay/email_listener.py` | MODIFY | `on_event` 回调注入，调用方自定义处理 |
| `mailcode/cli_chat.py` | **NEW** | 交互式 chat 模式，不走 IMAP |
| `mailcode/cli.py` | MODIFY | 注册 `chat` 子命令 |
| `mailcode/session_cli.py` | MODIFY | 增强：`--wide` 宽格式、`--filter` 按状态过滤、stats 统计 |
| `mailcode/relay/conversation_handler.py` | MODIFY | 新增 `get_session_stats()` 方法 |

### Phase 4: 已知 Bug 修复

| 文件 | 操作 | 变更说明 |
|------|------|----------|
| `mailcode/cli.py` | MODIFY | `cmd_config` 异常路径空指针保护 |
| `mailcode/health.py` | MODIFY | IMAP 断开后拉起重试不 panic |
| `mailcode/config.py` | MODIFY | `session_ttl_days` 配置项 0 值处理 |
| `mailcode/relay/email_listener.py` | MODIFY | `_clean_body()` Unicode 截断兼容性修复 |
| `install.sh` | MODIFY | 路径硬编码修复 |

## Phase 1 详细设计

### ResumeHandler 类设计

```python
class ResumeHandler:
    """基于 claude --resume 的对话处理。

    核心思路：不维护 session 文件中的"对话上下文"，
    而是由 Claude 内部状态管理上下文。MailCode 只做：
      1. 映射：email msg_id → Claude session UUID
      2. 存档：transcript 记录（纯归档，不参与推理）
      3. 转发：正文传给 `claude --resume -p "..."`

    数据目录结构：
      ~/.config/mailcode/
        claude_sessions.json        # msg_id → UUID 映射
        transcripts/                # 存档目录
          <uuid>.json               # 按 Claude session UUID 命名的 transcript
    """

    def __init__(self, email_channel, enable_footnote=True):
        self.email_channel = email_channel
        self.enable_footnote = enable_footnote
        self._ensure_dirs()

    # ── 目录 / 文件管理 ──

    def _ensure_dirs(self):
        """确保映射文件和 transcripts 目录存在。"""

    def _mapping_path(self) -> Path:
        """返回 claude_sessions.json 的路径。"""

    def _transcript_dir(self) -> Path:
        """返回 transcripts/ 目录路径。"""

    def _transcript_path(self, claude_session_id: str) -> Path:
        """返回某个 Claude session 的 transcript 文件路径。"""

    # ── 映射文件读写 ──

    def _load_mapping(self) -> dict:
        """加载 claude_sessions.json。损坏时返回空字典 + warn。"""

    def _save_mapping(self, mapping: dict):
        """原子写 claude_sessions.json。"""

    def _find_session(self, msg_id: str) -> Optional[str]:
        """通过 msg_id 查找 Claude session UUID。"""

    def _save_mapping_entry(self, msg_id: str, claude_session_id: str):
        """保存 msg_id → Claude session UUID 映射。"""

    # ── Transcript 管理 ──

    def _append_transcript(self, claude_session_id: str, entry: dict):
        """追加单条记录到 transcript 文件。文件不存在则创建。"""

    # ── 系统命令 ──

    def _try_system_command(self, body: str) -> Optional[str]:
        """处理系统命令。命中则返回回复文本，否则返回 None。"""

    # ── 脚注 ──

    def _build_footnote(self, claude_session_id: str, transcript_entry_count: int) -> str:
        """构建邮件正文脚注。"""

    # ── 主入口 ──

    def handle_email(self, from_email: str, subject: str, body: str,
                     references: str = "", in_reply_to: str = "") -> bool:
        """处理一封对话邮件（使用 claude --resume）。

        流程:
          1. 检查系统命令（/help, /status 等直接回复，不经 Claude）
          2. 通过 in_reply_to 查映射，找到 Claude session UUID
          3. 未找到 → 新建 Claude session（claude --session-id 或自动创建）
          4. 找到 → 复用（claude --resume）
          5. 追加 transcript
          6. 可选：追加脚注
          7. SMTP 发回复
          8. 更新映射文件
        """
```

### Claude Runner 改造

```python
# mailcode/utils/claude_runner.py (新增函数)

def get_claude_session(prompt: str, cwd: str = "",
                       resume_session_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """调用 claude 并返回 (response, session_id)。

    使用 `claude -p` 时无法获取 session_id。
    改用 `claude --session-id <id>` + `--resume` 搭配。

    Args:
        prompt: 传给 Claude 的 prompt
        cwd: 工作目录
        resume_session_id: 续接已有 session 的 UUID，None 则新建

    Returns:
        (response: str or None, session_id: str or None)
    """
```

### IMAPListener 改造

```python
# mailcode/relay/email_listener.py (新增 Handler 路由)

class IMAPListener:
    def __init__(self, ..., handler_mode: str = "conversation", ...):
        # handler_mode: "conversation" | "stateless" | "resume"
        self.handler_mode = handler_mode
        self._resume_handler: Optional["ResumeHandler"] = None
        ...

    def process_email(self, email_entry, ...):
        if self.handler_mode == "resume":
            return self._handle_via_resume(email_entry)
        # 原有路由逻辑不变
        ...

    def _handle_via_resume(self, email_entry) -> Tuple[bool, str]:
        """路由到 ResumeHandler。"""
```

## Phase 2 详细设计

### 会话脚注

脚注格式标准化：

```
---
会话 ID: a1b2c3d4e5f6
消息: 3
工作目录: /home/user/project
帮助: 回复 /help 查看可用命令
```

- `ResumeHandler._build_footnote()` 在 outgoing 邮件正文末尾追加
- 可通过 `session.footnote: false` 配置关闭
- 仅在 `--handler resume` 模式下生效

### 系统命令

路由逻辑：

```python
_SYSTEM_COMMANDS = {
    "/help":    "MailCode 可用命令:\n"
                "  /help     - 显示此帮助\n"
                "  /status   - 查看当前会话状态\n"
                "  /cancel   - 取消当前处理\n"
                "\n直接回复正文即可与 AI 助手对话。",
    "/status":  None,  # 动态生成
}

def _try_system_command(self, body: str) -> Optional[str]:
    """检查 body 是否为系统命令。是则返回回复文本。"""
    if not body:
        return None
    cmd = body.strip().split("\n")[0].strip().lower()
    if cmd not in _SYSTEM_COMMANDS:
        return None
    if cmd == "/status":
        return self._build_status_response()
    return _SYSTEM_COMMANDS[cmd]
```

### 已收悉通知

- 位置：`ResumeHandler.handle_email()` 中，调用 `claude --resume` 之前
- 条件：当前没有可复用的已缓存 Claude session（即每次都是新进程）
- 触发：SMTP 发送"已收悉，正在处理中..."邮件
- 防止重复：维护一个 `_acknowledged` 集合（session_id → bool），同 session 第二次邮件不再重复通知

## Phase 3 详细设计

### CLI 事件流

```python
# server.py

def run_serve(args):
    ...
    listener = IMAPListener(handler_mode=args.handler)

    if args.events:
        listener.on_event = lambda event: print(
            f"[{event['type']}] {event.get('message', '')}"
        )

    listener.listen(...)
```

事件格式：

```python
{
    "type": "email.received",
    "timestamp": 1717500000.0,
    "data": {
        "from": "user@example.com",
        "subject": "Hello"
    }
}
```

### Chat 模式

- `mailcode chat` 启动交互式 REPL
- 类似 `python3 -m asyncio` 风格，输入一行就发给 Claude
- 支持 `/sessions` 列出所有 Claude session，`/switch <id>` 切换
- 底层复用 ResumeHandler，不走 IMAP/SMTP
- 依赖 `--handler resume` 模式

### Session CLI 增强

- `mailcode session list --wide` — 宽格式，显示完整 subject/from 不截断
- `mailcode session list --filter active|expired|all` — 按状态过滤
- `mailcode session stats` — 统计：总 session 数、总消息数、平均寿命、最活跃 session 等

## Phase 4 Bug 修复清单

1. **`cli.py` 空指针**：`cmd_config` 中 `get_smtp_config()` / `get_imap_config()` 在配置不完整时报 `KeyError`，需加 try/except 兜底返回空字典
2. **`health.py` panic**：IMAP 连接测试中 `mail.logout()` 调用时若 `mail` 为 None 会抛 `AttributeError`，需加 None 检查
3. **`config.py` TTL 0 值**：`session_ttl_days=0` 表示"不清理"，但当前代码中 `int(0)` 被视为 falsy 导致不清理逻辑正确，但验证器误报警告。需调整验证逻辑
4. **`email_listener.py` Unicode 截断**：`_clean_body()` 中 `body[:500]` 在日志输出时可能截断多字节 UTF-8 字符导致 `UnicodeDecodeError`。改用 `body.encode('utf-8')[:500].decode('utf-8', errors='replace')`
5. **`install.sh` 路径硬编码**：`pip install` 中 `./` 前缀在非 repo 根目录运行时失效。改用 `$(dirname "$0")` 定位脚本所在目录

## 测试策略

### 单元测试

- `tests/unit/test_resume_handler.py` — 覆盖 ResumeHandler 全部方法
  - 映射文件读写（创建、更新、损坏恢复）
  - transcript 追加（新建文件、追加已有文件）
  - 系统命令路由（/help、/status、未知命令）
  - 脚注生成（启用/禁用、格式验证）
  - 已收悉通知逻辑（触发条件、去重）
  - `handle_email` 主流程（新建 session、续接 session、Claude 失败）

- `tests/unit/test_claude_runner.py` — 覆盖新增的 `get_claude_session()`
  - `--resume` 参数传递
  - session_id 回传解析
  - 超时/错误处理

### 集成测试

- `tests/integration/test_resume_handler.py` — 端到端测试（mock IMAP/SMTP）
- 验证新旧 handler 切换不破坏现有流程
- 验证 `--handler resume` 下邮件收到回复

### 手动验证

- `mailcode serve --once --dry-run --handler resume` 走通全流程
- 真实发邮件验证脚注、系统命令、已收悉通知

### 验收标准

- [ ] ResumeHandler 单元测试全部通过
- [ ] ResumeHandler + ConversationHandler 完全独立，互不影响
- [ ] `--handler resume` 模式下邮件能正确收到 Claude 回复
- [ ] 会话脚注在回复邮件末尾正确显示
- [ ] 系统命令 /help、/status 返回正确内容（不经 Claude）
- [ ] 长时间处理时用户收到"已收悉"通知邮件
- [ ] `mailcode chat` 能交互式与 Claude 对话并切换 session
- [ ] `mailcode serve --events` 实时打印事件流
- [ ] 全部 Phase 4 Bug 修复已合入，无回归
- [ ] 现有 ConversationHandler 测试全部通过（向后兼容）
- [ ] `ruff check` 无新增 warning

## 风险与注意事项

1. **`claude --session-id` / `--resume` 可用性**：当前 Claude Code CLI 是否暴露这些参数需确认。如果不可用，备选方案是通过 `--append-system-prompt` + 自定义 session 文件路径来模拟（退化方案）
2. **claude session UUID 获取**：新建 session 后需要从 claude 输出中解析 session_id。如果 claude 不输出此信息，需通过文件系统扫描 `~/.claude/sessions/` 目录按时间戳获取（脆弱，但有 fallback）
3. **session 泄漏**：如果不显式关闭 Claude session，长期运行可能导致 Claude 内部状态膨胀。需实现 session 闲置 TTL 自动关闭（通过 `conversation_handler` 现有的 TTL 清理机制）
4. **向后兼容数据**：旧 `session_*.json` 文件不会被 ResumeHandler 读取。如果用户切换 handler，旧对话不可续接。需在文档中说明此限制
5. **并发冲突**：多个邮件同时到达时，同一 msg_id 的映射条目可能被覆盖。映射文件写操作需加文件锁（`fcntl.flock` 或 `portalocker` 备选）
6. **claude 子进程管理**：`claude --resume` 可能长时间运行，需确保超时机制覆盖此场景。当前 `CLAUDE_TIMEOUT_SECONDS = 86400（24h）` 对 resume 模式同样适用
7. **路径安全**：`cwd` 指令提取逻辑与现有 `extract_cwd()` 复用，注入风险与现有 conversation_handler 一致，不做额外限制
