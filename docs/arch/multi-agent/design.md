# 支持多 Coding Agent — 架构设计

> 状态: **架构设计阶段**（arch-design）。
> 写于 2026-09-04。基于 [`context.md`](./context.md) 的需求澄清。
>
> **本文结构**:
> 1. 三个候选方案（Minimal / Extensible / Performance）
> 2. 对比矩阵 + 推荐
> 3. 推荐方案的详细设计
> 4. 待回答问题（接 context.md 未澄清列表）

---

## 1. 三个候选方案

### 方案 A：Minimal（最小复杂度）

**Tagline**: ABC + 模块级注册表；加新 agent = 1 文件 + 1 行注册；零 Protocol 体操；claude 保持默认。

**核心架构**:

- `mailcode/utils/agent.py` 新增 `BaseRunner`（`abc.ABC`），提供 `call()` 模板方法（拼 args + 调 `_run_subprocess` + 错误处理）。
- 三个 runner 继承 `BaseRunner`，各自重写 `_build_args()` 和 `_extra_session_args()`。
- 模块级 dict `AGENTS: dict[str, BaseRunner]` + `get_runner(name)` / `list_agent_names()`。
- **保留** `call_claude()` 作为薄 shim，内部走 `ClaudeRunner().call()`，5 个调用点逐个迁移，diff 1-2 行/处。

**配置 schema**（平 dict，自动补默认）:
```json
{
  "default_agent": "claude",
  "agents": {
    "claude": {"command": "claude", "extra_args": ["--dangerously-skip-permissions"]},
    "pi": {"command": "pi", "extra_args": []}  # ⚠️ Pi 用户需在 extra_args 显式填 --provider X --model Y (见下)
  }
}
```

> v1 只内置 Claude + Pi 两个 agent。OpenCode 暂不实现（参考 README "安装"段仍保留 OpenCode 文字，但 runtime 不接通；用户加 OpenCode runner 时按 v2 流程：1 新文件 + 1 行注册）。
+ `schedules.json` 每条 task 加可选 `agent` 字段。

**Session 策略**: 按 agent 子目录：`~/.config/mailcode/<agent>/transcripts/`、`conversations/`、`sessions/`。老路径读时 fallback、写时新路径。`mailcode config migrate-agents` 子命令（dry-run 默认，--apply 执行）。

**复杂度**: 5 新文件 + 7 改文件 + ~450 新行 + ~80 改行 + 4 测试文件。

**借鉴**: Cline `src/api/index.ts` 工厂 + Herdr 模块级 agent registry。

---

### 方案 B：Extensible（可扩展优先）

**Tagline**: Protocol + 完整 schema 配置 + 软迁移 + 一键扩展。

**核心架构**:

- `typing.Protocol` 定义 `AgentRunner` 契约（不强制继承），每个 Runner 独立类。
- `mailcode/utils/agent.py` 模块级 dict `AGENTS` + `get_runner/register/list_agents` 三个公开 API。
- `mailcode/utils/agent_config.py` 新增 `AgentConfig` dataclass + 手写 validator（stdlib only，拒绝引入 `jsonschema` 包）。
- 完整的 config schema：`command` / `extra_args` / `default_model` / `env` / `cwd_strategy`（enum: subprocess|flag|both）/ `session_dir_strategy`（enum: agent_self|opencode_db）/ `timeout_default` / `permission_flag` / `install_hint` / `metadata`。
- 5 个调用点增加 `agent_type` 参数，handler 构造时显式传。
- 启动时 auto-migrate 旧数据 + 30 天保留期 + `mailcode config migrate-agents --apply`。

**复杂度**: 8 新文件 + 9 改文件 + ~1200 新行 + ~150 改行 + 6 测试文件。

**借鉴**: Aider 的 `model-metadata.json` + `model-settings.yml` schema + LiteLLM 抽象（拒绝引入, 太重）。

---

### 方案 C：Performance（性能/资源优先）

