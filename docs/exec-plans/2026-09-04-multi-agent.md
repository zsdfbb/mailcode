# 2026-09-04 Multi-Agent — 执行计划

> 配套设计: `docs/design-plans/2026-09-04-multi-agent.md`
> 设计文档: `docs/arch/multi-agent/design.md` (§4.10 两阶段 PR 拆分)
> 评审报告: `docs/arch/multi-agent/review.md` (8 维度评分 GREEN)

---

## PR1: 引入抽象层 + ClaudeRunner（零行为变化）

PR1 结束后现有 47 个 unit test 全部不回归，无行为变化。

---

### Task 1: BaseAgentRunner ABC + 注册表

- **type**: impl
- **files**: `mailcode/utils/agent.py` (新建)
- **deps**: 无
- **description**:
  新建 `agent.py`，包含:
  - `BaseAgentRunner` ABC: `call()` 模板方法（拼 args + subprocess.run + 5 类错误处理）、`is_available()` + `hint_for_failure()`、`_cached_which()` 60s TTL + `threading.Lock` 保护
  - 抽象方法: `_build_args(*, session_id, resume, no_session) -> list[str]`、`_command() -> str`
  - 可覆写方法: `_env_extras() -> dict[str, str]`
  - `AgentNotFoundError(KeyError)` 自定义异常
  - `AGENTS: dict[str, BaseAgentRunner]` 模块级注册表 + `get_runner(name)` / `list_agents()` / `register(name, runner)`
  - `_register_builtins()`: 启动时注册 ClaudeRunner + PiRunner（PR1 阶段 PiRunner 可暂用占位 stub）
- **test**: 见 Task 1-test
- **review**:
  - design.md §4.2 代码示例必须修正后参照（review.md 易修复 1-3）
  - `_build_args` 签名只含 4 个 keyword-only 参数，不含 prompt/cwd/timeout
  - `_which_lock` 必须是 `threading.Lock` 类变量，所有 Runner 实例共享
  - `os.environ` import 不可遗漏

---

### Task 1-test: BaseAgentRunner 单元测试

- **type**: test
- **files**: `tests/unit/test_agent_runner.py` (新建), `tests/unit/test_agent_runner_concurrency.py` (新建)
- **deps**: Task 1
- **description**:
  `test_agent_runner.py`:
  - 创建 MinimalRunner(BaseAgentRunner) 子类（_build_args 返回固定 list，_command 返回 "echo"）
  - 测试 `call()` 模板方法: mock subprocess.run，验证正常返回 stdout.strip()、returncode != 0 返回 None、TimeoutExpired 返回 None、FileNotFoundError 返回 None、OSError 返回 None
  - 测试 `is_available()`: mock shutil.which 返回/不返回路径
  - 测试 `_cached_which()` 60s TTL: 连续调两次，验证第二次用缓存（shutil.which 只 call 一次）
  - 测试 `hint_for_failure()` 返回默认文案 + 子类覆写生效
  - 测试 `get_runner("unknown")` 抛 `AgentNotFoundError`
  - 测试 `list_agents()` 返回排序列表
  - 测试 `register()` 运行时注册
  `test_agent_runner_concurrency.py`:
  - `ThreadPoolExecutor` 10 线程同时调 `is_available()`, 验证没有 race（shutil.which 调用次数 <= 线程数，不是线程数^2）
- **test** (test of test): 所有新测试必须通过
- **review**:
  - mock subprocess.run 需覆盖 5 个错误分支各至少一次
  - 并发测试需 `pytest.mark.timeout(10)` 防死锁

---

### Task 2: ClaudeRunner 改造

- **type**: impl
- **files**: `mailcode/utils/claude_runner.py` (改)
- **deps**: Task 1
- **description**:
  将现有 `call_claude()` 函数体重构为:
  - 新增 `ClaudeRunner(BaseAgentRunner)` 类，`name = "claude"`
  - `_command()` 返回 `"claude"`
  - `_build_args()`: 构造 `["claude", "--dangerously-skip-permissions"]` + 可选 `--session-id` / `--resume`
  - `hint_for_failure()` 返回 Claude 专用安装提示
  - 保留 `call_claude()` 函数作为 shim，内部调 `ClaudeRunner().call()`，docstring 标 `@deprecated`
  - 现有 `call_claude` 的 5 个调用点不改（shim 保证向后兼容）
