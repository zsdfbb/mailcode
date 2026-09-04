# ADR-001: Agent Runner 抽象选型 — ABC + 模块级 dict

> 状态: **Accepted** (2026-09-04)
> 决定者: 架构探索 (Zhang Shuai)
> 关联: [`design.md §4.2`](./design.md)

## 背景

需要让 MailCode 同时支持 Claude Code / OpenCode / Pi 三种 agent，未来可能加更多。需要选择 runner 抽象的实现方式。

## 候选方案

### A. `abc.ABC` + 模块级 dict 注册表
- `BaseAgentRunner(abc.ABC)`，模板方法 `call()` + 子类重写 `_build_args()`
- `AGENTS: dict[str, BaseAgentRunner]` 在 `mailcode/utils/agent.py` 模块级
- 加新 agent = 1 新文件 + 1 行注册
- 共享 `_run_subprocess()` 模板，消除 3 份 subprocess 错误处理代码

### B. `typing.Protocol` + 模块级 dict
- 不强制继承，纯鸭子类型
- 协议即契约，无共享代码
- 加新 agent = 1 新文件 + 1 行注册
- 缺点：每个 runner 都得复制 subprocess 错误处理模板

### C. `abc.ABC` + `importlib.metadata.entry_points`
- 内置 plugin 发现机制
- 第三方包可注册新 agent
- 缺点：v1 不需要 plugin 系统，引入 entry_points 让部署变复杂（要装 wheel 才能让 mailcode 找到 agent）

## 决定

**选 A：ABC + 模块级 dict。**

理由：
1. **共享模板消除重复**：`_run_subprocess()` 的 5 个 try/except 分支（TimeoutExpired、FileNotFoundError、OSError、returncode、stdout）写一次就够。Protocol 派每个 runner 都会复制粘贴一遍。
2. **零第三方依赖**：模块级 dict 是 stdlib 基础功能，entry_points 需要第三方包机制（wheel 安装、metadata 读取）。
3. **可演进到 entry_points**：v1 用 dict，v2 真有第三方 plugin 需求时，把 `AGENTS` dict 替换为 `entry_points(group="mailcode.agents")` 即可，handler 代码无需改动（`get_runner(name)` 接口不变）。
4. **可测试性**：每个 Runner 单独测试 `_build_args()` 的精确 argv 形状，模板方法在 `test_agent_runner.py` 一次覆盖。

## 后果

- **正**：~450 行新代码（含 3 个 Runner + 基类 + 注册表）替代原本 ~70 行的 `call_claude`。代码总量下降 0，行数转移到通用模板。
- **正**：所有 runner 行为一致（错误处理、日志格式、超时语义）。
- **负**：每个 Runner 子类比 Protocol 实现多 5 行（重写 `_build_args` + `_command` 等）。
- **负**：v1 不支持第三方插件。如果未来有需求，得做 entry_points 迁移。

## 不采纳 B 的具体理由

- **代码重复**：subprocess 错误处理 5 个分支 × 3 个 runner = 15 处重复代码。新增第 4 个 agent 时还得复制一遍。
- **类型提示弱**：Protocol 用 `typing.runtime_checkable` 才能做 isinstance 检查，没运行时检查就只能靠测试覆盖，缺安全感。
- **可扩展性边际效益小**：Protocol 的"无强制继承"优势在 3 个固定 agent 时不明显，在 30+ agent 时才凸显（v2 再考虑）。

## 不采纳 C 的具体理由

- **零第三方依赖约束**：CLAUDE.md 明确 MailCode 主体零运行时第三方依赖。entry_points 本身不引入依赖，但鼓励第三方包注册 → 用户体验上"装 mailcode 后还得装额外包才能用新 agent"，跟 MailCode 的"装一个就够"理念冲突。
- **v1 需求不明确**：当前没有"第三方想贡献 agent runner"的需求。预先构建就是 over-engineering（YAGNI）。

## 参考

- Cline `src/api/index.ts` — 类似 Cline 用工厂 + adapter 模式
- Herdr `agents/registry.py` — 模块级 agent 注册 dict
- Aider LiteLLM — 拒绝引入，太重