**Tagline**: Subclass-per-agent ABC + 单一 subprocess 模板 + 启动时构造 + 60s PATH 缓存 + env-var session 目录。

**核心架构**:

- `mailcode/utils/agent.py` 新增 `BaseAgentRunner`（`abc.ABC`）+ 模板方法 `_run_subprocess()`，两个子类 `ClaudeRunner` / `PiRunner` 各自重写 `_build_args()` 和 `_env_extras()`。
- **关键差异**：runner 实例在 listener / scheduler 启动时**预构造一次**，持有在 listener / scheduler 上；不是每次邮件都走 dict lookup。
- `shutil.which` 缓存 60s（每 runner 一份）。
- Session 目录通过环境变量 `MAILCODE_AGENT_SESSION_DIR` 传给子进程（OpenCode / Pi 原生支持）。
- **保留** `call_claude()` 作为薄 shim（与 Minimal 同思路）。
- 旧路径读时 fallback（与 Minimal 同思路）。

**复杂度**: 4 新文件 + 9 改文件 + ~450 新行 + ~250 改行 + 4 测试文件。

**借鉴**: 现有 `claude_runner.py:20-89` 的 subprocess 模板。

---

## 2. 对比矩阵

| 维度 | A. Minimal | B. Extensible | C. Performance | 备注 |
|------|-----------|---------------|----------------|------|
| 实现复杂度 | **4** ✓ | 2 | 3 | 1=最难, 5=最简 |
| 与现有架构契合度 | **5** ✓ | 3 | 4 | MailCode 风格是 dumb pipe + 小模块 |
| 演进灵活性 | 3 | **5** ✓ | **5** ✓ | 加第 4 个 agent |
| 向后兼容质量 | **5** ✓ | 4 | **5** ✓ | 保护现有 Claude 用户 |
| 性能特征 | 4 | 3 | **5** ✓ | 邮件级延迟 vs 子进程开销 |
| 测试可维护性 | 4 | 4 | 4 | 都支持 parametrize |
| 新文件数 | 5 | **8** | 4 | |
| 改文件数 | 7 | **9** | **9** | |
| 新增行数 | 450 | **1200** | 450 | |
| 修改行数 | **80** | 150 | 250 | |
| 测试文件 | 4 | **6** | 4 | |

（评分: 5=最好, 1=最差。粗体 = 该维度的最佳方案）

## 3. 推荐：Hybrid（Minimal + Performance 为主 + Extensible 三点取舍）

**结论**: 三个方案**没有明显赢家**——性能与契合度 Minimal 强，可扩展性 Extensible 强，性能 Performance 强。Hybrid 采各家之长。

**核心选型**:
- **ABC + 模块级 dict 注册表** (from A & C)：消除 3 份 subprocess 错误处理重复；显式比 entry_points 简单。
- **`call_claude()` shim** (from A & C)：现有 47 个 unit test + 5 个调用点零修改迁移到新抽象。
- **预构造 runner 实例** (from C)：listener / scheduler 启动时一次性构造，邮件路径上少一层 dict lookup。
- **60s `shutil.which` 缓存** (from C)：从"每封邮件 1 次 PATH 查找"降到"60s 1 次"。
- **`MAILCODE_AGENT_SESSION_DIR` 环境变量** (from C)：让 Pi 用其原生 session 存储路径，无需构造 `--session-dir` flag。
- **两阶段 PR 拆分** (from C)：PR1 只引 `BaseAgentRunner` + `ClaudeRunner` + 留 shim（diff 小, 易 review）；PR2 才接 `PiRunner` + 改造 5 个调用点（v1 不含 OpenCodeRunner）。

**采纳 Extensible 的两点取舍**（取舍 = 收益 > 成本, 但不全盘接受 Extensible 的过度设计）:
1. **`is_available()` + `hint_for_failure()` 方法加到 ABC 上**（v1 缺失的二进制会静默失败，新增这俩方法让失败响亮可读）。
2. **`runner_config_from_dict()` 装载器** 集中在 `agent.py`：避免每个 runner 自己解析 config 重复实现。