- **test**: 见 Task 2-test
- **review**:
  - `call_claude()` shim 签名必须与现有完全一致（prompt, cwd="", *, session_id=None, resume=False, timeout=None）
  - shim 内部 kwargs 映射到 ClaudeRunner.call() 时 no_session 默认 False
  - 现有 `test_claude_runner.py` 全部 47 个 unit test 不回归

---

### Task 2-test: ClaudeRunner 回归验证

- **type**: test
- **files**: `tests/unit/test_claude_runner.py` (确认不回归)
- **deps**: Task 2
- **description**:
  运行现有 `tests/unit/test_claude_runner.py` 全部测试，确认:
  - `call_claude()` shim 行为与改造前完全一致
  - 无 import 错误
  - 无回归失败
- **test**: `pytest tests/unit/test_claude_runner.py -q` 全通过
- **review**:
  - 如果现有测试有 mock `subprocess.run` 的，确认 mock 路径仍然正确（可能需要从 `claude_runner.subprocess.run` 改为 `agent.subprocess.run`）

---

### Task 3: Config 改造

- **type**: impl
- **files**: `mailcode/config.py` (改), `mailcode/resources/default.json` (改)
- **deps**: Task 1
- **description**:
  `config.py`:
  - 新增 `get_default_agent(config: dict) -> str`: 读 `config.get("default_agent", "claude")`
  - 新增 `get_agent_config(config: dict, agent_name: str) -> dict`: 读 `config.get("agents", {}).get(agent_name, {})`
  - 在 `validate_serve_config()` 中新增: `default_agent` 必须是 `agents` 的 key 之一（否则报错）
  - 老 config 兼容: 若无 `default_agent` / `agents` 字段，内存里自动补全（不写回磁盘）
  `default.json`:
  - 新增 `"default_agent": "claude"` 和 `"agents": {"claude": {"command": "claude", "extra_args": ["--dangerously-skip-permissions"]}}`
- **test**: 见 Task 3-test
- **review**:
  - `get_default_agent` / `get_agent_config` 需处理 config 字段缺失的情况（优雅降级）
  - validate 报错信息需包含 "default_agent must be one of: [...]"
  - default.json 新增字段必须向后兼容老 config 读取

---

### Task 3-test: Config 单元测试

- **type**: test
- **files**: `tests/unit/test_config.py` (改)
- **deps**: Task 3
- **description**:
  在 `test_config.py` 中新增测试:
  - `test_get_default_agent`: 返回 "claude"（默认）/ 用户自定义值
  - `test_get_agent_config`: 返回正确 dict / agent 不存在返回空 dict
  - `test_validate_default_agent_in_agents`: default_agent 不在 agents 中 → 报错
  - `test_validate_default_agent_missing`: 无 default_agent 字段 → 默认 "claude" 不报错
  - `test_legacy_config_auto_fill`: 老 config 无 default_agent/agents 字段，内存补全但不写磁盘
- **test**: 新增测试 + 现有 config 测试全通过
- **review**:
  - mock config dict 覆盖: 空 dict、只有部分字段、完整字段

---

### Task 4: PR1 全量回归

- **type**: test
- **files**: 无新增
- **deps**: Task 1, Task 2, Task 3
- **description**:
  运行全部 unit test 确认零回归:
  ```bash
  source .venv/bin/activate && python3 -m pytest tests/unit/ -q
  ```
- **test**: 全部通过（47 现有 + 所有新增）
- **review**:
  - 如果有失败，排查是否是 mock 路径变化导致
  - 确认 `ruff check mailcode/ tests/` 无新 warning

---

## PR2: 接入 Pi + 调用点迁移 + 迁移工具（功能上线）

PR2 结束后 `mailcode serve` 支持通过 config 切换 agent，Pi 能接收邮件处理。

---

### Task 5: PiRunner 实现

- **type**: impl
- **files**: `mailcode/utils/pi_runner.py` (新建)
- **deps**: Task 1
- **description**:
  新建 `PiRunner(BaseAgentRunner)`:
  - `name = "pi"`
  - `_command()` 返回 `"pi"`
  - `_build_args()`: 构造 `["pi", "-p"]` + 可选 `--session-id <uuid>` / `-c` (resume) / `--no-session`
  - `hint_for_failure()`: 返回 Pi 专用安装提示（npm install + pi /login + provider 配置警告）
  - **不硬编码 `--provider`**: 用户在 config `agents.pi.extra_args` 自填
  - extra_args 从 config 注入: `_build_args` 末尾追加 `config_agents["pi"]["extra_args"]`
