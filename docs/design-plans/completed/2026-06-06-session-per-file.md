# Session-Per-File 重构 设计计划

## 背景

### 当前状态
- `mailcode/relay/conversation_handler.py` 用单一 `~/.local/share/mailcode/data/conversations/threads.json` 存所有对话线程
- 每条记录的 key = 邮件 Message-ID, value = chat 风格 history (`role: user/assistant` + content)
- 每次 `handle_email` 都**全量读写** threads.json (read → mutate → write 整个文件)
- `thread_id` 在对话过程中**会变** (用户首封 ID → 我们首封回复 ID → 我们第二封回复 ID), 每次切换都要清理旧 key

### 问题
1. **写放大**: 100 个线程时, 处理第 100 封邮件要重写 99 条无关数据
2. **并发隐患**: 全量写无锁, 多进程/多线程下会互相覆盖
3. **删除复杂**: 删除单条 = read-modify-write 全量文件
4. **ID 不稳定**: thread_id 不断变化, 调试/索引/CLI 查询语义混乱
5. **prompt 拼接职责错位**: MailCode 当中间人拼完整 prompt, 需要懂"哪些历史相关 / 摘要 / 截断"等上下文管理决策
6. **`project_dir` 残留**: 早期 OpenCode 集成遗留, 跟"自然语言邮件对话"产品定位不匹配, 但代码里仍然存在 (per-instance, per-session, per-config 三级 fallback)
7. **`system_prompt` 配置**: MailCode config 里塞一个长 system_prompt 字符串, 跟 Claude Code 原生 `CLAUDE.md` 机制重复

### 为什么需要这次变更
- 重构是为支撑**自然语言邮件对话**这个产品定位, 砍掉不属于这个定位的"项目代理"残留
- 让 MailCode 真正变成 "dumb pipe": 收邮件 → 存盘 → 通知 Claude → 发邮件, 不参与任何智能决策
- 把上下文管理、cwd 设置、system prompt 这些事**全部交给 Claude 自己的机制** (CLAUDE.md, Read 工具, file system)
- 改善扩展性: per-session 文件让 N 个 session 的读写互不干扰

## 设计

### 整体方案
**单文件 → 多文件 + 索引**, 加上**用户邮件内嵌控制指令**机制。

```
~/.local/share/mailcode/data/conversations/
├── index.json              # msg_id → session_id 索引 (O(1) 查找)
├── session_<uuid>.json     # 一个 session 一份文件
├── session_<uuid>.json
└── ...
```

### 架构决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| session_id 生成 | `uuid4().hex[:12]` | 跟邮件头解耦, 不受客户端差异影响, 12 位 hex 足够 |
| 创建时机 | 收到第一封邮件立即创建 | 失败可重试不丢历史, 实现简单 |
| 查找机制 | index.json 优先, 全量扫描兜底 | 1000+ session 时 index 仍 O(1), 扫描不依赖索引 |
| 文件格式 | JSON, 每条记录是真实邮件 (from/subject/body/msg_id/in_reply_to/date) | email-centric, 不是 chat-centric; Claude 读起来自然 |
| history 管理 | MailCode 不拼完整 prompt, Claude 用 Read 工具读 session 文件自助 | 上下文管理交给 Claude, MailCode 不知道"什么是历史" |
| cwd 机制 | 用户在邮件正文写 `cwd: <path>`, MailCode 提取后作为 `claude -p` 的 cwd | 用户内嵌控制, MailCode 不需要 config 字段或 CLI 命令 |
| cwd 持久化 | session.cwd 字段, 粘性 (新邮件不指定则沿用) | 一次设置, 整个 session 受益 |
| 默认 cwd | `Path.home()` | 永远有效, 跟 systemd/launchd CWD 解耦 |
| system_prompt | **砍掉 MailCode config**, 走 Claude Code 原生 `cwd/CLAUDE.md` | 单一职责: MailCode 管邮件, Claude Code 管 system prompt |
| project_dir 概念 | **完全砍掉** (per-instance, per-session, per-config 三级 fallback 全删) | 跟"自然语言对话"产品定位不匹配, 越简单越对 |
| TTL | 90 天, 启动时 + CLI 双触发清理, 损坏文件 warn 但保留 | 防膨胀, 不激进, 给用户恢复机会 |
| 错误邮件 | 空 response / claude 失败: 写日志 + 发邮件通知用户 | 不静默, 也不重试 (重试可能再失败) |
| 多 session 路由 | 严格 In-Reply-To 匹配, 无匹配建新 session | 简单可靠, 不做"短时间同 from 合并"这种猜测 |
| CLI 顶层 | `conversation` → `session` (内部命名跟 CLI 一致) | list / show / delete / cleanup 四个子命令 |
| 旧 threads.json | **不迁移** (项目无生产用户) | 干净落地, 不留兼容代码 |