> **2026-09-04 review 易修复 3**: 原方案采纳 Extensible 的 `cwd_strategy: enum subprocess|flag` 字段, review 发现 Claude/Pi 都用 subprocess cwd=, v1 用不上, **改为不采纳**。v2 加 OpenCode 时再补 `cwd_strategy` 字段 (届时与 `default_model` 一同扩展 schema)。

**不采纳 Extensible 的部分**:
- 完整 schema + `env` / `metadata` / `session_dir_strategy` 字段：v1 用不上, 占 ~400 行 validator 代码。
- 手写 validator（1200 行里一半是它）：直接用 `dict.get(key, default)` + 启动时报错就够了。
- 启动时 auto-migrate：可能误删用户数据, 用 `mailcode config migrate-agents --apply` 显式更安全。

**总工作量**: ~700 行新增 + ~150 行修改, ~10 个新文件 + ~9 个改文件, ~5 个测试文件。

---

## 4. 推荐方案的详细设计

### 4.1 模块结构

```
mailcode/
├── utils/
│   ├── agent.py              [新] BaseAgentRunner ABC + AGENTS 注册表 + get_runner
│   ├── claude_runner.py      [改] ClaudeRunner(BaseAgentRunner) + call_claude() shim
│   ├── opencode_runner.py    [新] (v2 再加, v1 不实现)
│   ├── pi_runner.py          [新] PiRunner(BaseAgentRunner)
│   ├── paths.py              [新] agent_home() / transcripts_dir() / sessions_dir() / conversations_dir()
│   └── migrate.py            [新] migrate_legacy(dry_run, apply) — claude_sessions.json / transcripts / conversations 迁移
├── relay/
│   ├── conversation_handler.py  [改] 接 agent_name; _session_path 用 agent_home
│   ├── resume_handler.py        [改] 同上
│   ├── stateless_handler.py     [改] 同上
│   ├── email_listener.py        [改] 启动时构造 runners 缓存
│   └── scheduler.py             [改] Task.agent 字段 + 启动时构造 runners
├── cli.py                       [改] --agent 参数 + agents list 子命令 + migrate-agents
├── cli_chat.py                  [改] --agent 参数
├── config.py                    [改] get_default_agent() / get_agent_config() / merge_legacy_agent_field()
├── health.py                    [改] 默认探测 3 个 agent 的 --version
└── resources/
    └── default.json             [改] 新增 default_agent + agents 块
```

### 4.2 核心接口（`mailcode/utils/agent.py`）

