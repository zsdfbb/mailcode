# 支持多 Coding Agent — 架构上下文

> 状态: **架构探索阶段**（arch-explore）。
> 目的: 在动手前澄清"支持多 coding agent"到底意味着什么、现有架构哪些边界要动、范围与未澄清问题。
> 写于 2026-09-04。
>
> **演进**: 初稿聚焦"加 Pi"，根据反馈扩为"支持多 agent"（Claude Code / Pi / 未来更多）。**OpenCode v1 不实现**（用户 2026-09-04 决定, 见 design.md）。OpenCode 仍作为 v2 候选保留：未来加 OpenCodeRunner 时按 ADR-001 的"v2 流程"——1 新文件 + 1 行注册即可。

---

## 概述

把 MailCode 从"Claude Code 单一 agent"改造为"可插拔的多 agent 架构"，让用户可在配置中选择 Claude Code / Pi 中的一个（或在不同 schedule / session 上用不同 agent），邮件依然通过 IMAP/SMTP 中转。

**v1 范围**：两种 agent 跑通 + 抽象 + 测试分层。
- **Claude Code** (`claude`，Anthropic) — 默认
- **Pi** (`pi`，`@mariozechner/pi-coding-agent`，开源)

**v2 候选（v1 不实现）**: OpenCode / aider / gemini-cli / Codex CLI 等。抽象 (ADR-001) 设计支持 v2 零成本新增。

详见 §"外部依赖"。

---

## 现有架构

### 模块边界

MailCode 是"邮件 ↔ 本地 AI Agent"的中转管道。对外接口有两个：
- **CLI**（`mailcode` 子命令: `serve` / `chat` / `schedule` / `session` / `state` / `health` / `config`）
- **配置文件**（`~/.config/mailcode/config.json`）

调用方只有一类：**本地用户**通过 IMAP 收件箱 + SMTP 发件箱接进。

### 核心抽象

**当前没有 Agent 抽象层。** 整个运行时把 Claude Code 当成唯一 agent：

| 抽象 | 文件 | 说明 |
|------|------|------|
| 子进程包装 | `mailcode/utils/claude_runner.py:20-89` | `call_claude()` 硬编码 `["claude", "--dangerously-skip-permissions"]` |
| 多轮对话（per-file） | `mailcode/relay/conversation_handler.py` | 自维护 `session_<id>.json` 文件，prompt 让 Claude 用 `Read` 工具自助读 |
| 多轮对话（resume） | `mailcode/relay/resume_handler.py` | 用 `claude --session-id <uuid> --resume`，由 Claude 自管 session |
| 单次回复 | `mailcode/relay/stateless_handler.py` | 一封邮件一次 `claude -p`，不写 session |
| 定时调度 | `mailcode/relay/scheduler.py:727` | `call_claude(prompt, cwd, timeout=...)` |
| 终端 REPL | `mailcode/cli_chat.py:52` | `call_claude(user_input, cwd=..., session_id=..., resume=...)` |

所有调用最终汇聚到 `mailcode.utils.claude_runner.call_claude` —— 这是**唯一的 agent 边界点**，是改造的最佳切入点。

### 关键数据流

```
邮件进来 → IMAPListener.process_email
            ├─ system command (status/help/sessions) → 直接回信, 不调 agent
            └─ 否则按 session 路由:
                ├─ session 关    → StatelessHandler.handle_email     → call_claude
                ├─ session 开 + resume 关  → ConversationHandler      → call_claude
                └─ session 开 + resume 开  → ResumeConversationHandler → call_claude (--session-id/--resume)

定时调度 → Scheduler._run_task  → call_claude
终端 REPL → cli_chat            → call_claude (--session-id/--resume)
```

所有 agent 调用都通过同一个 `call_claude(prompt, cwd, session_id?, resume?, timeout?)` 函数。

### 外部依赖