- **test**: 见 Task 5-test
- **review**:
  - argv 形状必须与 design.md §4.3 PiRunner 示例一致（已通过 L3 金丝雀验证）
  - `-p` 是 Pi 的 stdin prompt 标志，不可省略
  - `-c` 是 Pi 的 resume 标志（不是 `--resume`）
  - extra_args 注入顺序: base args 先，extra_args 后

---

### Task 5-test: PiRunner 单元测试

- **type**: test
- **files**: `tests/unit/test_pi_runner.py` (新建)
- **deps**: Task 5
- **description**:
  新建 `test_pi_runner.py`:
  - 测试 `_build_args()` 精确 argv 形状:
    - 无 session/resume → `["pi", "-p"]`
    - 有 session_id → `["pi", "-p", "--session-id", "<uuid>"]`
    - 有 resume → `["pi", "-p", "-c"]`
    - 有 no_session → `["pi", "-p", "--no-session"]`
    - 全组合 → 顺序正确
  - 测试 `hint_for_failure()` 返回含 "npm i -g" 的提示
  - 测试 `_command()` 返回 "pi"
  - 测试 `is_available()` 依赖 PATH 中有 pi（mock shutil.which）
- **test**: 全部通过
- **review**:
  - 用 `pytest.mark.parametrize` 覆盖参数组合
  - 不要 mock subprocess（只测 argv 构造，不测真实调用）

---

### Task 6: paths.py 路径模块

- **type**: impl
- **files**: `mailcode/utils/paths.py` (新建)
- **deps**: 无
- **description**:
  新建 `paths.py`，集中所有 agent 相关路径派生:
  - `_MAILCODE_HOME = Path.home() / ".config" / "mailcode"`
  - `agent_home(agent: str) -> Path`: `_MAILCODE_HOME / agent`
  - `transcripts_dir(agent: str) -> Path`: `agent_home(agent) / "transcripts"`
  - `conversations_dir(agent: str) -> Path`: `agent_home(agent) / "conversations"`
  - `sessions_file(agent: str) -> Path`: `agent_home(agent) / "sessions.json"`
  - `legacy_sessions_file() -> Path`: `_MAILCODE_HOME / "claude_sessions.json"` (旧路径，fallback 用)
  - `legacy_transcripts_dir() -> Path`: `_MAILCODE_HOME / "transcripts"` (旧路径)
  - `legacy_conversations_dir() -> Path`: `_MAILCODE_HOME / "conversations"` (旧路径)
- **test**: 见 Task 6-test
- **review**:
  - 路径用 `Path` 不是字符串拼接
  - legacy 路径函数不含 agent 参数（旧路径全局共享）

---

### Task 6-test: paths.py 单元测试

- **type**: test
- **files**: `tests/unit/test_paths.py` (新建)
- **deps**: Task 6
- **description**:
  新建 `test_paths.py`:
  - 测试 `agent_home("claude")` 返回 `~/.config/mailcode/claude`
  - 测试 `agent_home("pi")` 返回 `~/.config/mailcode/pi`
  - 测试 `transcripts_dir("claude")` 返回 `~/.config/mailcode/claude/transcripts`
  - 测试 `conversations_dir("pi")` 返回 `~/.config/mailcode/pi/conversations`
  - 测试 `sessions_file("claude")` 返回 `~/.config/mailcode/claude/sessions.json`
  - 测试 legacy 路径函数返回全局共享路径
- **test**: 全部通过
- **review**:
  - mock `Path.home()` 避免依赖真实 home 目录
  - 用 `tmp_path` fixture 验证路径构造不依赖文件系统状态

---

### Task 7: Handler 调用点迁移

- **type**: impl
- **files**: `mailcode/relay/conversation_handler.py` (改), `mailcode/relay/resume_handler.py` (改), `mailcode/relay/stateless_handler.py` (改)
- **deps**: Task 1, Task 6
- **description**:
  `conversation_handler.py`:
  - `__init__` 新增 `agent_name: str = "claude"` 参数
  - `self.runner = get_runner(agent_name)` 替代 `import call_claude`
  - `_session_path` 改用 `paths.conversations_dir(self.agent_name)`
  - `call_claude(...)` → `self.runner.call(...)`
  - 读 fallback: 新路径不存在时读 legacy 路径

  `resume_handler.py`:
  - 同上: `__init__` 新增 `agent_name`
  - `_transcripts_dir` / `_mapping_file` 改用 `paths.*`
  - `call_claude(...)` → `self.runner.call(...)`
  - 读 fallback: 新路径不存在时读 legacy 路径

  `stateless_handler.py`:
  - 最小改动: 保留 `from mailcode.utils.claude_runner import call_claude` (shim 自动转)
  - 或改用 `get_runner(agent_name).call()` — 视 diff 量决定