```python
import abc
import logging
import os          # ← review 易修复 1: 漏 import
import shutil
import subprocess
import threading  # ← review 需讨论 5: _which_cache 并发保护
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class BaseAgentRunner(abc.ABC):
    """所有 agent runner 的抽象基类。"""

    name: str  # "claude" / "pi"

    # —— 并发: 所有 Runner 实例共享这个缓存 + 锁 ——
    _which_cache: dict[str, tuple[float, Optional[str]]] = {}
    _which_lock = threading.Lock()

    # —— 公开 API ——
    def is_available(self) -> bool:
        """检查二进制是否在 PATH 中（缓存 60s, 线程安全）。"""
        return self._cached_which() is not None

    def hint_for_failure(self) -> str:
        """未安装 / 调用失败时的可读 hint (供错误邮件正文)。
        子类可重写以提供更具体的指引 (例如 'npm i -g ...')。"""
        return f"{self.name} CLI 未安装或不在 PATH 中。请参考 README 安装指引。"

    def call(self, prompt: str, cwd: str = "",
             *, session_id: Optional[str] = None, resume: bool = False,
             no_session: bool = False, timeout: Optional[int] = None) -> Optional[str]:
        """主入口: 调子进程, 返回 stdout.strip() 或 None。

        模板方法: 拼 args + 跑 subprocess + 处理 5 类错误。
        所有 Runner 共享 — 不要在子类重写。
        """
        cwd = cwd or str(Path.home())
        args = self._build_args(
            session_id=session_id, resume=resume, no_session=no_session,
        )
        env = {**os.environ, **self._env_extras()}

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                args, input=prompt, capture_output=True,
                text=True, timeout=timeout or 86400, cwd=cwd, env=env,
            )
        except subprocess.TimeoutExpired:
            logger.error("agent=%s 子进程超时 cwd=%s", self.name, cwd)
            return None
        except FileNotFoundError:
            logger.error("agent=%s 命令未找到", self.name)
            return None
        except OSError as e:
            logger.error("agent=%s OS 错误: %s", self.name, e)
            return None

        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            logger.error("agent=%s 失败: rc=%s elapsed=%.1fs stderr=%r",
                         self.name, result.returncode, elapsed, result.stderr[:2000])
            return None
        logger.info("agent=%s 成功: elapsed=%.1fs stdout_len=%d",
                    self.name, elapsed, len(result.stdout))
        return result.stdout.strip()

    # —— 子类必须实现 ——
    @abc.abstractmethod
    def _build_args(self, *, session_id: Optional[str], resume: bool,
                    no_session: bool) -> list[str]:
        """构造子进程 argv (不含 cwd / prompt / timeout — 这些由 call() 模板统一处理)。"""

    @abc.abstractmethod
    def _command(self) -> str:
        """返回二进制名 (用于 shutil.which)。"""

    # —— 子类可重写 ——
    def _env_extras(self) -> dict[str, str]:
        """注入到子进程的环境变量。默认返回空 dict。"""
        return {}

    # —— 内部 ——
    def _cached_which(self) -> Optional[str]:
        """60s TTL 缓存 shutil.which (线程安全: 用 _which_lock 保护 read-modify-write)。"""
        cmd = self._command()
        with self._which_lock:
            now = time.monotonic()
            cached = self._which_cache.get(cmd)
            if cached and (now - cached[0]) < 60:
                return cached[1]
            path = shutil.which(cmd)
            self._which_cache[cmd] = (now, path)
            return path


# —— 自定义异常 ——
class AgentNotFoundError(KeyError):
    """请求的 agent 未注册 (拼写错 / v1 未实现)。"""


# —— 注册表 (模块级, import 后只读, 多线程读安全) ——
AGENTS: dict[str, BaseAgentRunner] = {}


def get_runner(name: str) -> BaseAgentRunner:
    if name not in AGENTS:
        raise AgentNotFoundError(
            f"unknown agent: {name!r}. available: {sorted(AGENTS)}. "
            f"配置 tasks[i].agent 请从可用列表选, 拼写错或 v1 未实现都会触发此异常。"
        )
    return AGENTS[name]


def list_agents() -> list[str]:
    return sorted(AGENTS)


def register(name: str, runner: BaseAgentRunner):
    """运行时注册 (仅测试 / plugin 用)。"""
    AGENTS[name] = runner


# —— 启动时注册内置 agent ——
def _register_builtins():
    from .claude_runner import ClaudeRunner
    from .pi_runner import PiRunner
    AGENTS["claude"] = ClaudeRunner()
    AGENTS["pi"] = PiRunner()


_register_builtins()
```

### 4.3 三个 Runner 的 `_build_args` 形状

> **2026-09-04 实地 CLI 探测修订**：原方案中 Pi 用 `--session` 是基于文档假设，**实际本地验证有偏差**。下面是基于 `claude 2.1.177` / `pi 0.84.4` `--help` 实测的修正版（OpenCode 未实测, v1 不实现）。

