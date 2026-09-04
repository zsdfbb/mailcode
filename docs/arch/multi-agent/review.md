# 多 Coding Agent 改造 — 架构质量评审

> 评审对象: [`context.md`](./context.md) + [`design.md`](./design.md) + 4 篇 ADR (001/002/003/004/005) + L3 金丝雀测试 `tests/integration/test_real_cli_argv.py`
> 评审时间: 2026-09-04
> 评审者: arch-validate

## 范围与方法

- **可行性**: 实地 CLI 探测 (claude 2.1.177 / pi 0.84.4) + 三方案对比矩阵合理性
- **可维护性**: 模块边界 / 接口稳定性 / 错误传播 / 并发安全 / 资源管理
- **可理解性**: 概念一致性 / 抽象层次 / 文档完整度
- **性能与可靠性**: 单封邮件延迟 / 故障模式 / 降级策略

---

## 各维度结论

### 🟢 可行性 — GREEN

| 项 | 评价 |
|----|------|
| Pi argv 形状 | ✅ 本地金丝雀测试验证 (`--session-id` 接受正确) |
| 三方案对比 | ✅ 评分合理, Hybrid 选取每个方案的局部最优 |
| 两阶段 PR 拆分 | ✅ PR1 (零行为变化) + PR2 (功能上线), diff 隔离清晰 |
| 零第三方依赖 | ✅ ABC + 模块级 dict, 不引入 entry_points/jsonschema |
| 总工作量估算 | ⚠️ ~700 行可能略低估 (migration tool + L2 fake binary 未计入), 实际可能 800-1000 行 |

**亮点**: L3 金丝雀测试在设计阶段就捕获了 2 个 Pi 假设错误 (--session 改 --session-id、provider 不可硬编码), 这是传统 mock 测试测试抓不到的——**最直接的"为什么要分层测试"证据**。

---

### 🟢 模块边界与关注点分离 — GREEN

- `BaseAgentRunner` 只关心 subprocess 协议 (args + env + 错误处理), 不关心 agent 内部逻辑 ✓
- `paths.py` 集中所有路径派生, handler 不再硬编码 `_MAILCODE_HOME / "conversations"` ✓
- `migrate.py` 单文件承担迁移逻辑, 不污染 handler / runner ✓
- `claude_runner.py` 既保留旧 `call_claude()` shim 又承载 `ClaudeRunner` 类, **过渡期明确标注 deprecated** ✓

**微瑕** (易修复):
- `agent.py` 同时承担"接口定义 + 注册表 + 启动注册"三个职责, 50 行内可接受, 不必拆
- ADR-005 的 fake binary 设计在 §4.3.2 但没有具体 Python 文件路径, 实现时需要落地

---

### 🟡 接口稳定性 — YELLOW

| 接口 | 稳定性 | 演进性 |
|------|--------|--------|
| `BaseAgentRunner.call(prompt, cwd, *, session_id, resume, no_session, timeout)` | 🟢 7 参数, 含 keyword-only | v2 想加新参数 (如 `images=`) 直接扩 keyword-only 不破坏现有 |
| `_build_args(...)` | 🟡 同 7 参数 | 跟 `call` 同步, 问题是 `_build_args(prompt, cwd, ...)` 里 `prompt` 和 `timeout` **从未被任何 runner 使用** — ClaudeRunner/PiRunner 都只读 `session_id` / `resume` / `no_session` |
| `_command()` | 🟢 简单, 二进制名 |
| `_env_extras()` | 🟢 默认空 dict, 扩展无成本 |

**⚠️ 发现的具体问题**:

1. **dead params**: `_build_args(prompt, ..., timeout)` 的 `prompt` 和 `timeout` 参数 ClaudeRunner / PiRunner 都用不上, 这是冗余接口。`prompt` 是因为 `call()` 已经把 prompt 写到 stdin 了, runner 只负责构造 argv; `timeout` 是因为 `subprocess.run(..., timeout=...)` 在 `call()` 顶层统一处理。
   - **影响**: 实现 `ClaudeRunner._build_args` 时会签名 lint 警告未使用参数
   - **建议**: 简化签名 `_build_args(self, *, session_id, resume, no_session) -> list[str]`, 不用传 cwd/prompt/timeout (cwd 由 Runner 内 `self.cwd` 默认值, prompt 走 stdin, timeout 由 call 模板方法统一)
   - **修法**: 在 PR1 实现时改