### 数据模型

**session JSON 结构**:
```json
{
  "session_id": "a1b2c3d4e5f6",
  "cwd": "/Users/zs/Projects/MyApp",
  "created_at": 1717500000.0,
  "last_interaction": 1717500000.0,
  "emails": [
    {
      "direction": "incoming",
      "from": "user@example.com",
      "to": "bot@example.com",
      "subject": "项目咨询",
      "body": "你好, 我想咨询...",
      "msg_id": "<abc@user.com>",
      "in_reply_to": null,
      "references": null,
      "date": "2026-06-01T10:23:00Z"
    },
    {
      "direction": "outgoing",
      "from": "bot@example.com",
      "to": "user@example.com",
      "subject": "Re: 项目咨询",
      "body": "你好!",
      "msg_id": "<xyz@mailcode.com>",
      "in_reply_to": "<abc@user.com>",
      "references": null,
      "date": "2026-06-01T10:25:00Z"
    }
  ]
}
```

**index.json 结构**:
```json
{
  "version": 1,
  "updated_at": 1717500000.0,
  "msg_to_session": {
    "<any_incoming_or_outgoing_msg_id>": "<session_id>"
  }
}
```

### 主流程 (`handle_email`)

```
1. 收邮件 (from IMAPListener)
2. 提取 cwd: 从 body 第一行匹配 ^cwd:\s*(.+)$ (大小写不敏感, ~ 展开, is_dir 验证)
3. 剥离 cwd: 把该行从 body 移除后再存
4. 查找/创建 session:
   - 查 index[msg_to_session][in_reply_to] → 找到则 load
   - 找不到则扫描 session_*.json 兜底
   - 都没有则创建空 session (session_id = uuid4().hex[:12])
5. 如果新邮件带 cwd → session.cwd = extracted_cwd
6. 追加 incoming 邮件到 session.emails
7. 保存 session 文件
8. 更新 index: 把 incoming.msg_id → session_id
9. 构建最小 prompt: "用户最新邮件已写入 {session_file}, 请用 Read 工具读取后回复"
10. 调 claude -p, cwd=session.cwd or Path.home()
11. 处理错误:
    - returncode != 0 / FileNotFoundError / TimeoutExpired → 写日志 + 发邮件通知用户
    - response 空字符串 / 全空白 → 写日志 + 发邮件通知用户
12. 追加 outgoing 邮件到 session.emails
13. 保存 session 文件
14. 更新 index: 把 outgoing.msg_id → session_id
15. 发回复邮件 (用 SMTP), 带 In-Reply-To + References 头
```

### Prompt 模板

```python
def _build_prompt(self, session_file_path):
    return f"""用户最新邮件已写入 session 文件: {session_file_path}

请用 Read 工具读取该文件, 了解完整对话上下文 (emails 字段是邮件列表, direction=incoming/outgoing), 然后回复用户最新邮件。

回复内容将作为邮件正文发送, 请用纯文本格式。"""
```

### 关键不变量

- **session.cwd 一旦设置, 整个 session 沿用** (除非新邮件再次带 cwd: 覆盖)
- **outgoing 邮件总是写到 session 文件再 SMTP 发** (失败可重发, 不重调 claude)
- **index 写入失败不影响正确性** (扫描是兜底)
- **损坏的 session 文件不删, 只 warn** (用户可手动恢复)
- **claude 失败 / 空 response 都发通知邮件** (不静默, 不重试)

## 涉及文件

### 新增
- 无 (只是数据文件增加, 不需要新增代码文件)