```python
# ClaudeRunner — claude 2.1.177 (--dangerously-skip-permissions 仍可用)
class ClaudeRunner(BaseAgentRunner):
    name = "claude"

    def _command(self) -> str:
        return "claude"  # 用于 shutil.which

    def _build_args(self, *, session_id, resume, no_session) -> list[str]:
        # cwd / prompt / timeout 由 BaseAgentRunner.call() 模板统一处理
        args = ["claude", "--dangerously-skip-permissions"]
        if session_id is not None:
            args.extend(["--session-id", session_id])
        if resume:
            args.append("--resume")
        return args

    def hint_for_failure(self) -> str:
        return ("Claude Code 未安装或不在 PATH 中。"
                "请运行 'claude --version' 验证, 或访问 https://docs.anthropic.com/zh-CN/docs/claude-code 安装。")


# PiRunner — pi 0.84.4 (用 --session-id <uuid> 语义化标志, 不是 --session <path|id>!)
class PiRunner(BaseAgentRunner):
    name = "pi"

    def _command(self) -> str:
        return "pi"

    def _build_args(self, *, session_id, resume, no_session) -> list[str]:
        # ⚠️ Pi 不硬编码 --provider — 用户环境差异大 (直连 anthropic vs openrouter vs google)
        # 用户在 config 里通过 agents.pi.extra_args 自填, e.g.:
        #   "pi": {"extra_args": ["--provider", "openrouter", "--model", "anthropic/claude-sonnet-4-5"]}
        args = ["pi", "-p"]
        if session_id is not None:
            args.extend(["--session-id", session_id])
        if resume:
            args.append("-c")
        if no_session:
            args.append("--no-session")
        return args

    def hint_for_failure(self) -> str:
        return ("Pi CLI 未安装或不在 PATH 中。"
                "请运行 'npm i -g @mariozechner/pi-coding-agent' 安装, 然后 'pi /login' 配置 provider。"
                "注意 Pi 默认 provider 是 google, 推荐配 'openrouter' 或 'anthropic' 走 Claude 模型。")
```

**实测 vs 原方案的差异 (针对 Pi)**:
- Pi `--session` → **`--session-id`**（取精确 UUID, 缺失则创建；`--session` 还要 path 形式，不友好）
- Pi **provider 不可硬编码**：本地用户的 `~/.pi/agent/auth.json` 实际配的是 openrouter API key（不是直连 anthropic），硬编码 `--provider anthropic` 会拿到 403。PiRunner **不填 `--provider`**，让用户在 `agents.pi.extra_args` 自己配：`["--provider", "openrouter", "--model", "anthropic/claude-sonnet-4-5"]`。Pi 默认 provider 是 google，未配置时会走 google（用户在 README 警示里能看到）。
- Pi `--session-id` 本地验证 argv 接受正常（创建了 session 文件, 尽管 API 调用失败）
- OpenCode 未实测（v1 不实现）。若 v2 加 OpenCodeRunner，**先跑 L3 金丝雀测试**验证 argv 形状再写实现 — 这是本次设计的核心教训。

### 4.3.1 真实 CLI 金丝雀测试 (L3 层)

新增 `tests/integration/test_real_cli_argv.py`, **默认 skip**, 加 pytest marker `@pytest.mark.real_agent`, 需要 `MAILCODE_TEST_REAL_AGENT=1` 才跑。覆盖：

1. **`claude --dangerously-skip-permissions --session-id <uuid> -p "<prompt>"`** 真实运行, 断言 argv 形状 + returncode=0 + 输出含 prompt 关键词
2. (v1 跳过 OpenCode, 若 v2 加入再补这一条)
3. **`pi -p --provider anthropic --session-id <uuid> "<prompt>"`** 真实运行, 同上
4. **session 续接**: 创建 + 接同 uuid 各跑一次, 验证 session 文件真在 ~/.claude/projects/.../<uuid>.jsonl 等
5. **cwd 隔离**: 两个不同 cwd 各跑一次, 验证 session 文件落到对应 cwd-hash 目录

金丝雀测试发现的偏差已写回 §4.3（见上面"实测 vs 原方案的差异"）。后续 agent 升级时这套测试是**第一道防线**。

### 4.3.2 Fake Binary 测试 (L2 层)

`tests/unit/fake_agents/fake_claude.py` / `fake_pi.py` 两个 Python 脚本（v1 不含 fake_opencode.py）, 在 PATH 中替换真 binary, 模拟真实行为：
- 打印 argv 到 stderr (供测试断言)
- 写一个标记文件 (模拟 session 文件创建)
- echo prompt 内容到 stdout
- 退出 0