2. **`cwd_strategy` enum 字段定义但 Runner 不消费**: §4.4 schema 提到 `cwd_strategy: subprocess / flag`, 但 §4.3 ClaudeRunner / PiRunner 的 `_build_args` 示例里**根本没读 `self.cwd_strategy`**。两 Runner 都是 subprocess cwd= 模式。
   - **影响**: 用户在 config 配 `cwd_strategy: flag` 但 Runner 不响应
   - **建议**: v1 不需要 `cwd_strategy` 字段 (Pi 用 subprocess, v2 加 OpenCode 才需要), schema 暂时只保留 `command` / `extra_args` / `timeout_seconds`, v2 再扩

---

### 🟡 错误传播与处理完整性 — YELLOW

`BaseAgentRunner.call()` 模板方法统一处理 5 个失败分支:

```python
except subprocess.TimeoutExpired: → return None
except FileNotFoundError:        → return None
except OSError as e:              → return None
if result.returncode != 0:        → return None
```

**🟢 优点**:
- 5 个 runner 共享同一份错误处理 (消除 15 处重复, ADR-001 已论证)
- 返回 `None` 语义清晰, 调用方统一判定 "AI 失败 → 发通知邮件"

**🟡 风险**:

1. **超时被 kill 但 session 文件半写**: `subprocess.TimeoutExpired` 时 subprocess 被强制 kill, 但 agent 可能正写到 session 文件 (e.g. Pi 的 `~/.pi/agent/sessions/<uuid>.jsonl` 追加写入), kill 后文件可能损坏。
   - **影响**: 下次 `--session-id <同一uuid> --resume` 接到损坏文件, agent 可能报错或丢失上下文
   - **严重性**: 低-中 (timeout 主要发生在 agent 卡死, 此时 session 文件基本没写多少)
   - **建议**: v1 接受风险, v2 加"session 文件 hash 校验 + 自动重命名 .partial"

2. **stderr 截断 500 字符可能丢关键信息**: §4.2 日志只 `result.stderr[:500]`, 如果 agent 在 stderr 1000 字符后才输出关键错误, 看不全。
   - **影响**: 用户在 relay.log 看 debug 时信息不全
   - **建议**: 提到 2000 字符 (Claude/Pi 输出通常短, 长输出一般是工具调用详情, 反而不是真错误)

3. **`returncode != 0` 不区分 agent-specific exit codes**: Claude 进程异常退出会 return 1; Pi / OpenCode 类似。当前所有非零都 → return None + 同一封错误邮件。
   - **影响**: 用户看不到"agent 自己发现 prompt 有问题" vs "agent 二进制崩溃" 的差异
   - **建议**: v1 不区分, v2 考虑

---

### 🟡 并发安全 — YELLOW

MailCode 多线程模型 (`scheduler.py` 是 daemon thread, IMAP listener 是主线程 + IDLE 线程, 文档 §scheduler.py:24):

**🟢 已正确**:
- `AGENTS` 模块级 dict 在 `_register_builtins()` 后**只读**, 并发读安全 ✓
- `ScheduleStore` 用模块级 `threading.RLock` 保护 JSON 读写 ✓
- `_run_task` 用 `_running_task_ids: set[str]` 保护"同一任务不并发跑" ✓

**🟡 需要关注的**:

1. **`_which_cache: dict[str, tuple[float, Optional[str]]]` 是类变量**, 所有 Runner 实例共享。
   - 当前用法 `self._which_cache[cmd] = (now, path)` 是 dict 单 key 赋值, CPython 下原子 ✓
   - 但 `if cmd in self._which_cache: ts, path = ...` 读 + 写两步骤非原子, 多线程同时首次 `is_available()` 调用可能**两个线程都触发 shutil.which** (浪费一点点 PATH lookup, 不致命)
   - **严重性**: 低 (浪费 < 10ms CPU, 不会数据损坏)
   - **建议**: 加 `_WHICH_LOCK = threading.Lock()` 保护 `_which_cache` 的读-改-写, 实现成本 ~5 行

