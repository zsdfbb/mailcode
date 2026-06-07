# 2026-06-07 集中配置和数据文件 重构报告

## 概述
将 MailCode 的配置文件 (`~/.config/mailcode/`) 和运行时数据 (`~/.local/share/mailcode/data/`) 合并到 `~/.config/mailcode/` 一个目录,采用扁平布局。同时清理死代码、修复误导性命名,做到代码内外一致 + 自解释。

**前提**: 项目仍在开发中,无生产用户,不需数据迁移。

## 涉及文件

### 代码修改
| 文件 | 变更 |
|------|------|
| `mailcode/config.py` | 删 `get_default_project_dir()` 和 `get_projects_dir()` 两个死函数;删内联 `default_project_dir` 字段 |
| `mailcode/resources/default.json` | 删 `_notes` 和字段里的 `default_project_dir` |
| `mailcode/cli.py` | 删测试配置模板里的 `default_project_dir` 字段 |
| `mailcode/relay/conversation_handler.py` | `_DATA_DIR` → `_MAILCODE_HOME`, 路径 `~/.local/share/mailcode/data` → `~/.config/mailcode`;docstring 同步 |
| `mailcode/relay/email_listener.py` | 路径常量改名 + 文件 `sent-messages.json` → `state.json`;属性 `sent_messages_path` → `state_path`;4 个方法重命名 (`_load_state` / `_save_state` / `_prune_old_sent_messages` / `_is_duplicate`);JSON 键 `uids` → `processed_uids`、`messages` → `sent_messages`;新增内存字段 `self.sent_messages: list` 与 `self.processed_uids: set` 对称 |
| `tests/unit/conftest.py` | docstring 改新路径;fixture 里删 `default_project_dir` 残留 |
| `tests/unit/test_config.py` | 删 3 个 `test_get_default_project_dir_*` 测试和对应 import |
| `tests/unit/test_listener_lifecycle.py` | 跟随 `_save_state` 重命名,2 处 `patch.object` 更新 |
| `install.sh` | 删写 `default_project_dir` 的 python3 -c 块 |
| `uninstall.sh` | 删 `DATA_DIR` 变量 + 两处删除 DATA_DIR 的 if 块 (数据已合入 CONFIG_DIR) |
| `docs/design-final/design.md` | 9 处路径/文件名引用更新, §12.2 段重组为新扁平布局 |

### 新增字段
- `IMAPListener.sent_messages: list = []` (in-memory 缓存 `state.json` 的 sent_messages 键,避免每次 save 重读磁盘)

## 最终目录布局
```
~/.config/mailcode/
├── config.json              # 配置 (用户编辑)
├── test_config.json         # 集成测试配置
├── relay.log                # 运行日志
├── conversations/           # 会话数据 (per-file + index)
│   ├── index.json
│   └── session_<id>.json
└── state.json               # IMAP listener 状态
    # { processed_uids: [...], sent_messages: [{message_id, sent_at}, ...] }
```

## 验证结果

### 单测
```
$ pytest tests/unit/ -q
........................................................................ [ 34%]
........................................................................ [ 68%]
.................................................................        [100%]
209 passed in 0.19s
```

### Lint
```
$ ruff check mailcode/ tests/
All checks passed!
```

### Bash 语法
```
$ bash -n install.sh uninstall.sh
(no output, exit 0)
```

### Import
```
$ python3 -c "import mailcode; print(mailcode.__version__)"
0.3.0
```

### 残留检查
```
$ grep -rn "sent-messages\|sent_messages_path\|get_default_project_dir\|get_projects_dir" mailcode/ tests/ install.sh uninstall.sh
(无结果)

$ grep -rn "default_project_dir" mailcode/ tests/ install.sh uninstall.sh
(无结果)
```

## 已知遗留

### 文档历史
- `docs/exec-plans/completed/2026-05-31-config-fix.md` 含历史 `default_project_dir` 引用
- `docs/design-plans/completed/coldstart-security-redesign.md` 含历史引用
- `docs/reports/2026-06-03-conversation-mode-report.md` 含旧 `~/.local/share/mailcode/` 引用
- `docs/design-plans/completed/2026-06-06-session-per-file.md` 含旧路径引用

这些是归档的历史文档,保留原样作为决策记录,**不修改**(符合"completed 目录不动"惯例)。

### 旧数据遗弃
- 老 `~/.local/share/mailcode/data/` 目录被遗弃,无迁移逻辑 (项目无生产用户)
- 旧 `~/.local/share/mailcode/data/sent-messages.json` 内容无人读取
- 用户如有旧数据可手动 `rm -rf ~/.local/share/mailcode`

## 设计决策回顾

1. **根目录保持 `~/.config/mailcode/`**: 不切到 `~/.local/share/mailcode/`,降低迁移成本。
2. **不集中路径常量**: 按用户偏好"默认写死",`conversation_handler.py` 和 `email_listener.py` 各自定义 `_MAILCODE_HOME`。
3. **state.json 改名**: 反映真实职责 (listener 状态),不是"sent messages"。
4. **JSON 键重命名**: `processed_uids` 和 `sent_messages` 一眼可懂。
5. **方法全面重命名**: 内部 API 与新文件名/键名一致,无命名错位。
6. **死代码全清**: `get_default_project_dir()` + 3 个测试 + `default_project_dir` 字段全部删除。

## 自我评估
- 改动总量适中 (~12 个文件),每个改动都是单点替换或局部重命名
- 5 个 subagent 并发执行,无中断、无失败
- 全量测试一次通过 (209 passed)
- ruff 一次通过
- 一次到位,无需重试