测试用 `subprocess.run` + 环境变量改 PATH 注入 fake binary, 验证 Runner 端到端行为（不依赖真 API）。

### 4.4 配置 schema（最终）

```json
{
  "default_agent": "claude",
  "agents": {
    "claude":    {"command": "claude",    "extra_args": ["--dangerously-skip-permissions"]},
    "pi":        {"command": "pi",        "extra_args": []}  # ⚠️ 用户必填 provider (见下)
  }
}
```

**字段** (v1 最小集, v2 按需扩):
| 字段 | 必需 | 用途 |
|------|------|------|
| `default_agent` | 否 | 全局默认 agent 名, 默认 `"claude"` |
| `agents.<name>.command` | 是 | 二进制路径或 PATH 名 |
| `agents.<name>.extra_args` | 否 | 每次调都附加的参数 (默认见上, Pi 用户必填 `--provider`) |
| `agents.<name>.timeout_seconds` | 否 | per-agent 默认超时, 覆盖 runner 内部默认 |
| `schedules.tasks[].agent` | 否 | 单条 schedule 覆盖 default_agent |

> v1 不包含的字段 (设计上保留扩展空间, **v1 schema 验证不识别 → 启动 warning 但不报错**):
> - `default_model` (Pi/Claude 都通过 `extra_args` 透传, v2 加 aider 等需要时才单独建字段)
> - `cwd_strategy` (Claude/Pi 都用 subprocess cwd=, v2 加 OpenCode 才需要 flag 模式 — 见 §3 review 易修复 3)
> - `env` / `metadata` (v1 用 `extra_args` 拼环境变量更直观)

**老 config 兼容**: 读 `config.json` 时, 若无 `default_agent` / `agents` 字段, 内存里自动补全（**不写回磁盘**）。`config show` 显示补全后的视图。

**配置校验**:
- `mailcode config validate` 必须验证 `default_agent` 是 `agents` 的 key 之一
- `schedules.json` 加载时验证 `tasks[i].agent` 是已注册 agent（不是等运行时崩溃, 是配置加载时 fail-fast）

### 4.5 Session 命名空间

```
~/.config/mailcode/
├── claude_sessions.json          [旧, 读时 fallback, 写时不再用]
├── transcripts/<uuid>.json        [旧, 同上]
├── conversations/                 [旧, 同上]
│
├── claude/                        [新]  (agent=claude)
│   ├── sessions.json
│   ├── transcripts/<uuid>.json
│   └── conversations/session_<id>.json + index.json
├── opencode/                      [v2]
│   ├── sessions.json
│   ├── transcripts/<uuid>.json
│   └── conversations/...
└── pi/                            [新]
    ├── sessions.json
    ├── transcripts/<uuid>.json
    └── conversations/...
```

**handler 路径构造**:
```python
# mailcode/utils/paths.py
def agent_home(agent: str) -> Path:
    return _MAILCODE_HOME / agent

def transcripts_dir(agent: str) -> Path:
    return agent_home(agent) / "transcripts"

def conversations_dir(agent: str) -> Path:
    return agent_home(agent) / "conversations"

def sessions_file(agent: str) -> Path:
    return agent_home(agent) / "sessions.json"
```

**读时 fallback**: 每个 handler 内部 `_load_X(agent)` 先尝试新路径, 不存在再读老路径（同时把 `agent` 视为默认 `claude`）。

### 4.6 迁移

```bash
mailcode config migrate-agents [--apply] [--agent NAME]
```

默认 dry-run：列出将移动的文件、目标路径。`--apply` 实际执行：

1. 检测 `~/.config/mailcode/claude_sessions.json` → 移到 `<home>/claude/sessions.json`
2. 检测 `~/.config/mailcode/transcripts/*.json` → 移到 `<home>/claude/transcripts/`
3. 检测 `~/.config/mailcode/conversations/*.json` → 移到 `<home>/claude/conversations/`
4. 写 marker: `~/.config/mailcode/legacy-claude-migration.json` 记录时间戳 + 文件清单
5. 老路径创建 symlink 指向新路径（30 天 grace period）
6. 启动时若 marker 超过 30 天, 删 symlink

