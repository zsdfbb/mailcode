# ADR-005: 测试分层策略 — L1/L2/L3

> 状态: **Accepted** (2026-09-04)
> 决定者: 架构探索 (Zhang Shuai)
> 关联: [`design.md §4.3.1 / §4.3.2`](./design.md)

## 背景

多 agent 改造引入两个新的 subprocess 边界 (Claude / Pi, v1 不含 OpenCode), 需要决定测试金字塔的层级：

- 现状 (CLAUDE.md / `tests/unit/`): 全部 L1 mock, 47 个测试。
- 现状 (`tests/integration/`): 目录存在但空, `tests/run_tests.sh --integration` 期望有 `test_config.json`。
- 现状 (`tests/binary/`): 提及但未实现。

**核心问题**: 只用 L1 mock 风险 = **设计假设无法被真 binary 验证**。本次设计初期假设 (针对 Pi)：
- Pi `--session <path|id>` ❌ 实际是 `--session-id <uuid>`
- Pi 默认 provider = `google`, 直连 anthropic 必须显式 `--provider` ⚠️ 用户环境差异大 (本地验证：用户实际配 openrouter key)

这些假设直到跑真 binary 才暴露。如果没真 binary 测试, PR2 写完用户部署才发现 = 糟糕体验。

## 决定

引入 **3 层测试金字塔**, 各自职责清晰 + CI 边界明确:

### L1 — 单元 mock (现状, 默认 CI 跑)

- **位置**: `tests/unit/`
- **依赖**: 仅 `unittest.mock` patch `subprocess.run`, 无 binary / 无网络
- **速度**: <10ms/测试, 全跑 < 1s
- **价值**: 验证业务逻辑 (handler 状态机、文件 I/O、配置解析)
- **CI**: ✅ 默认跑
- **覆盖**: 47 个测试 → 后续加 ~10 个新 mock-based runner 测试

### L2 — Fake binary (新增, 默认 CI 跑)

- **位置**: `tests/unit/fake_agents/fake_<agent>.py` + `tests/unit/conftest.py`
- **依赖**: 写小 Python 脚本模拟 CLI 行为, 不调真 API
- **速度**: ~100ms/测试 (含 subprocess 启动)
- **价值**: 验证 subprocess 契约 (cwd 传递 / env 注入 / timeout / returncode / stdout 解析 / session 文件创建) 而不依赖真 API
- **CI**: ✅ 默认跑
- **覆盖**: 每个 Runner 1-2 个 fake binary 测试, 验证 `_run_subprocess()` 模板方法

**Fake binary 设计**:
```python
# tests/unit/fake_agents/fake_claude.py
#!/usr/bin/env python3
"""Fake claude CLI, 用于 L2 测试。

行为:
- 把 argv 写到 $FAKE_AGENT_ARGV_LOG (测试断言用)
- 把 prompt 内容 echo 到 stdout
- 写一个标记文件 $FAKE_AGENT_SESSION_FILE 模拟 session 创建
- 退出 0 (除非 FAKE_AGENT_FAIL=1)
"""
import os, sys
with open(os.environ['FAKE_AGENT_ARGV_LOG'], 'w') as f:
    f.write(' '.join(sys.argv))
if os.environ.get('FAKE_AGENT_SESSION_FILE'):
    Path(os.environ['FAKE_AGENT_SESSION_FILE']).touch()
print(sys.stdin.read() if not sys.stdin.isatty() else 'fake claude response')
sys.exit(int(os.environ.get('FAKE_AGENT_FAIL', 0)))
```

### L3 — 真 binary 金丝雀 (新增, CI 默认 skip)

- **位置**: `tests/integration/test_real_cli_argv.py`
- **依赖**: 真 binary + API key + 网络
- **速度**: 5-30s/测试
- **价值**: **当 agent CLI 升级改 flag 名时第一线报警**。本次设计初期的 2 个 Pi flag 错误就是 L3 跑出来的（OpenCode 未实测, 因为 v1 不实现）。
- **CI**: ❌ 默认 skip (要 `MAILCODE_TEST_REAL_AGENT=1` 才跑)
- **覆盖**: 每个 agent 2-3 个测试 (argv shape / 真实运行 / session 续接); v1 只覆盖 Claude + Pi

**L3 测试特性**:
1. 默认 skip via `pytest.mark.skipif` + env var 检查
2. 自定义 marker `real_agent` (在 `tests/integration/conftest.py` 注册, 避免 UnknownMarkWarning)
3. Skip 条件三层:
   - binary 不存在 (`shutil.which` 返回 None)
   - 认证缺失 (API key env 或 `~/.pi/agent/auth.json` / `~/.claude/.credentials.json`)
   - 网络不通 (socket/SSL/DNS 级错误, **不**把 HTTP 4xx 算不通——4xx 说明 TLS+TCP+DNS 都通了)
