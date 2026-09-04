# ADR-003: 配置 Schema — `default_agent` + `agents: {<name>: {...}}` 字典

> 状态: **Accepted** (2026-09-04)
> 决定者: 架构探索 (Zhang Shuai)
> 关联: [`design.md §4.4`](./design.md)

## 背景

多 agent 支持需要在 `~/.config/mailcode/config.json` 里表达"用户当前用哪个 agent / 各 agent 怎么配置"。需要决定 schema 形状。

## 候选方案

### A. 单字段 + per-agent 命令覆盖
```json
{"agent": "pi", "claude_extra_args": [...], "opencode_extra_args": [...], ...}
```
- 缺点：每加一个 agent 要加 N 个配置字段；不通用。

### B. `default_agent` + `agents: {<name>: {...}}` 字典
```json
{
  "default_agent": "claude",
  "agents": {
    "claude": {"command": "claude", "extra_args": ["--dangerously-skip-permissions"]},
    "opencode": {"command": "opencode", "extra_args": ["--auto"], "cwd_strategy": "flag"},
    "pi": {"command": "pi", "extra_args": ["--approve"]}
  }
}
```
- 优点：通用，每 agent 自描述
- 缺点：略复杂，老 config 要兼容

### C. `agents: {<name>: {default: true, ...}}`（用 flag 标记默认）
```json
{"agents": {"claude": {"default": true, "command": "claude"}, "pi": {...}}}
```
- 缺点：必须保证只有一个 `default: true`，schema 校验复杂

### D. 三个独立顶层字段
```json
{"claude": {...}, "opencode": {...}, "pi": {...}, "current": "claude"}
```
- 缺点：跟 B 几乎等价但命名更差（agent name 跟配置混在顶层，键空间污染）

## 决定

**选 B**。

理由：
1. **未来扩展零成本**：v2 加 aider 就 `agents.aider: {...}` 一块，无需改 schema
2. **per-agent 配置完整**：每个 agent 块包含该 agent 特定的字段，互不干扰
3. **校验简单**：只需校验 `default_agent` 是 `agents` 的 key 之一即可
4. **向后兼容**：老 config（无 `agents` 字段）在 `load_config()` 时内存里补全（不写回磁盘）

## 字段最小集

> **2026-09-04 review 易修复**: 原提案包含 `cwd_strategy` 和 `default_model` 两个字段。review 发现这两个字段 v1 用不上（Claude/Pi 都用 subprocess cwd=；model 通过 `extra_args` 透传），保留会误导用户。**v1 schema 只保留以下 4 个字段**。

| 字段 | 必需 | 用途 | 默认值 |
|------|------|------|--------|
| `default_agent` | 否 | 全局默认 agent 名 | `"claude"` |
| `agents` | 否 | 注册的 agent 配置字典 | 内置 `{claude, pi}` 两块 |
| `agents.<name>.command` | 是 | 二进制路径/PATH 名 | (无默认, 启动报错) |
| `agents.<name>.extra_args` | 否 | 每次调附加参数 | claude→`["--dangerously-skip-permissions"]`, pi→`[]` (用户必填 `--provider`) |
| `agents.<name>.timeout_seconds` | 否 | per-agent 默认超时 | (runner 内部默认 86400) |
| `schedules.tasks[].agent` | 否 | 单条 schedule 覆盖 default_agent | (继承 default_agent) |

**校验规则** (在 `validate_serve_config()`):
- `default_agent` 必须是 `agents` 的 key 之一, 否则启动报错
- `schedules.json` 加载时, `tasks[i].agent` 同上 (fail-fast, 不等运行时崩溃)
- v2 加新字段时, `load_config()` 对未知字段 warning 但不报错 (前向兼容)

## 兼容策略

**关键**: 老 config（无 `default_agent` / `agents` 字段）必须**零修改自动工作**。

实现细节（`config.py`）:
```python
def _merge_default_agents(config: dict) -> dict:
    """内存补全 default_agent + agents, 不写回磁盘。"""
    config = dict(config)  # 浅拷贝, 不污染原 dict
    config.setdefault("default_agent", config.get("agent", "claude"))
    config.setdefault("agents", {
        "claude": {"command": "claude", "extra_args": ["--dangerously-skip-permissions"]},
        "opencode": {"command": "opencode", "extra_args": ["--auto"], "cwd_strategy": "flag"},
        "pi": {"command": "pi", "extra_args": ["--approve"]},
    })
    # 兼容老 config 的顶层 agent 字段（legacy）
    return config
```

`load_config()` 调用 `_merge_default_agents()` 后返回补全后的 dict。`config show` 输出补全后的视图。

`mailcode config init` 写入新 schema（`default_agent` + `agents`），但只是默认模板，用户改了啥就存啥。

## 后果

- **正**：schema 通用，加 v2 agent 零成本
- **正**：老用户升级零感知（`load_config()` 自动补全）
- **正**：文档简单（一节写 schema, 后面表格列字段）
- **负**：`config show` 输出比老版本长（多了 3 个 agent 默认块），但这是好事（用户能看到当前所有 agent 的配置）
- **负**：手写一个小 validator 校验 `default_agent` 是 `agents` 的 key ~20 行，但比引入 `jsonschema` 包（破坏零依赖约束）划算

## 不采纳 A 的理由

- 每加一个 agent 字段：`claude_extra_args` / `opencode_extra_args` / `pi_extra_args` / 未来还有 `aider_extra_args` ...
- 用户想隐藏某些 agent 时无法（删除字段 = 失去配置）
- schema 跟 agent name 强耦合

## 不采纳 C 的理由

- `default: true` 多对一约束必须在 validator 里强制（防止用户写两个 true），增加校验复杂度
- 默认值语义不直观（新用户看 config.json 看到三个 agent 块不知道哪个生效）

## 不采纳 D 的理由

- 跟 B 几乎等价但 agent name 散在顶层，未来加 v2 agent 时容易跟其他顶层字段（如 `mailcode_bot` / `security` / `session` / `schedule`）混淆

## 参考

- aider `~/.aider.model.settings.yml` 用 YAML per-model 配置（不适合 MailCode，因为 MailCode 用 JSON 且需要 schema 校验）
- Cline `~/.cline/data.json` 用单一 provider 配置（不适合 MailCode，因为 MailCode 是 multi-agent 不是 multi-provider）
