# ADR-002: Session 数据按 Agent 命名空间 + 软迁移策略

> 状态: **Accepted** (2026-09-04)
> 决定者: 架构探索 (Zhang Shuai)
> 关联: [`design.md §4.5 / §4.6`](./design.md)

## 背景

现状 MailCode 在 `~/.config/mailcode/` 下保存三类数据：
- `claude_sessions.json`（ResumeConversationHandler 的 msg_id → claude_session_id 映射）
- `transcripts/<uuid>.json`（对话归档，ResumeConversationHandler）
- `conversations/session_<id>.json` + `index.json`（ConversationHandler 的 per-file session）

多 agent 支持后需要按 agent 隔离这三类数据，否则：
1. OpenCode / Pi 的 session_id 和 Claude 的混在同一个 mapping 文件里无法区分
2. transcripts 里混着不同 agent 的对话记录，调试时无法分辨
3. 升级用户担心"我的现有数据会不会被覆盖/丢失"

## 候选方案

### A. 命名空间文件（每个文件带 agent 前缀）
- `claude_sessions.json` → `sessions/claude.json` + `sessions/opencode.json` + `sessions/pi.json`
- 优点：单一目录，文件少
- 缺点：未来加 agents.claude / transcripts / conversations / sessions 四类 × N 个 agent 文件散乱

### B. 按 agent 子目录（每 agent 独立目录）
```
~/.config/mailcode/
├── claude/
│   ├── sessions.json
│   ├── transcripts/<uuid>.json
│   └── conversations/session_<id>.json + index.json
├── opencode/
└── pi/
```
- 优点：每 agent 数据物理隔离，新加 agent = 新建目录
- 缺点：目录层级更深，迁移要移动大量文件

### C. 不分目录，文件名前缀
- `claude_sessions.json` + `opencode_sessions.json` + `pi_sessions.json`
- transcripts/conversations 同理
- 优点：扁平，跟现状最接近
- 缺点：3 类 × N 个 agent = 文件爆炸，未来加 field 时命名冲突风险

### D. 不改路径，混存 + JSON 内加 agent 字段
- 仍用 `claude_sessions.json` 但内部每条记录加 `"agent": "opencode"`
- 优点：老路径完全不动
- 缺点：耦合严重，读写逻辑变复杂

## 决定

**选 B：按 agent 子目录**。

理由：
1. **物理隔离**：OpenCode 和 Pi 的 mapping 文件、transcripts、conversations 互不干扰，未来加 v2 agent（如 aider）就是新建 `<home>/aider/` 目录
2. **可清理性**：删除某 agent 数据 = 删对应目录，原子且安全
3. **跟 MailCode 现状的"配置 + 数据"模型一致**：`~/.config/mailcode/` 下既有文件又有子目录（`conversations/`、`transcripts/`、`schedules.json`、`state.json`）
4. **handler 代码对称**：每个 handler 内部所有路径都通过 `paths.agent_home(self.agent_name)` 派生，统一且易改

## 迁移策略（关键）

**不能简单"启动时自动迁移"** — 风险：
- 用户可能有 cron 脚本依赖老路径
- 自动迁移出错时无法回滚
- 用户升级时若不清楚改动会困惑

**采用"显式子命令 + dry-run 默认 + 30 天 grace period"组合**：

```bash
mailcode config migrate-agents [--apply] [--agent NAME]
```

行为：
1. **Dry-run 默认**：列出将移动的所有文件 + 目标路径 + 总数，不动磁盘。
2. **`--apply` 才执行**：移动文件到新路径，写 marker 文件 `~/.config/mailcode/legacy-claude-migration.json` 记录时间戳 + 清单。
3. **30 天 grace**：迁移完成后，老路径创建 symlink 指向新路径。30 天内任何 handler 都能从老路径读（防御性兜底），30 天后 marker 超期，`mailcode serve` 启动时删 symlink。
4. **每封邮件的双路径读兼容**：handler 内部 `_load_X()` 先尝试新路径，失败回退老路径。这样即使迁移出错或用户没迁移，新代码也能读老数据。

## 后果

- **正**：每个 agent 数据完全隔离，调试/审计简单
- **正**：未来加新 agent 不影响现有数据
- **正**：旧用户升级时零感知（除非主动 `--apply` 迁移）
- **负**：路径深度增加（`<home>/claude/conversations/session_xxx.json`），文档要清楚写
- **负**：迁移工具代码 ~150 行（dry-run + apply + marker + symlink + cleanup），但只写一次

## 不采纳 A 的理由

- `sessions/claude.json` / `sessions/opencode.json` 这种设计在 N=3 时还行，未来加 v2 agent（如 aider）时 `sessions/aider.json` 又会跟 `conversations/aider/...` 形成两套命名风格不一致
- 实际场景下，transcripts/conversations/sessions 三类文件本就该聚在一起（B 比 A 自然）

## 不采纳 C 的理由

- 扁平命名导致 `claude_sessions.json` / `opencode_sessions.json` / `pi_sessions.json` 这种命名容易冲突（如未来加 `claude_sessions_v2.json` 之类的版本字段）
- 不利于用户"删除某个 agent 的所有数据"操作

## 不采纳 D 的理由

- `claude_sessions.json` 内含 `"agent": "opencode"` 条目的设计本身就是"用什么名字放什么数据"的错位
- 读写逻辑要按 agent 分流，代码复杂度上升
- 完全不解决"调试时数据散乱"的问题

## 参考

- MailCode 现有 `~/.config/mailcode/` 结构本身已经是"配置 + 子目录"模式
- aider 的 `~/.aider.model.settings.yml` 用 YAML 配置而非目录隔离（不适合 MailCode，因为 MailCode 是"per-session 数据"而非"per-config 数据"）