### 修改
- `mailcode/relay/conversation_handler.py` — **完全重写** (核心)
- `mailcode/relay/email_listener.py` — 微调 (line 356-358 的 ConversationHandler 初始化)
- `mailcode/cli.py` — `cmd_conversation` → `cmd_session`, 完整实现 list/show/delete/cleanup
- `mailcode/config.py` — 删 `CONVERSATION_DEFAULTS.system_prompt`, 加 `SESSION_TTL_DAYS`, 调整 `get_conversation_config` 改名为 `get_session_config`
- `mailcode/resources/default.json` — 同步 config 变更
- `tests/unit/test_conversation_handler.py` — **完全重写** 适配新 API
- `docs/design-final/design.md` — 追加 "Session 管理" 章节, 描述新架构
- `CLAUDE.md` — 更新 `mailcode session` 命令文档, 删除 `mailcode conversation`

### 删除
- `mailcode/templates/conversation_reply.txt` (如有, 走 subprocess 模式后已废弃)
- 概念层面: `project_dir` (instance / session / config 三层全部删除)

## 测试策略

### 测试范围
- **单元测试** (主要): `tests/unit/test_conversation_handler.py` 覆盖新 API
- **手动验证** (必要): 跑 `mailcode serve --once --dry-run` 走完 IMAP 接收 + 处理 + SMTP 发送全流程
- **集成测试**: 现有 `tests/integration/` 端到端测试不需改 (mock 层面已兼容)

### 测试框架与命令
```bash
source .venv/bin/activate
python3 -m pytest tests/unit/test_conversation_handler.py -v
python3 -m pytest tests/ -v
python3 -m ruff check mailcode/ tests/
```

### 验收标准 (可自动化)
- [ ] session 文件 IO 单元测试通过 (创建/读取/更新/删除)
- [ ] index 同步单元测试通过 (增/删/查 msg_id)
- [ ] cwd 提取单元测试通过 (有/无/无效/带 ~ 各种情况)
- [ ] handle_email 主流程单元测试通过 (新建 session / 续接 / claude 失败 / 发送失败 / 错误邮件)
- [ ] TTL 清理单元测试通过 (过期删除 / 损坏文件 warn)
- [ ] 全部现有测试无回归

### 验收标准 (需人工验证)
- [ ] 启动 `mailcode serve --idle` 不报 paths 错误
- [ ] 真实发一封邮件, 收到 Claude 风格回复
- [ ] 回复邮件中, Claude 真的读到了 session 文件 (可见 context-aware 回复)
- [ ] 发 `cwd: /tmp` 的邮件, Claude 引用了 /tmp 下的内容 (如果有)

## 波及文档

- `docs/design-final/design.md` — **追加章节** "Session 管理" 描述新架构 (文件结构 / 主流程 / cwd 机制)
- `CLAUDE.md` — **更新** `mailcode session` 命令列表 (从 conversation 改为 session), 删 `mailcode conversation`

## 风险与注意事项

1. **数据丢失风险**: 这是破坏性重构, 老的 `~/.local/share/mailcode/data/conversations/threads.json` 不再被读取。由于项目无生产用户, 风险为 0。如果未来需要兼容, 加一次性迁移脚本即可
2. **claude -p 工具集假设**: 新设计依赖 `claude -p` 模式下 Read 工具可用。如不工作, 备选方案: 用 `--append-system-prompt` flag 让 MailCode 读 `cwd/CLAUDE.md` 传过去 (退化方案, 暂不实现)
3. **索引同步**: index.json 损坏不影响正确性 (扫描兜底), 启动时不重建索引, 仅在写入时增量更新
4. **路径安全**: 用户邮件里 `cwd: /etc` 这种指令理论上能引导 Claude 读 /etc 内容。但用户已经通过 SPF/DKIM + 白名单认证, 这是邮件发件人自己的指令, 不算攻击面。如果担心, 加路径白名单 (限制在 `$HOME` 子目录) 作为后续 hardening
5. **TTL 误删**: 默认 90 天, 但用户可能想保留重要 session 长期。提供 `mailcode session cleanup --dry-run` 预览, 实际删除前给用户机会
6. **重命名 CLI 破坏性**: `mailcode conversation` → `mailcode session` 是 breaking change。考虑到无生产用户, 直接改名不做 alias
7. **cwd 字段的边界情况**: 用户写 `cwd: ./relative` 时, 相对路径相对什么解析? 决定: 相对 `Path.cwd()` (即 MailCode 启动时的 CWD), 因为 `is_absolute()` 检查后会用 `.absolute()` 补全