2. **`task.agent` 字段 + Scheduler 多线程**: 假设 `task.agent = "pi"` 但 v1 没实现 PiRunner (假设性场景), `_run_task` 会调到 `get_runner("pi")` → `AgentNotFoundError` 抛出。**当前设计没明确这个错误的捕获位置**。
   - **影响**: schedule 触发后整个线程崩溃, 影响其他 schedule
   - **建议**: `get_runner()` 失败应被 `_run_task` 捕获, 写入 `task.last_error` + 发错误邮件, scheduler 继续

3. **预构造 runner 实例的生命周期**: 设计说"listener / scheduler 启动时一次性构造"。如果 serve 模式下 listener 跑 30 天, runner 实例持有期间 PATH 可能变 (用户装新 binary / 卸载) → `_which_cache` 60s TTL 自动失效 OK, 但 `is_available()` 缓存命中是基于上次查询结果, 可能短暂不一致 (低风险)

---

### 🟡 资源管理 — YELLOW

| 资源 | 生命周期 | 风险 |
|------|----------|------|
| `subprocess.run` 子进程 | 单次调用, 自动 cleanup | ✅ |
| `shutil.which` 缓存 | 60s TTL, 内存里 ~几 KB | ✅ 上界可控 |
| `transcripts/*.json` 文件 | 永久保留, 需清理 | ⚠️ 长期会增长, v1 没清理策略 |
| `conversations/session_*.json` | 永久保留 | ⚠️ 同上, 会无限增长 |
| `claude_sessions.json` / `sessions.json` 映射文件 | 永久保留 | ⚠️ 已处理 (`msg_id → session_id` 映射) |
| symlink (`~/.config/mailcode/claude_sessions.json → claude/sessions.json`) | 30 天 | ✅ 有 grace + marker |
| agent 内部 session 文件 (`~/.claude/projects/.../*.jsonl`) | 永久, **MailCode 不管** | ⚠️ MailCode 传给 agent UUID 后, agent 自己积累 |

**🟡 主要问题**:

1. **MailCode transcripts / conversations 文件无上限**: 长期跑会无限增长, 但**这是现状问题, 不是本次新增**。
   - **建议**: ADR 没明确"是否本次顺手加 TTL 清理"。context.md §TTL 提到 "ConversationHandler._get_ttl_days() 默认 90 天" 但 transcript 没有 TTL。

2. **Symlink 在用户目录移动时断裂**: `~/.config/mailcode/claude_sessions.json → claude/sessions.json` 是相对路径 symlink。如果用户 `mv ~/.config/mailcode ~/Documents/`, symlink 跟着移动后还指向原相对位置, 应该 OK。但如果 `rm -rf ~/.config/mailcode/` + 重建 (罕见但可能), symlink dangling。
   - **建议**: 启动时 `os.path.exists(target)` 检查 symlink, dangling 则清理并提示用户重新跑 `--apply`

---

### 🟡 可理解性 — YELLOW

**🟢 优点**:
- 4 个 ADR 各聚焦一个决策, 决策脉络清晰
- Hybrid 方案选取有明确理由, 不像"作者夹带私货"
- L3 金丝雀测试 docstring 把"为什么需要"写透

**🟡 缺点**:

1. **设计文档 §4.2 代码示例有 bugs, 实现时必须修正**:
   - `os.environ` 用了但 `import os` 漏写
   - `_build_args` 签名有 `prompt` / `cwd` / `timeout` 但 ClaudeRunner / PiRunner 示例不用 → 新人按示例写会困惑
   - `ClaudeRunner` / `PiRunner` 类没有补 `_command()` 抽象方法实现 (示例只展示 `_build_args`)

2. **§5 Q3 决策行写错**: "MailCode 统一传 `--session <uuid>` / `-s <uuid>`" — 实际 PiRunner 用 `--session-id`, 不是 `--session`。**Q3 描述与 §4.3 实现不一致**, 实现时必须统一。

3. **"Hybrid picks" 术语不在 README/CLAUDE.md 词汇表**: 新人看到"采 from A & C"会懵。应该在 ADR-001/003/004 互相 cross-link。