- **test**: 见 Task 7-test
- **review**:
  - handler 的 `agent_name` 参数必须有默认值 `"claude"`，保证向后兼容
  - legacy fallback 必须是 "新路径优先 + 旧路径 fallback"，不是反过来
  - 不要引入额外的 config 读取逻辑（agent_name 由调用方传入）

---

### Task 7-test: Handler 回归测试

- **type**: test
- **files**: `tests/unit/test_conversation_handler.py` (改), `tests/unit/test_resume_handler.py` (改), `tests/unit/test_stateless_handler.py` (改)
- **deps**: Task 7
- **description**:
  修改现有 handler 测试:
  - 注入 mock runner（通过 conftest.py 的 `mock_runner` fixture，见 Task 13）
  - 确认 `agent_name` 参数有默认值时不破坏现有构造调用
  - 新增测试: 传入 `agent_name="pi"` 时调用的是 PiRunner（mock 路径断言）
  - 新增测试: legacy fallback（新路径不存在时自动读旧路径）
- **test**: 全部通过，无回归
- **review**:
  - 现有测试不能因为新增参数而失败（默认值保证）
  - mock runner 的 `.call()` 返回值需与现有 mock_claude 行为一致

---

### Task 8: email_listener fallback 更新

- **type**: impl
- **files**: `mailcode/relay/email_listener.py` (改)
- **deps**: Task 6
- **description**:
  - 更新 `claude_sessions.json` fallback 路径: 从 `_MAILCODE_HOME / "claude_sessions.json"` 改为优先读 `paths.sessions_file("claude")`，不存在再 fallback 到 `paths.legacy_sessions_file()`
  - 如果 listener 直接读写其他硬编码路径，同步改为用 `paths.*`
- **test**: 见 Task 8-test
- **review**:
  - 这是 plan 文件中标注的 HIGH gap: email_listener.py 直接读 claude_sessions.json 未在 design 迁移列表里
  - 确认 fallback 是 "新路径优先 + 旧路径 fallback" 模式

---

### Task 8-test: email_listener fallback 测试

- **type**: test
- **files**: `tests/unit/test_listener_lifecycle.py` (改)
- **deps**: Task 8
- **description**:
  - mock paths 函数，验证 email_listener 优先读新路径 sessions.json
  - 新路径不存在时 fallback 到旧路径 claude_sessions.json
  - 两个路径都不存在时正常初始化（不崩溃）
- **test**: 全部通过
- **review**:
  - 覆盖: 新路径存在、新路径不存在+旧路径存在、两个都不存在

---

### Task 9: Scheduler agent 路由

- **type**: impl
- **files**: `mailcode/relay/scheduler.py` (改)
- **deps**: Task 1, Task 3
- **description**:
  - `Task` dataclass 新增 `agent: Optional[str] = None`
  - `_run_task`: 解析 `task.agent or config.get_default_agent(config)` 获取 agent name
  - `call_claude(prompt, cwd, timeout)` → `get_runner(agent_name).call(prompt, cwd, timeout=timeout)`
  - **加 `try/except AgentNotFoundError`**: 捕获后写入 `task.last_error` + 发错误邮件 + scheduler 继续（review R1 修复）
  - 加载 schedules.json 时: 验证 `task.agent` 是已注册 agent（fail-fast，review R7 修复）
- **test**: 见 Task 9-test
- **review**:
  - `AgentNotFoundError` 不能导致 scheduler 线程崩溃
  - 错误邮件内容需包含 `task.last_error` 的具体错误信息
  - fail-fast 校验需在加载阶段，不是运行时

---

### Task 9-test: Scheduler agent 路由测试

- **type**: test
- **files**: `tests/unit/test_scheduler.py` (改)
- **deps**: Task 9
- **description**:
  - mock get_runner，验证 `task.agent` 正确路由到对应 runner
  - 测试 `task.agent = None` 时 fallback 到 config default_agent
  - 测试 `task.agent = "opencode"` (未注册) → AgentNotFoundError 被捕获 + task.last_error 写入 + scheduler 不崩溃
  - 测试 schedules.json 加载时 task.agent 拼错 → fail-fast 报错