| 依赖 | 用途 | 当前耦合 | 备注 |
|------|------|---------|------|
| Claude Code CLI | 默认 agent | 深度硬编码 | 安装靠官方安装器 |
| OpenCode CLI | 历史支持但**运行时未接通**（死代码） | `email_listener.py` 还在检查 `X-OpenCode-Remote-Token`，但从未调过 `opencode` 子进程 | 见 `docs/exec-plans/completed/2026-05-31-default-agent-claude.md` |
| Pi CLI | **本次新增** | 不存在 | `npm i -g @mariozechner/pi-coding-agent` |
| Node.js 20+ | Claude / OpenCode / Pi 通用前置 | 间接 | 不算 MailCode 的依赖 |
| Python 3.x stdlib | MailCode 主体 | **零第三方运行时依赖** | 这是约束, 见下 |
| IMAP/SMTP 邮箱 | 邮件通道 | - | 与 agent 无关 |

### 三种 agent CLI 对照表

| 项 | Claude Code (`claude`) | OpenCode (`opencode`) | Pi (`pi`) |
|----|------------------------|----------------------|-----------|
| **安装** | 官方安装器 | 官方安装器（curl \| sh / brew） | `npm i -g @mariozechner/pi-coding-agent` |
| **非交互单轮** | `claude` (stdin 传 prompt，2026-06 改；旧 `--print`) | `opencode run "<prompt>"` (子命令模式) | `pi -p "<prompt>"` |
| **stdin 支持** | ✅ 当前已用 stdin | ✅ `Bun.stdin.text()` 非 TTY 时读 | ✅ `-p` 把 stdin 内容合并入 prompt |
| **session 续接** | `--session-id <uuid>` (新) / `--resume` (接最近) | `-s <session-id>` / `-c` (继续) / `--fork` | `--session <id>` / `-c` / `-r` (弹选择) |
| **session 路径** | `~/.claude/projects/<cwd-hash>/<uuid>.jsonl` (自管) | opencode 内置 db | `~/.pi/agent/sessions/<cwd-hash>/<uuid>.jsonl` |
| **session 自管** | MailCode 只传 UUID | MailCode 传 UUID, opencode 自存 | MailCode 只传 UUID, Pi 自存 |
| **权限跳过** | `--dangerously-skip-permissions` | `--auto` (旧 `--yolo` / `--dangerously-skip-permissions`) | `--approve` 或 `defaultProjectTrust` 配 settings |
| **多 provider/model** | 单一 (Claude) | `--model <provider/model>` / `-m` | `--provider <x> --model <x>` / `:thinking` |
| **JSON 事件流** | 不支持 | `--format json` (ndjson) | `--mode json` (ndjson) |
| **RPC 模式** | 不支持 | `opencode acp` (stdin/stdout ndjson) | `--mode rpc` (JSON-RPC) |
| **cwd 参数** | subprocess `cwd=` | `--dir <path>` (CLI 标志) | subprocess `cwd=` |
| **错误码语义** | `returncode != 0` → None | 同 | 同 |

---

## 约束

### 技术约束

- **零第三方运行时依赖**（CLAUDE.md / README / design.md 一致声明）。多 agent 支持仍要保持这个 — 三个 agent 都是子进程 CLI，MailCode 不 import 任何 agent SDK。
- **Node 20+ 前置**: 三个 agent 都需要 Node 20+，不增加新约束。
- **MailCode 仅 stdlib Python 3.x**, 不能引入 `httpx` / `aiohttp` / 任何第三方包。
- **运行时只调子进程**: MailCode 永远是 subprocess 调 agent CLI, 不引入 IPC / RPC。OpenCode 的 `--format json` 和 Pi 的 `--mode rpc` 在 v1 可以不考虑（over-engineering），仅在需要 streaming / tool telemetry 时才用。
- **历史 OpenCode 死代码要清理**: `email_listener._is_own_message` 还在检查 `X-OpenCode-Remote-Token`，但代码路径从未发过这个 header。本次顺手清掉（或统一用 `X-MailCode-Remote-Token`）。