4. **5 个 ADR 没有一个总览图或目录**: arch-explore / arch-design / arch-validate 三阶段产物需要一张 map 让人找得到。

---

### 🟢 性能与可靠性 — GREEN (邮件场景下)

| 维度 | 数据 | 评价 |
|------|------|------|
| 每封邮件 agent 调用开销 | 1 个 subprocess, 200-500ms 启动 + 1-30s LLM | ✅ 不可压缩, 抽象层 < 1ms 不影响 |
| 抽象层 per-mail 开销 | dict lookup + cached which + 1 函数调用 | < 0.1ms, 相对 subprocess 启动可忽略 |
| 内存占用 | 3 个 Runner 单实例 × 几 KB + which 缓存 | < 50 KB, 可忽略 |
| 故障模式 | 5 种异常分支都有对应 fallback (None + 错误邮件) | ✅ 用户体验"AI 失败"一致 |
| 降级策略 | 旧路径读 fallback + 30 天 symlink grace | ✅ 升级用户零感知 |
| 可观测性 | `logger.info(...)` 记录 elapsed / stdout_len / returncode | ✅ 当前够用, v2 可加 metrics |

**🟡 不足**:

1. **缺乏 metrics/tracing**: MailCode 当前只有 logging, 没 metrics 端口。如果用户想知道"今天 Pi 调了几次、平均延迟", 没法查询。
   - **建议**: v1 不做, v2 加 `mailcode stats agents` 子命令汇总

2. **L3 测试 skip 规则可能太宽松**: 当前 stderr 含 403/429 文本就 skip。但 agent stderr 格式可能变化, 未来 Pi 改输出格式 → 测试意外失败 (false negative)。**反面**: 测试太严又会 false positive fail。
   - **现状判断**: 当前规则 (匹配 stderr 子串) 是合理的 pragmatic 折中

---

## 风险排序 (影响 × 可能性)

| # | 风险 | 影响 | 可能性 | 风险分 |
|---|------|------|--------|--------|
| R1 | `task.agent` 配置拼错 (如 "opencode" v1 未实现) → scheduler 线程崩溃 | 中 | 中 | **🟡 中** |
| R2 | Symlink 断裂 (用户动 `~/.config/mailcode/` 目录) | 中 | 低 | **🟢 低** |
| R3 | Subprocess 超时被 kill 但 session 文件半写 | 中 | 低 | **🟢 低** |
| R4 | `_which_cache` 类变量并发 read-modify-write (CPython 下基本无害但理论上 racy) | 极低 | 中 | **🟢 低** |
| R5 | MailCode transcripts / conversations 长期无限增长 | 中 | 高 | **🟡 中** (现状问题, 不是本次引入) |
| R6 | design.md §4.2 代码示例未实现时按抄, 引入 bugs | 中 | 高 | **🟡 中** |
| R7 | `task.agent` / `default_agent` 字段拼写错 (用户写 `c l a u d e` 带空格) → runtime 找不到 | 低 | 中 | **🟢 低** |
| R8 | `X-OpenCode-Remote-Token` 死代码保留 (用户实际只发 `X-MailCode-Remote-Token`) | 极低 | 高 | **🟢 极低** |
| R9 | L3 测试误判 (stderr 文本匹配未来 agent 输出格式变化) | 低 | 中 | **🟢 低** |
| R10 | 总工作量低估 (~700 行 → 实际 800-1000) | 低 | 高 | **🟢 低** |

---

## 改进建议 (分层)

### 🟢 易修复 (PR1 实现前顺手)

1. **修正 design.md §4.2 代码示例**:
   - 补 `import os`
   - 简化 `_build_args` 签名为 `_build_args(self, *, session_id, resume, no_session) -> list[str]` (去掉未用参数)
   - ClaudeRunner 示例补 `_command()` 方法实现
   - PiRunner 示例同步精简

2. **修正 design.md §5 Q3 描述**: 改为 "MailCode 统一传 `--session-id <uuid>` (语义化标志), Resume 用 `--resume` (Claude) / `-c` (Pi)"

3. **`cwd_strategy` 字段从 v1 schema 移除**: Pi 不需要, v2 加 OpenCode 再加。schema 简化为 `command` / `extra_args` / `timeout_seconds` 三字段。