### 4.7 CLI 表现

```bash
mailcode agents list
# claude: ✅ /usr/local/bin/claude (1.0.17)
# opencode: ❌ 未实现 (v2 计划)
# pi: ✅ /usr/local/bin/pi (0.71.1)

mailcode config show
# {
#   "default_agent": "claude",
#   "agents": {...}
# }

mailcode schedule add daily-todo \
  --agent pi \
  --type daily --time 09:00 \
  --prompt "..." --to-email me@x.com

mailcode chat --agent pi

mailcode config migrate-agents --apply
```

### 4.8 5 个调用点的改造 diff 量

| 文件 | 改动 |
|------|------|
| `conversation_handler.py` | `__init__` 加 `agent_name` 参数; `_session_path` 改用 `paths.conversations_dir(self.agent_name)`; `cr_module.call_claude(...)` → `self.runner.call(...)` |
| `resume_handler.py` | 同上; `_transcripts_dir` / `_mapping_file` 改用 `paths.*` |
| `stateless_handler.py` | `from mailcode.utils.claude_runner import call_claude` 保留（旧 import 路径, shim 自动转） |
| `scheduler.py` | `Task.agent: Optional[str]` 字段; `_run_task` 解析 `task.agent or default_runner`; `call_claude` → `runner.call` |
| `cli_chat.py` | `args.agent` 解析 + 启动时构造 runner |

每处 diff 5-15 行, **5 处总计 ~50 行修改**。

### 4.9 测试策略

- `tests/unit/conftest.py` 新增 `mock_runner(name)` fixture 通用 mock（替换原 `mock_claude` / `mock_opencode_available`）。
- `tests/unit/test_agent_runner.py` 新增：测试 BaseAgentRunner 模板方法、which 缓存、env 注入、错误处理、AgentNotFoundError。
- `tests/unit/test_claude_runner.py` 保留向后兼容（用 `ClaudeRunner` 包老 `call_claude`）。
- `tests/unit/test_pi_runner.py` 新增：断言 `_build_args` 精确 argv 形状（v1 无 test_opencode_runner.py）。
- `tests/unit/test_conversation_handler.py` / `test_resume_handler.py` 等注入 mock runner, 不再直接 mock subprocess。
- `tests/unit/test_config_migration.py` 新增：迁移子命令的 dry-run / apply 行为。

### 4.10 两阶段 PR 拆分

**PR1: 引入抽象层 + Claude 实现（无功能变化）**
- 新增 `mailcode/utils/agent.py`（BaseAgentRunner + AGENTS 注册表 + ClaudeRunner 注册 + _which_lock）
- 改 `mailcode/utils/claude_runner.py`：把现有 `call_claude` 函数体挪进 `ClaudeRunner.call`，保留 `call_claude()` shim
- 改 `mailcode/resources/default.json`：新增 `default_agent` + `agents` 字段
- 改 `mailcode/config.py`：新增 `get_default_agent()` / `get_agent_config()`
- 改 `mailcode/config.py`：在 `validate_serve_config()` 里加 `default_agent in agents` 校验
- 新增 `tests/unit/test_agent_runner.py`
- 新增 `tests/unit/test_agent_runner_concurrency.py`：`ThreadPoolExecutor` 并发调 `is_available()`, 验证 lock 正确
- **diff ~250 行, 5 个文件, 0 个行为变化**