- **test**: 全部通过
- **review**:
  - mock runner 的 `.call()` 返回 None 时，scheduler 行为需验证（发错误邮件）

---

### Task 10: CLI 改造

- **type**: impl
- **files**: `mailcode/cli.py` (改), `mailcode/cli_chat.py` (改)
- **deps**: Task 1, Task 3
- **description**:
  `cli.py`:
  - 新增 `agents list` 子命令: 遍历 `list_agents()`，显示每个 agent 的可用状态 + 版本（`is_available()` + subprocess --version）
  - 新增 `config migrate-agents` 子命令入口（调用 migrate.py，见 Task 11）
  - `serve` 命令: 无需改（agent 路由在 scheduler/listener 内部）

  `cli_chat.py`:
  - 新增 `--agent` 参数: `parser.add_argument("--agent", default="claude")`
  - 启动时 `runner = get_runner(args.agent)`
  - `call_claude(...)` → `runner.call(...)`
- **test**: 见 Task 10-test
- **review**:
  - `agents list` 输出格式需包含: agent 名 + 可用/不可用 + 版本号 + 路径
  - `--agent` 参数默认值 "claude" 保证向后兼容
  - 未知 agent 名需给出友好错误提示

---

### Task 10-test: CLI 单元测试

- **type**: test
- **files**: `tests/unit/test_cli.py` (改), `tests/unit/test_cli_chat.py` (改)
- **deps**: Task 10
- **description**:
  `test_cli.py`:
  - mock list_agents + is_available，测试 `agents list` 输出包含正确 agent 名
  - 测试 `config migrate-agents` 调用 migrate 函数
  `test_cli_chat.py`:
  - mock get_runner，测试 `--agent pi` 传入后调用 PiRunner
  - 测试默认 `--agent claude` 行为
- **test**: 全部通过
- **review**:
  - CLI 测试用 `monkeypatch` 或 mock，不执行真实 CLI 调用

---

### Task 11: 迁移工具

- **type**: impl
- **files**: `mailcode/utils/migrate.py` (新建), `mailcode/schedule_cli.py` (改)
- **deps**: Task 6
- **description**:
  `migrate.py`:
  - `migrate_legacy(dry_run: bool = True, agent: str = "claude") -> list[str]`: 返回将移动的文件清单
  - dry_run 默认: 列出将移动的文件和目标路径
  - `--apply` 实际执行:
    1. `claude_sessions.json` → `<home>/claude/sessions.json`
    2. `transcripts/*.json` → `<home>/claude/transcripts/`
    3. `conversations/*.json` → `<home>/claude/conversations/`
    4. 写 marker: `legacy-claude-migration.json` (时间戳 + 文件清单)
    5. 老路径创建 symlink 指向新路径
  - dangling symlink 检测: 启动时检查 symlink target，断裂则清理 + 提示重新 `--apply`

  `schedule_cli.py`:
  - 加载 schedules.json 时: 验证 `tasks[i].agent` 是已注册 agent
  - 校验失败 → raise + 清晰错误信息
- **test**: 见 Task 11-test
- **review**:
  - migrate 必须是幂等的（重复 apply 不出错）
  - symlink 用 `Path.symlink_to()`，target 用相对路径
  - marker 文件格式: `{"migrated_at": "ISO timestamp", "files": [...]}`

---

### Task 11-test: 迁移工具单元测试

- **type**: test
- **files**: `tests/unit/test_migration.py` (新建)
- **deps**: Task 11
- **description**:
  新建 `test_migration.py`:
  - 用 `tmp_path` 创建模拟的 `~/.config/mailcode/` 目录结构
  - 测试 dry-run: 文件不被移动，返回文件清单
  - 测试 apply: 文件被正确移动到新路径
  - 测试 marker 文件被创建
  - 测试 symlink 被创建（指向新路径）
  - 测试重复 apply 幂等（marker 已存在则跳过）
  - 测试 dangling symlink 检测（手动断链后调用 → 清理 + 不崩溃）
- **test**: 全部通过
- **review**:
  - 所有文件操作用 `tmp_path`，不碰真实 `~/.config/mailcode/`
  - mock `Path.home()` 指向 `tmp_path`

---

### Task 12: Health per-agent 探测

- **type**: impl
- **files**: `mailcode/health.py` (改)
- **deps**: Task 1
- **description**:
  - 遍历 `list_agents()`，对每个 agent 调 `is_available()`
  - 可用时: 运行 `agent --version` 获取版本号
  - 健康报告输出每个 agent 的状态 + 版本 + 路径
  - 不可用时: 显示 `hint_for_failure()` 内容
