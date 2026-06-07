# 集中配置和数据文件 设计计划

## 背景
MailCode 的配置文件和数据文件当前分散在两个独立的用户目录:
- 配置: `~/.config/mailcode/` (config.json, test_config.json, relay.log, projects/)
- 数据: `~/.local/share/mailcode/data/` (conversations/, sent-messages.json)

这种割裂导致:
- 用户排查问题时要查两个目录
- `sent-messages.json` 文件名严重名不副实,实际存储的是 listener 状态(uids 去重集 + sent_messages 反自循环记录)
- `~/.config/mailcode/projects/` 是个死路径,对应的 `get_default_project_dir()` 和 `get_projects_dir()` 函数定义了但生产代码从不读
- 部分 XDG 规范被破坏(配置和状态混居)

**前提**: 项目仍在开发中,无生产用户,因此不考虑历史数据迁移。

## 设计

### 整体方案
把所有 MailCode 文件集中到 `~/.config/mailcode/` 一个目录,采用扁平布局,同时清理死代码和误导性命名,做到"内外一致 + 自解释"。

### 架构决策
1. **根目录保持 `~/.config/mailcode/`**: 不切到 `~/.local/share/mailcode/`,理由是 `~/.config/mailcode/` 已存在,改成单点合并成本最低。
2. **不集中路径常量**: 按用户偏好"默认写死",`_MAILCODE_HOME` 常量在 `conversation_handler.py` 和 `email_listener.py` 各自定义,不抽到 `config.py`。
3. **state.json 重命名**: 反映真实职责(listener 状态),不是"sent messages"。
4. **state.json JSON 键重命名**: `uids` → `processed_uids`,`messages` → `sent_messages`,做到自解释。
5. **方法/属性全面重命名**: `sent_messages_path` → `state_path`,`_load_processed_uids` → `_load_state`,`_save_processed_uids` → `_save_state`,`_cleanup_old_messages` → `_prune_old_sent_messages`,`_is_already_sent` → `_is_duplicate`。方法名按"描述数据行为"原则,JSON 键和文件名按"自解释"原则。
6. **清理死代码**: `get_projects_dir()` / `get_default_project_dir()` / 3 个相关单测 / `default_project_dir` 字段在所有配置模板和 install.sh 里的写入逻辑,全部删除。

### 数据流 / 接口变更
无对外接口变化。`_is_already_sent` 改成 `_is_duplicate` 是 internal-only。

### 目录布局 (最终)
```
~/.config/mailcode/
├── config.json              # 现有,不动
├── test_config.json         # 现有,不动
├── relay.log                # 现有,不动
├── conversations/           # 移自 ~/.local/share/mailcode/data/conversations/
│   ├── index.json
│   └── session_<id>.json
└── state.json               # 新(从 sent-messages.json 改名 + 改位置)
    # 内含: { "processed_uids": [...], "sent_messages": [{message_id, sent_at}, ...] }
```

## 涉及文件

### 修改
- `mailcode/config.py` — 删 `get_default_project_dir()`(L171-175)、`get_projects_dir()`(L178-179);删 L51/L57 内联 `default_project_dir` 字段
- `mailcode/resources/default.json` — 删 L6 `_notes` + L10 字段
- `mailcode/cli.py` — 删 L167 测试模板里的 `default_project_dir`
- `mailcode/relay/conversation_handler.py` — 改 L15 路径常量;改 L127 docstring
- `mailcode/relay/email_listener.py` — 改 L27 路径;重命名 `self.sent_messages_path` → `self.state_path`(L37 + 5 处引用);重命名 4 个方法;改 JSON 键 `uids`→`processed_uids`、`messages`→`sent_messages`
- `install.sh` — 删 L129-140 整段 python3 -c 块(写 default_project_dir)
- `uninstall.sh` — 删 L51 DATA_DIR 定义 + 删 L121-132 删除 DATA_DIR 块 + 删 L150+ 同类块(若存在)
- `tests/unit/conftest.py` — 改 L9 docstring;删 L38、L71 fixture 里的 `default_project_dir`
- `tests/unit/test_config.py` — 删 L6 import 中的 `get_default_project_dir`;删 L205-241 三个测试函数
- `docs/design-final/design.md` — 更新所有 `sent-messages.json` 和 `~/.local/share/mailcode/` 引用(6+ 处)

### 删除
- 无文件删除(只是清理函数和字段)

## 测试策略
- **单测**:
  - 运行 `pytest tests/unit/ -q` 全套确认无回归
  - 删除了 `get_default_project_dir` 的 3 个测试(Q6 决策),其他测试不应受影响
  - 检查 `test_email_channel.py` / `test_listener_lifecycle.py` 是否间接引用 `sent_messages_path` 或 `default_project_dir`(grep 验证后已确认无)
- **Lint**: `ruff check mailcode/ tests/` 通过
- **手动验证**(可选): `mailcode config init --force` 看是否正常生成 `config.json`(不带 `default_project_dir`)

## 波及文档
- `docs/design-final/design.md` — 6+ 处路径/文件名引用需更新

## 风险与注意事项
- **无生产用户**: 项目在 dev,不需迁移逻辑,但需在 commit message / report 中明确"老 `~/.local/share/mailcode/` 数据被遗弃"
- **uninstall.sh DATA_DIR 块**: 删除时要确保不会误删 `CONFIG_DIR`(已通过 grep 验证 `DATA_DIR` 只在 L51 定义,L121-132 使用,L150+ 同类块)
- **方法重命名一致性**: `_load_state` 现在加载整个 state struct(含 processed_uids + sent_messages),`self.processed_uids` 这个 in-memory 字段仍保留(set 类型),只是来源换成 state.json 的 `processed_uids` 键