4. argv vs API 失败的区分: argv 错误 fail (CI 应捕获), API 提供方问题 (403/429/配额) skip (用户环境差异, 不是代码问题)

## 三层覆盖矩阵

| 维度 | L1 mock | L2 fake binary | L3 真 binary |
|------|---------|---------------|--------------|
| 业务逻辑 | ✅ | partial | |
| Handler 状态机 | ✅ | | |
| subprocess 契约 | partial | ✅ | partial |
| argv 形状精确 | partial | ✅ | ✅ |
| 真实 CLI flag 兼容性 | | | ✅ |
| Session 文件创建 | | ✅ | ✅ |
| 网络/API 调用 | | | ✅ (但易 flaky) |
| 默认 CI 跑 | ✅ | ✅ | ❌ |
| 需要 binary | ❌ | ❌ | ✅ |
| 需要 API | ❌ | ❌ | ✅ |
| 速度 | <10ms | ~100ms | 5-30s |

## skip 规则详解

L3 skip 优先级 (满足任一即 skip):
1. `MAILCODE_TEST_REAL_AGENT != "1"` (CI 强制 skip)
2. `shutil.which(agent_name) is None` (binary 缺失)
3. 认证缺失 (`_claude_auth_ok` / `_pi_auth_ok` 检查对应 auth 文件/env; v1 无 `_opencode_auth_ok`)
4. 网络不通 (urllib 探测, 区分 4xx vs socket 错误)

L3 **fail 规则**:
- argv 形状错误 (如 `--session-id` 改名为 `--session-uuid`)
- binary 拒绝运行 (returncode != 0 且 stderr 不含已知可接受错误 403/429/"No project session found"/"Request not allowed")

L3 **skip-after-run 规则** (argv OK 但 API 失败):
- stderr 包含 403/429/"No project session found"/"Request not allowed" → skip
- 这正是本次 Pi 验证遇到的情况: argv 正确, openrouter 用户调 anthropic provider 拿到 403, 测试优雅 skip

## 经验 (来自本次 Pi argv 验证)

2026-09-04 本地验证 Pi 0.84.4 时:

```bash
$ /opt/homebrew/bin/pi -p --session-id "mailcode-canary-123" "..."
Warning: No project session found with id 'mailcode-canary-123'; creating a new session with that id.
403 {"error":{"forbidden","message":"Request not allowed"}}
```

解读:
- ⚠️ "No project session found... creating a new session" → **`--session-id <uuid>` flag 工作正常** (Pi 创建了 session 文件用我们的 UUID)
- ⚠️ "403 Request not allowed" → 默认 provider = anthropic, 用户 `~/.pi/agent/auth.json` 配的是 openrouter key, 直连 anthropic 失败
- L3 测试应判定 argv OK + skip (不是 fail)

启示:
1. argv 形状测试和 API 调用测试**不能耦合** — 必须分别 fail
2. Pi provider **不可硬编码** (`--provider anthropic`), 让用户通过 `agents.pi.extra_args` 自填
3. session-id 创建成功 = argv 完全正确, 不需要 full API success 来证明

## 后果

- **正**: 早期发现 2 个 Pi CLI 假设错误 (`--session` → `--session-id` + provider 不可硬编码)
- **正**: PR2 后用户升级 agent CLI 时, L3 立即报警 (不需要等用户邮件反馈)
- **正**: CI 跑得快 (L1+L2 < 5s), 不被真 API 拖慢
- **正**: L3 跑得起 — 本地开发者手动 `MAILCODE_TEST_REAL_AGENT=1 pytest` 一次 ~30s 就能验证
- **负**: L3 维护成本 — agent 升级要更新测试 (但这正是其价值)
- **负**: L2 fake binary 是新增测试资产, ~200 行 Python 脚本

## 不采纳"全 L3 真 binary 测试"的理由

- CI 每次跑要花 $0.5-$2 (token 成本) × N 个 PR
- 30+ 测试 × 30s = 15 分钟 CI, 太慢
- API 配额耗尽时 CI 全红, 调试困难
- LLM 输出非确定性, assertion 难写

## 不采纳"全 L1 mock"的当前现实理由

- 本次 design 阶段已暴露 3 个 CLI 假设错误, 都是 mock 抓不到的
- 用户升级 agent CLI 时不会主动跑 mock, 等 PR2 上线才会真 binary 测试
- 失去"升级前主动报警"的第一道防线

## 参考

- pytest markers: https://docs.pystest.org/en/stable/how-to/mark.html
- pytest skip patterns: https://docs.pytest.org/en/stable/how-to/skip.html
- Aider `tests/` 结构: 单元 + integration 分离, integration 默认 skip
- OpenHands: 测试金字塔 4 层 (Unit / Integration / Runtime / End-to-end)