- **test**: 见 Task 12-test
- **review**:
  - 每个 agent 探测独立，一个失败不影响其他
  - 版本获取用 subprocess + 1s timeout，防卡死

---

### Task 12-test: Health 单元测试

- **type**: test
- **files**: `tests/unit/test_health.py` (改)
- **deps**: Task 12
- **description**:
  - mock is_available + subprocess.run，测试输出包含每个 agent 名 + 状态
  - 测试 agent 不可用时显示 hint_for_failure
  - 测试 --version 超时时不崩溃（显示 "版本获取超时"）
- **test**: 全部通过
- **review**:
  - 覆盖: 所有 agent 可用、部分可用、全部不可用

---

### Task 13: default.json 补全 + conftest fixture

- **type**: impl
- **files**: `mailcode/resources/default.json` (改), `tests/unit/conftest.py` (改)
- **deps**: Task 5, Task 6
- **description**:
  `default.json`:
  - 在 agents 块中补 pi 默认配置:
    ```json
    "pi": {"command": "pi", "extra_args": []}
    ```

  `conftest.py`:
  - 新增 `mock_runner(name)` fixture: 创建一个 FakeRunner 实例，可配置 `.call()` 返回值 + `.is_available()` 返回值
  - 替代原有 `mock_claude` fixture（保持向后兼容，保留旧名指向新 fixture）
- **test**: 见 Task 13-test
- **review**:
  - default.json 补 pi 块后，现有 config 测试不能回归
  - mock_runner fixture 需支持参数化（不同 agent name 返回不同 mock）

---

### Task 13-test: PR2 全量回归

- **type**: test
- **files**: 无新增
- **deps**: Task 5, 6, 7, 8, 9, 10, 11, 12, 13
- **description**:
  运行全部 unit test:
  ```bash
  source .venv/bin/activate && python3 -m pytest tests/unit/ -q && python3 -m ruff check mailcode/ tests/
  ```
- **test**: 全部通过，ruff 无 warning
- **review**:
  - 如果有失败，逐个排查是 impl 问题还是 mock 路径变化
  - 总测试数应 >= 47 (现有) + ~20 (新增)

---

### Task 14: E2E 金丝雀验证

- **type**: MANUAL_ACK_REQUIRED
- **files**: 无代码文件
- **deps**: Task 13 (全部 PR2 完成)
- **description**:
  手动端到端验证，需真 agent 二进制 + 真 API:
  1. `MAILCODE_TEST_REAL_AGENT=1 pytest tests/integration/ -v` — 运行 L3 金丝雀测试
  2. `mailcode agents list` — 确认 Claude + Pi 都显示可用
  3. `mailcode serve --once --dry-run` — 确认启动无错误
  4. 发送一封邮件，验证 Claude 能处理并回复
  5. 在 config 中切换 `default_agent: "pi"`，发第二封邮件，验证 Pi 能处理
  6. `mailcode config migrate-agents --dry-run` — 确认迁移预览正确
- **test**: 每步都需人工确认输出正确
- **review**:
  - L3 金丝雀测试输出需包含 prompt 关键词（证明真 API 调通）
  - 两封邮件的回复内容需不同（证明 agent 切换生效）
  - 如果 Pi API 调用失败（403/429），记录到 issue 但不阻塞合并

---

## 任务依赖图

```
PR1:
  T1 ──→ T1-test
  T1 ──→ T2 ──→ T2-test
  T1 ──→ T3 ──→ T3-test
  T1 + T2 + T3 ──→ T4 (全量回归)

PR2:
  T5 ──→ T5-test
  T6 ──→ T6-test
  T1 + T6 ──→ T7 ──→ T7-test
  T6 ──→ T8 ──→ T8-test
  T1 + T3 ──→ T9 ──→ T9-test
  T1 + T3 ──→ T10 ──→ T10-test
  T6 ──→ T11 ──→ T11-test
  T1 ──→ T12 ──→ T12-test
  T5 + T6 ──→ T13 ──→ T13-test (全量回归)
  T13 ──→ T14 (E2E 金丝雀)
```

## 执行顺序建议

PR1 内: T1 → T1-test → T2 → T2-test → T3 → T3-test → T4
PR2 内: T5+T6 (并行) → T5-test+T6-test (并行) → T7+T8+T9+T10+T11+T12 (并行) → 各自 test → T13 → T13-test → T14
