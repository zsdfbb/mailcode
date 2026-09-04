# ADR-004: 保留 `call_claude()` 函数为向后兼容 shim

> 状态: **Accepted** (2026-09-04)
> 决定者: 架构探索 (Zhang Shuai)
> 关联: [`design.md §4.2 / §4.10`](./design.md)

## 背景

现状 MailCode 有 5 处直接调用 `mailcode.utils.claude_runner.call_claude()`：
- `conversation_handler.py:426`
- `resume_handler.py:236`
- `stateless_handler.py:63`
- `scheduler.py:727`
- `cli_chat.py:52`

加上 `tests/unit/conftest.py` 里大量 `mock_claude` / `mock_opencode_available` fixture 间接 mock 这个函数。总共 47 个 unit test 引用 `call_claude` 或其 mock 链。

多 agent 重构时，需要决定这 5 处 + 47 个测试是否一次性迁移到新抽象。

## 候选方案

### A. 全量迁移：删除 `call_claude`，5 处全改成 `runner.call(...)`
- 优点：彻底，代码无 shim 冗余
- 缺点：单个 PR diff 大（5 处 + 47 个测试），review 困难，回滚成本高

### B. 保留 `call_claude` 为 shim，5 处零修改
- 优点：5 处调用点不变，47 个测试不变，PR diff 最小
- 缺点：代码有"过渡期冗余"（shim + 新抽象并存）

### C. 保留 `call_claude` 但加 deprecation warning
- 优点：明确告诉用户"这函数即将移除"
- 缺点：每次调用都打 warning，日志噪音

### D. 两阶段 PR（PR1 引入新抽象 + 保留 shim，PR2 逐步迁移调用点）
- 优点：每 PR diff 小，review 容易，可独立 revert
- 缺点：中间状态长一段时间（约 1-2 周）

## 决定

**选 B + D 组合：保留 `call_claude` 为薄 shim，且分两阶段 PR。**

### 实现

`mailcode/utils/claude_runner.py` 改造后:

```python
# 老 API — 保留, 等价于 ClaudeRunner().call()
def call_claude(prompt, cwd="", *, session_id=None, resume=False, timeout=None):
    """兼容 shim — 新代码请用 ClaudeRunner().call() 或 get_runner('claude').call()"""
    return ClaudeRunner().call(prompt, cwd,
                               session_id=session_id, resume=resume, timeout=timeout)


# 新 API
class ClaudeRunner(BaseAgentRunner):
    name = "claude"
    def _command(self): return "claude"
    def _build_args(self, prompt, cwd, *, session_id, resume, no_session, timeout):
        args = ["claude", "--dangerously-skip-permissions"]
        if session_id is not None:
            args.extend(["--session-id", session_id])
        if resume:
            args.append("--resume")
        return args
    def hint_for_failure(self) -> str:
        return "Claude Code 未安装或不在 PATH 中。请先安装 Claude Code, 或检查 PATH 配置。"
```

5 个调用点**第一阶段不变**。第二阶段 PR 才把每个调用点改成：
```python
# 旧: cr_module.call_claude(prompt, cwd)
# 新: get_runner("claude").call(prompt, cwd)
```

## 理由

1. **零行为变化的第一阶段**：PR1 提交后, 47 个测试立即验证旧代码路径与新抽象行为一致（因为 shim 走新抽象, 而新抽象实现就是从老 `call_claude` 函数体迁移来的）。
2. **diff 最小**：PR1 ~200 行（新增 agent.py + claude_runner 重写 + 默认 config），PR2 ~600 行（5 处调用点 + OpenCodeRunner + PiRunner + migrate）。
3. **可独立 revert**：PR1 出问题 revert 后代码完全等于 master 状态（因为 shim 等价老函数）。PR2 出问题 revert 只丢多 agent 支持, 不会破坏 Claude-only 路径。
4. **保留迁移灵活性**：PR2 里每个调用点可以独立 commit（5 个 commit, 一个文件一个），review 粒度细。

## 后果

- **正**：PR diff 小, review 容易, 回滚安全
- **正**：老用户升级 PR1 后零行为变化, 风险低
- **正**：v1 过渡期代码清晰（shim 只在 claude_runner.py 一个文件, 容易识别"还没迁移的调用点"）
- **负**：shim 长期存在（约 1-2 周 PR2 完成期）。`grep call_claude` 仍能命中, 但都是 shim 调用不是直接 subprocess。
- **负**：开发者可能误以为 `call_claude` 是"推荐 API"。需要在 docstring 明确说"deprecated, use ClaudeRunner or get_runner('claude')"。

## 不采纳 A 的理由

- 单 PR diff 大（5 调用点 + 47 个测试）
- review 困难（一改 50 处, 看不清到底改了啥）
- 回滚时如果只想回滚一部分（保留多 agent 但回滚某个调用点的迁移）做不到

## 不采纳 C 的理由

- `warnings.warn` 在每次调用时打日志, 噪音太大（每天可能上百封邮件）
- 即使 deprecation, 在用户没主动迁移的情况下不可能删（破坏向后兼容）
- 实际上 shim 比 deprecation 更干净 — shim 永远兼容, 用户根本不需要知道

## 不采纳"激进删除 call_claude"的理由

- 47 个 unit test 直接依赖 `call_claude` 的 import / mock 路径, 一次性全改风险高
- 如果 v2 之后真有用户 fork 项目想回到单 agent 模式, 老代码路径得留

## 后续清理

PR2 完成后，建议:
1. 把 5 个调用点改完
2. 在 `claude_runner.py` docstring 加 "deprecated, use `get_runner('claude').call()`"
3. 不删除 shim（永远留着, 标记 deprecated）

若 v3 决定彻底清理 shim, 需要一次性迁移所有测试 + 调用点, 风险更高, 但代码更纯净。v1/v2 不做。

## 参考

- Django `django.utils.translation.ugettext_lazy` 在 Django 4.0 标记 deprecated 但仍保留 2 个版本后才删
- Python 标准库 `os.path` 在 Python 3.12+ 仍保留旧 API, 同时推荐 `pathlib` 路径