**PR2: 接入 Pi + 调用点改造 + 迁移工具（v1 不含 OpenCodeRunner）**
- 新增 `mailcode/utils/pi_runner.py`
- 新增 `mailcode/utils/paths.py` / `migrate.py`
- 改 5 个调用点（Conversation/Resume/Stateless/Scheduler/cli_chat）
- 改 `cli.py` / `cli_chat.py` / `scheduler.py` / `health.py`
- **改 `scheduler.py:_run_task`**: 加 `try/except AgentNotFoundError` 捕获, 写入 `task.last_error` + 发错误邮件, scheduler 继续（review R1 修复）
- **改 `migrate.py`**: 启动时检测 dangling symlink（用户移动 `~/.config/mailcode/` 后断裂）, 清理 + 提示重新 `--apply`（review R2 缓解）
- 新增 `mailcode config migrate-agents` 子命令
- 新增 `mailcode agents list` 子命令
- 改 `mailcode/resources/default.json`：补 pi 默认块（opencode 不加）
- 改 `schedule_cli.py` / `scheduler.py`：加载 schedules.json 时 fail-fast 校验 `tasks[i].agent`（review R7 修复）
- 新增 ~5 个测试文件
- **diff ~550 行新 + ~150 行改**

---

## 5. 已回答的 context.md 未澄清问题

| Q | 决策 | 理由 |
|---|------|------|
| **Q1** Session ID 统一 | ✅ 用 mailcode UUID 同时传给 agent 内部 + MailCode 自己的 transcripts/ | 三个 agent 都支持 UUID 形式 session id; MailCode transcripts 独立审计 |
| **Q2** config schema | ✅ `default_agent` + `agents: {<name>: {...}}` | 清晰、向后兼容（老 config 内存补全） |
| **Q3** Resume 首封/续接参数 | ✅ MailCode 统一传 `--session-id <uuid>` (语义化 UUID, Claude/Pi 一致); ResumeConversationHandler 接最近时, Claude 用 `--resume`, Pi 用 `-c` | v1 简化, 用 mailcode_uuid 统一会话标识 |
| **Q4** 多 agent 共存 | ✅ v1 全局 + per-schedule 覆盖; v2 再考虑 per-thread | 简化 MVP |
| **Q5** 旧数据迁移 | ✅ `mailcode config migrate-agents --apply` 显式, dry-run 默认 + 30 天 grace + symlink | 避免误删, 给用户审视窗口 |
| **Q6** health 探测 | ✅ 默认探测三个 agent 的 `--version`, 健康报告里附 | 价值高, 成本低 |
| **Q7** integration test | ✅ 只补 unit, integration 留到真有 agent 二进制再说 | CI 跑不起, 手动验证 |
| **Q8** 反自循环 header | ✅ 保留 `X-MailCode-Remote-Token` + `X-OpenCode-Remote-Token` 双检查 (向后兼容), MailCode 发信只发前者 | 旧客户端不破坏 |
| **Q9** schema 字段 | ✅ `command` / `extra_args` / `timeout_seconds` (per-agent) + `default_agent` + `tasks[].agent` (review 后精简: `cwd_strategy` / `default_model` v1 不需要) | 极简, 仅含 v1 用得到的字段 |
| **Q10** 抽象边界 | ✅ MailCode 只传 prompt + cwd + session_id, agent 自助读 session 文件（dumb pipe 不变） | 三个 agent 都有 `read` 工具, 一致 |

---

## 6. 后续步骤

1. **Prototype（可选, 建议做）**:
   - 在 `mailcode/utils/` 下写 `pi_runner.py` 原型（不接其他模块, 验证 argv 形状已通过 L3 金丝雀完成）
   - 验证 session 续接行为（首次创建 vs 接已存在）

2. **PR1** (上面 §4.10): 引入抽象 + Claude 实现 + 默认 config（~200 行 diff）
3. **PR2** (上面 §4.10): 接入 Pi + 调用点改造 + 迁移工具（~620 行 diff, v1 不含 OpenCode）
4. **文档**: README/CLAUDE.md 加 multi-agent 配置章节, `docs/references/multi-agent.md` 加 agent 速查表
5. **手动验证**: `mailcode serve` 跑三封邮件, 每封用一个 agent, 端到端确认

预计 **2-3 周工作量**（单人, 含代码 review + 测试 + 文档）。