### 性能约束

- 当前 `call_claude` 默认超时 24h 兜底, 各 handler 自己覆盖到 180s/1800s。
- 各 agent 启动延迟差异（OpenCode 启动稍重, Pi Node 冷启动 ~200-500ms），相对于邮件往返延迟（秒级）可忽略。
- 不要为支持多 agent 而在每封邮件增加额外 I/O（不引入"探测 agent 可用性"的额外 IMAP 往返）。

### 演进约束

- **向后兼容**: 现有用户所有配置（`config.json` / `schedules.json` / `claude_sessions.json` / `transcripts/` / `conversations/`）必须继续工作。
- **默认 agent = claude**（2026-05-31 决定, 见 `docs/design-plans/completed/2026-05-31-default-agent-claude.md`）。OpenCode / Pi 是可选, 不是默认。
- **多 agent 并存**: 一台机器可能要同时跑 claude + pi（不同邮件主题走不同 agent）。这意味着会话数据要按 agent 命名空间隔离。
- **不要重蹈 OpenCode 覆辙**: 历史上 `agent_type` 在设计文档里出现多次, 实际代码从未真正分派（`docs/exec-plans/completed/2026-05-31-default-agent-claude.md` 只是改了默认值, 没建抽象层）。这次要么真做抽象 + 测试覆盖，要么至少做到"显式分派 + 测试断言"，避免再次背债。
- **未来扩展**: v1 之外还可能加 aider / gemini-cli / Codex CLI 等。抽象要允许"加新 agent 只新增一个 runner 文件 + 注册"。

### 组织约束