### 🟡 需讨论 (PR2 实现前定)

4. **scheduler `_run_task` 加 `AgentNotFoundError` 捕获**: 写 `task.last_error` + 发错误邮件, scheduler 继续 (避免一个错 schedule 阻塞其他 schedule)

5. **`_which_cache` 加 `threading.Lock`**: 5 行代码, 防 CPython 解释器升级到自由线程 (3.13t) 时出 race。**推荐**: 加锁, 成本极低。

6. **transcripts / conversations TTL 清理**: 现状已有 session TTL 90 天清理 (`conversation_handler.py:_cleanup_expired_sessions`)。transcripts 没 TTL, 但长期增长不会爆盘 (一个邮件 ~10KB)。**推荐**: 不在本次 v1 加, 留作 backlog。

### 🟢 架构级 (暂不做)

7. **metrics/tracing**: v2 加 `mailcode stats agents` 子命令, 统计各 agent 的调用次数 / 平均延迟 / 失败率

8. **per-email 主题路由**: context.md Q4 明确 v1 不做, v2 再考虑

9. **第三方 plugin (entry_points)**: context.md §扩展性 明确 v1 不做, v2 再升级

---

## 总体评分

| 维度 | 评分 | 备注 |
|------|------|------|
| 可行性 | 🟢 9/10 | Pi argv 验证完成, 三方案对比可信 |
| 可维护性 | 🟢 8/10 | 模块边界清晰, 模板方法消重复, §4.2 示例需修正 |
| 接口稳定性 | 🟡 7/10 | 接口签名前向兼容 OK, 但 `_build_args` dead params 需清理 |
| 错误传播 | 🟢 8/10 | 模板方法统一, stderr 截断 + session 半写风险可控 |
| 并发安全 | 🟡 7/10 | 大部分正确, `_which_cache` 加锁成本极低值得加 |
| 资源管理 | 🟡 7/10 | 进程/缓存 OK, transcript 无限增长是 backlog |
| 可理解性 | 🟡 7/10 | 5 个文档之间互相 cross-link 弱, §4.2 示例有 bugs |
| 性能与可靠性 | 🟢 9/10 | 邮件场景下无可挑剔, v2 再加 metrics |

**总评**: 🟢 **可以进入实现** (PR1), 但需先做"易修复"清单中 1-3 条, 把 design.md §4.2 代码示例和 §5 Q3 描述修正后再开始写代码。

---

## 给 PR1 / PR2 实现者的具体建议

### PR1 启动前 (10 分钟)

- [ ] 读本文 §"易修复" 1-3 条, 改 design.md
- [ ] `_build_args` 签名简化为 4 个 keyword-only 参数
- [ ] schema 删 `cwd_strategy`, ADR-003 同步更新

### PR1 实现中

- [ ] `BaseAgentRunner` 加 `threading.Lock` 保护 `_which_cache`
- [ ] 测试 `test_agent_runner.py` 覆盖并发场景 (`ThreadPoolExecutor` 同时调 `is_available()`)
- [ ] `call_claude()` shim 接受完整 kwargs, 显式标 `@deprecated` 在 docstring

### PR2 实现前

- [ ] `Scheduler._run_task` 加 `AgentNotFoundError` 捕获
- [ ] `mailcode config validate` 校验 `tasks[].agent` 是已注册 agent
- [ ] `mailcode config migrate-agents` 实现时加 symlink dangling 检查 (启动时清理)

---

## 元评估 (评审方法)

本次评审基于:
- 完整阅读 design.md (503 行) + context.md (413 行) + 5 个 ADR (~600 行) + L3 测试 (288 行)
- 实地验证 Pi argv shape (金丝雀测试跑通)
- 对照 MailCode 现有代码风格 (scheduler.py 锁模式 / conversation_handler.py session 文件格式)
- 没有跑 PR1 / PR2 实际实现 (评审对象是设计文档, 不是代码)

未覆盖:
- L2 fake binary 设计 (design.md §4.3.2 描述较粗, 但 PR2 阶段可补)
- 性能基准测试 (设计只给了 < 0.1ms 估算, 没实际 benchmark)
- PR1/PR2 实际 review (本文评审的是 design, 实现 review 是后续 PR review 阶段)