- 维护者单人（CLAUDE.md / git author = Zhang Shuai）。
- 已有 47 个 unit tests + integration tests, 改造 `call_claude` 会触动多数测试, 需同步更新。
- 文档语言: CLAUDE.md / README.md / docs/* 中文, README.en.md 英文。
- **不在 v1 引入 entry_points / plugin 机制**（避免 v1 还没稳就过度设计）—— 用 module-level 注册表就够, 后续真有必要再升级。

---

## 需求范围

### 范围内

1. **新增 Agent 抽象 + 注册表**（核心）:
   - 新增 `mailcode/utils/agent.py`，定义 `AgentRunner` Protocol（用 `typing.Protocol` 鸭子类型, 不强制继承）:
     ```python
     class AgentRunner(Protocol):
         name: str  # "claude" / "opencode" / "pi"
         def is_available(self) -> bool: ...
         def call(self, prompt, cwd, *, session_id=None, resume=False,
                  no_session=False, timeout=None) -> Optional[str]: ...
         def hint_for_failure(self) -> str: ...  # 人类可读 hint
         def build_prompt_intro(self, session_path_or_id: str) -> str: ...
     ```
   - 注册表 `AGENTS: dict[str, AgentRunner]`，模块级 `get_runner(name: str) -> AgentRunner`。
   - 三个实现：`ClaudeRunner` / `OpenCodeRunner` / `PiRunner`，各放一个文件（`claude_runner.py` / `opencode_runner.py` / `pi_runner.py`）。
   - 共享参数：`prompt` / `cwd` / `session_id` / `resume` / `no_session` / `timeout`，与现有 `call_claude` 五参对齐。

2. **配置改成 `agents: {<name>: {<config>}}` + `default_agent`**:
   - `~/.config/mailcode/config.json` 顶层加：
     ```json
     {
       "default_agent": "claude",
       "agents": {
         "claude": {"command": "claude", "extra_args": ["--dangerously-skip-permissions"]},
         "opencode": {"command": "opencode", "extra_args": ["--auto"], "run_subcommand": true},
         "pi": {"command": "pi", "extra_args": ["--approve"]}
       }
     }
     ```
   - 兼容读法：旧的 `"agent": "pi"` 单字段也接受, 内部转成新结构。
   - `schedules.json` 的每个 task 加可选 `agent` 字段（默认继承 `default_agent`），允许单条 schedule 单独指定。
   - 不做"按邮件主题路由"（v1 不做）。

3. **5 个调用点改造**:
   - `ConversationHandler` / `ResumeConversationHandler` / `StatelessHandler` / `Scheduler` / `cli_chat`，统一走 `get_runner(agent_type).call(...)`。
   - `IMAPListener.process_email` 增加可选 `force_agent` 参数（CLI 调试用, 默认 None）。

4. **会话数据按 agent 命名空间**（关键, 影响向后兼容）:
   - `claude_sessions.json` → `~/.config/mailcode/sessions/claude/sessions.json`（按 agent 一级目录）。
   - `transcripts/<uuid>.json` → `transcripts/<agent>/<uuid>.json`。
   - `conversations/session_*.json` → `conversations/<agent>/session_*.json` + 索引文件对应迁。
   - **迁移策略**: 启动时检测到旧 `claude_sessions.json` / `transcripts/*.json` / `conversations/*.json` 时：
     - 一次性 INFO 日志提示（不阻塞）。
     - 自动迁移到 `<agent>/` 子目录（agent 字段缺失视为 `claude`）。
     - 提供 `mailcode config migrate-agents` 手动触发迁移（dry-run 默认, `--apply` 才改）。
     - 旧文件保留 30 天后清理（写 `~/.config/mailcode/legacy-agents-cleanup-marker.json` 标记时间戳）。

5. **错误 hint 抽到 Runner**:
   - `resume_handler.py:249` 现有的 Claude 特定 hint（"Claude Code 未安装..."）抽到 `ClaudeRunner.hint_for_failure()`。
   - OpenCode / Pi 各自带 hint（"opencode 未安装, 请访问..." / "pi 未安装, npm i -g @mariozechner/pi-coding-agent"）。

6. **CLI 表现**:
   - `mailcode config show` / `init` 包含 `default_agent` + `agents` 字段。
   - `mailcode session list/show/delete/cleanup/stats` 在输出里加一列 `agent`。
   - `mailcode schedule add` 加 `--agent {claude|opencode|pi}` 参数（用 `choices=AGENT_NAMES`，加新 agent 时只需一处维护）。
   - 新增 `mailcode agents list` 子命令, 列出已注册 agent + 当前是否可用（`shutil.which`）。
   - **不新增** `mailcode agent` 子命令（v1 不做 plugin 体系）。

7. **测试**:
   - 抽出 `Runner` Protocol 后, `tests/unit/conftest.py` 抽 `mock_runner(name)` fixture 通用 mock。
   - `tests/unit/test_claude_runner.py` 保留向后兼容测试（用 `ClaudeRunner` 包老 `call_claude`）。
   - 新增 `tests/unit/test_opencode_runner.py`、`test_pi_runner.py`。
   - 新增 `tests/unit/test_agent_registry.py`：测试 `get_runner` 找不到 name 时抛清晰错误, 注册表完整性等。
   - `tests/unit/test_conversation_handler.py` / `test_resume_handler.py` 等注入 mock runner, 不再直接 mock subprocess。

8. **文档**:
   - README.md / README.en.md "安装"段加 OpenCode / Pi 的安装指引。
   - CLAUDE.md 加新章节 `agent` 配置说明。
   - `docs/design-final/design.md` §34-56 加 "Agent 抽象层" 章节, 解释 Protocol 设计。
   - 加 `docs/references/multi-agent.md`（agent 矩阵：每个 agent 的 install / cmd / quirks 速查表）。

### 范围外（明确不做的）

- ❌ OpenCode `--format json` / Pi `--mode rpc` / Pi `--mode json`（v1 用各自的 print 模式 `-p` / `run` 就够, 不引入事件流解析）。
- ❌ 按邮件主题 / 发件人 / 关键词路由到不同 agent（v1 只做"全局 default_agent + 单条 schedule 可覆盖"）。
- ❌ Agent 的 extension / skill / theme 加载（让用户自己在 `~/.pi/agent/settings.json` / `.opencode.json` 配）。
- ❌ 多 agent 并行 / 负载均衡（v1 单 agent per task / per session）。
- ❌ **不做 entry_points / plugin 机制**（v1 用 module-level dict 注册；加新 agent = 新增 runner 文件 + 在 `agent.py` 注册一行；真有必要 v2 再升级到 entry_points）。
- ❌ 改造为异步（asyncio）— 当前 stdlib sync 调用保持不变。
- ❌ aider / gemini-cli / Codex CLI（v1 不实现, 但抽象要允许 v2 加, 见 §"扩展性"）。

---

## 关键场景

### 场景 1：默认用户（Claude-only，无感知升级）

1. 用户跑 `pip install -U mailcode` 升级到新版本。
2. 首次启动 `mailcode serve`，检测到旧 `claude_sessions.json` / `transcripts/*.json`。
3. 一次性 INFO 日志：`[migration] 检测到旧版 agent 数据, 自动迁移到 sessions/claude/... (dry-run)`
4. `mailcode config migrate-agents --apply` 或下次启动时（如果默认 auto-migrate）。
5. 迁移完成后, 旧路径软链接到新路径（30 天后清理）。
6. 之后所有行为不变：邮件进 → Claude 处理 → SMTP 回信。

### 场景 2：用户切到 Pi

1. 用户跑 `npm i -g @mariozechner/pi-coding-agent` 并 `pi /login` 完成 OAuth。
2. 编辑 `~/.config/mailcode/config.json`:
   ```json
   {"default_agent": "pi"}
   ```
   或更显式：
   ```json
   {
     "default_agent": "pi",
     "agents": {
       "pi": {"command": "pi", "extra_args": ["--approve"]}
     }
   }
   ```
3. 跑 `mailcode agents list` → 显示 `claude: 可用 / opencode: 缺失 / pi: 可用`。
4. 跑 `mailcode serve`，收邮件 → `get_runner("pi").call(...)` → `pi -p` 子进程。
5. `mailcode session list` 显示 `agent=pi`，归档在 `transcripts/pi/<uuid>.json`、`conversations/pi/session_*.json`。

### 场景 3：单条 schedule 走 OpenPi，其余走 Claude

```bash
mailcode schedule add daily-todo \
  --agent pi \
  --type daily --time 09:00 \
  --prompt "读 ~/TODO.md，挑最重要的 3 件事发给我" \
  --to-email me@example.com
```

调度器在 `tasks[i].agent = "pi"`, 触发时 `_run_task` 用 `get_runner("pi")`；其他 schedule 不指定 `agent` 时继承 `default_agent`。

### 场景 4：OpenCode 用作多 provider 切换点

```json
{
  "default_agent": "opencode",
  "agents": {
    "opencode": {
      "command": "opencode",
      "extra_args": ["--auto"],
      "default_model": "anthropic/claude-sonnet-4-5"
    }
  }
}
```

MailCode 把 `default_model` 透传给 OpenCode 的 `--model` / `-m`。要切到 GPT / Gemini 直接改配置。

### 场景 5：Agent 未安装

- 配置 `agent: pi`，但 `pi` 二进制不存在。
- `PiRunner.is_available()` 返回 False。
- `call()` 内部判可用性, 直接返回 None。
- 错误邮件里用 `PiRunner.hint_for_failure()` 输出："Pi CLI 未安装, 请 `npm i -g @mariozechner/pi-coding-agent` 后重试"（与现有 `resume_handler.py:249` 对称）。

### 场景 6：邮件主题路由到不同 agent（v1 不支持，仅留 hook）

- 用户希望"主题 A 用 claude, 主题 B 用 pi" — v1 明确不支持, 文档明确写。
- 留 hook 位：`IMAPListener.process_email` 增加 `force_agent` 参数, CLI 调试可用。
- v2 设计"per-thread agent" 时复用这个 hook。

---

## 关键差异点（三个 agent 需逐项决策）

| 项 | Claude 当前 | OpenCode | Pi | 处理决策 |
|----|------------|----------|-----|---------|
| 子进程参数 | `--dangerously-skip-permissions` | `--auto` (子命令模式 `opencode run`) | `--approve` | 每个 Runner 自己写死 extra_args, 配 `agents.<name>.extra_args` 可覆盖 |
| Session ID 格式 | mailcode 自管 UUID, 传给 `--session-id` | mailcode 自管 UUID, 传给 `-s` | mailcode 自管 UUID, 传给 `--session` | 统一：mailcode 生成 UUID, 用 `AgentRunner.build_session_args(uuid)` 抽象 |
| Resume 续接 | `--resume` (最近) | `-c` / `--continue` | `-c` / `--continue` | ResumeConversationHandler 显式传 `--session <mailcode_uuid>` 续接指定 session（语义统一） |
| cwd 隔离 | subprocess `cwd=` | `--dir <path>` (推荐, 而不是 subprocess cwd) | subprocess `cwd=` | 抽象层统一 `cwd` 参数, 各 Runner 自行决定用 flag 还是 subprocess 参数 |
| 多 provider / model | 单一 | `--model <provider/model>` | `--provider` + `--model` | `agents.<name>.default_model` 可选, runner 透传 |
| stdin vs argv | 走 stdin (已改) | 走 stdin (官方推荐, 非 TTY) | 走 stdin (-p 合并入 prompt) | 三个都走 stdin |
| node 版本前置 | 隐含 | 显式 | 显式 Node 20+ | 启动校验: 三个 agent 都先 `node --version`, 缺失或 < 20 warn |
| 反自循环 header | `X-MailCode-Remote-Token` / `X-OpenCode-Remote-Token` (后者死代码) | (同上) | (同上) | 统一用 `X-MailCode-Remote-Token`, 删除 `X-OpenCode-Remote-Token` 死代码 |
| 错误 hint | Claude 特定 | OpenCode 特定 | Pi 特定 | 抽 `AgentRunner.hint_for_failure()`, 各 Runner 实现自己的人类可读 hint |
| Session 持久化格式 | Claude 自管二进制 | OpenCode 内置 db | Pi 用 JSONL | mailcode 自己的 transcripts 仍用 JSON (人类可读), 与各 agent 内部格式独立 |

---

## 扩展性

抽象设计的**核心目标**之一：v2 加新 agent（aider / gemini-cli / Codex CLI 等）只需要做两件事：

1. 新建 `mailcode/utils/<agent>_runner.py`，实现 `AgentRunner` Protocol。
2. 在 `mailcode/utils/agent.py` 的 `AGENTS` 字典里加一行：`AGENTS["aider"] = AiderRunner()`。

无需改任何 handler、CLI、config schema（只需要用户在 config 里手动加 `agents.aider = {...}` 块；如果要让 mailcode config init 默认带出来, 那就在 `AGENTS` 注册的同时把 default config 块也导出）。

不引入 entry_points（避免 v1 复杂度）：

- v1: 模块级 dict, 集中注册。
- v2 (若需要): 升级到 `importlib.metadata.entry_points(group="mailcode.agents")`。

---

## 约束下的取舍建议

- **Runner 抽象用 Protocol 不用 ABC**: `typing.Protocol` 鸭子类型, 不强制继承, 测试 fixture 友好。注册表用一个 dict 就够, 没必要搞 entry_points / plugin 系统。
- **不改 `call` 函数签名**: 保持 `call(prompt, cwd, *, session_id=None, resume=False, no_session=False, timeout=None)` 七参, 三个 Runner 都实现同样签名（`no_session` 是 Pi 特有扩展参数, 其他 Runner 内部忽略）。
- **schedule / session 文档格式**: 给老数据做"无 agent 字段 = 默认 claude"的宽容读取, 写入时显式带上 `agent`。
- **不复活 OpenCode 死代码（除清理）以外的旧桥接层**: 历史上 OpenCode 走的是 tmux + capture-pane 路径（见 `tests/unit/conftest.py:113-126` 的 mock_tmux fixture 和 2026-06-03 tmux-removal plan）。当前 claude_runner 已经统一走 subprocess, OpenCode 也按 subprocess 模式实现, 不复活 tmux。
- **配置 schema 升级路径**: 兼容老的 `config.json` 没 `agents` 字段的情况 → 内部补 `default_agent="claude"` + `agents={"claude": default_config}`。`config show` 把补全后的值显示出来, 但写回磁盘不强制改用户文件。

---

## 未澄清问题

- [ ] **Q1: 三个 agent 的 session 行为能否统一？**
  - Claude: `--session-id <uuid>` → session 文件 = `<uuid>.jsonl`
  - OpenCode: `-s <session-id>` → 内置 db
  - Pi: `--session <id>` → session 文件 = `<id>.jsonl`
  MailCode 用统一 mailcode_uuid 传给三者, 让各自落到自己的 session 文件/db。问题: OpenCode 的 `-s` 接 UUID 是否能"用该 UUID 续接" (需要验证)。
  倾向: 三者都用 mailcode_uuid 作 session id, mailcode 自己的 `transcripts/<agent>/<uuid>.json` 是审计独立, 与 agent 内部 session 存储并存。

- [ ] **Q2: 配置 schema 选哪个？**
  选项 A: `default_agent: "claude"` + `agents: {claude: {...}, opencode: {...}, pi: {...}}`（推荐）。
  选项 B: 只 `agent: "claude"` 单字段（最简单, 但 per-schedule 覆盖要另开 schema）。
  选项 C: `agents: {claude: {default: true, ...}, pi: {...}, opencode: {...}}`（`default: true` 标记默认）。
  倾向 A：清晰、向后兼容（读老 config 没 `agents` 时按 A 补全）。

- [ ] **Q3: ResumeConversationHandler 在三 agent 下的"首封创建 vs 续接"参数是否一致？**
  Claude: `--session-id <uuid>` (开) / `--resume` (接)。
  OpenCode: `-s <uuid>` (开 + 接通用, 还是区分?)。
  Pi: `--session <uuid>` (开 + 接通用)。
  需要在 design 阶段分别原型验证三个 agent 的"创建新 session" 和 "续接指定 session" 在 CLI 上是否等价。
  如果 OpenCode 不能等价, 需要在 OpenCodeRunner 里写条件分支。

- [ ] **Q4: 多 agent 能否共存于同一用户？**
  v1 限制：`default_agent` 全局一个, schedule / CLI 调试可覆盖。
  v2 才考虑：per-thread / per-sender 路由。
  需要在 design 阶段确认 v1 的"全局 + per-schedule 覆盖" 是不是用户能接受的 MVP。

- [ ] **Q5: 旧 `claude_sessions.json` / `transcripts/` / `conversations/` 怎么迁？**
  选项 A: 启动时 auto-migrate (一次性 INFO, 旧路径软链接到新路径)。
  选项 B: 提供 `mailcode config migrate-agents` 手动迁移（dry-run 默认, `--apply` 改）。
  选项 C: 干脆不迁, 兼容读旧路径 + 写新路径, 双写一段时间。
  倾向 A + B 都做：A 处理常见情况, B 给谨慎用户。

- [ ] **Q6: `mailcode health` 要不要探测 agent？**
  现状只检查 SMTP/IMAP。可以加 `--check-agents` 同时探测三个 agent 是否可用, 默认开。
  需要 design 阶段定 health 的输出格式（"✅ claude: 1.0.17 / ❌ opencode: 未安装 / ✅ pi: 0.71.1"）。

- [ ] **Q7: integration test 怎么办？**
  现状 `tests/integration/test_opencode_execution.py` 已存在 (mock opencode)。是否扩成对所有三个 agent 的 e2e？
  倾向只补 unit, integration 留到真有三个 agent 二进制可用再说。CI 不跑 integration, 只本地按需。

- [ ] **Q8: 反自循环 header 怎么处理？**
  email_listener.py:458-462 现在检查 `X-MailCode-Remote-Token` 和 `X-OpenCode-Remote-Token`。
  方案 A: 共用 `X-MailCode-Remote-Token`, 删除 `X-OpenCode-Remote-Token` 死代码（破坏性, 旧客户端可能还在用）。
  方案 B: 保留两个 header, MailCode 发信统一带 `X-MailCode-Remote-Token`, 旧客户端发的 `X-OpenCode-Remote-Token` 继续被识别（向后兼容）。
  倾向 B: 兼容优先。

- [ ] **Q9: "agents" 字段的 schema 怎么定义给用户？**
  - `command` (必需): 二进制路径或 PATH 中的名字。
  - `extra_args` (可选): 每次调都加上的额外参数 (如 `--approve` / `--auto`)。
  - `default_model` (可选): 默认 provider/model。
  - `run_subcommand` (可选 bool): OpenCode 特殊, 默认 False, OpenCode 设 True 让 `opencode run "<prompt>"` 而不是 `opencode "<prompt>"`。
  - 还有别的吗? (timeout 默认值 / 环境变量注入 / 临时目录隔离 / etc.)。
  需要 design 阶段定 schema。

- [ ] **Q10: 抽象的边界在哪？**
  - MailCode 只管"邮件 ↔ 子进程"，不管 agent 内部的 tool / skill / extension。
  - 但 MailCode 要不要提供"session 文件路径传给 agent" 的能力？Claude 的 `ConversationHandler` 现在让 Claude 自己用 `Read` 工具读 session_*.json (设计原则: dumb pipe)。其他 agent 呢？
    - OpenCode 有 `read` 工具, 同理。
    - Pi 有 `read` 工具, 同理。
  - 倾向: 三者一致, MailCode 只传 prompt (含 session 路径), agent 自己读。

---

## 后续建议

按 arch-explore → arch-design → prototype → 实现的顺序：

1. **arch-design**: 拍板 §"未澄清问题"里的 Q1/Q2/Q3/Q4/Q5/Q9/Q10, 写 `docs/arch/multi-agent/design.md`。
2. **prototype**:
   - 在 `mailcode/utils/opencode_runner.py` 和 `mailcode/utils/pi_runner.py` 各写原型, 不动其他模块, 验证 CLI 行为（特别是 session 续接）。
   - 跑 `opencode run "<prompt>" --auto` / `pi -p "<prompt>"` / `claude` 三者的真实输出对比。
3. **refactor**:
   - 抽 `AgentRunner` Protocol + 注册表。
   - 把现有 `call_claude` 包成 `ClaudeRunner.call`。
   - 5 个调用点改造。
4. **migrate**:
   - 2 套数据存储（sessions/transcripts + conversations）+ schedules.json schema。
   - 旧数据迁移 + cleanup。
5. **document**: README 更新, CLAUDE.md 加 agent 配置章节, `docs/design-final/design.md` 加 "Agent 抽象层" 章节, `docs/references/multi-agent.md` 新增。
6. **test**: unit tests 全绿 (含 test_agent_registry), integration 暂缓, 手动 `mailcode serve` 跑三个 agent 各一封邮件做端到端验证。

预计改动量: ~20 个 Python 文件, ~1500 行新代码, ~400 行修改, ~10 个测试文件。
