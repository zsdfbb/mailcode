# 执行计划 — tmux 会话回收改进 <!-- [zh] 内部执行计划，未翻译 -->

每个任务可独立验证，按顺序完成。每完成一个任务后运行对应测试。

---

## 任务 1：新增配置项 + Session 字段

**改动文件：**
- `mailcode/config.py` — 新增 `get_idle_timeout_minutes()` 读取 `email.session_idle_timeout_minutes`
- `mailcode/session/manager.py` — `create_session()` 写入 `last_active_at` 字段

**验证：**
- 新增单元测试：`SessionManager.create_session()` 包含 `last_active_at` 且等于 `created_at`
- `get_idle_timeout_minutes()` 默认值返回 30，配置缺失不抛异常

---

## 任务 2：注入时更新 last_active_at

**改动文件：**
- `mailcode/session/manager.py` — 新增 `touch_session(session_id)` → 更新 `last_active_at = now`
- `mailcode/relay/email_listener.py` — `_process_reply()` 和 `_process_new_session()` 注入成功后调用 `touch_session()`

**验证：**
- 单元测试：调用 `touch_session()` 后 `last_active_at` 被更新为当前时间
- pipeline 测试：注入成功后 session JSON 中 `last_active_at` > `created_at`

---

## 任务 3：注入前 Agent 存活性检查

**改动文件：**
- `mailcode/relay/injector.py` — 新增 `check_agent_alive(session_name) → bool`，复用 `tmux capture-pane` + `is_agent_active` 模式
- `mailcode/relay/email_listener.py` — 在 `_process_reply()` 注入前调用检查

**验证：**
- 单元测试：mock tmux 输出，验证 alive/dead 判断
- 集成测试：kill agent inside tmux → 注入返回 False

---

## 任务 4：扩展 _health_check()

**改动文件：**
- `mailcode/session/manager.py` — 重命名 `_cleanup_expired()` → `_health_check()`（保持向后兼容），
  新增闲置超时检查（`idle_timeout_minutes`）、命令数上限主动回收
- `mailcode/relay/email_listener.py` — 两处调用点（`_listen_poll` / `_listen_idle`）改为调用 `_health_check()`

**验证：**
- 单元测试：idle 超过 30 分钟的 session 被标记为 expired（复用 `_is_session_expired` 逻辑扩展）
- 单元测试：命令数达到 max_commands 的 session 被 `_health_check()` 清理
- 集成测试：模拟多个 session 不同状态，验证只清理符合条件的

---

## 任务 5：启动恢复（全量健康检查）

**改动文件：**
- `mailcode/session/manager.py` — 新增 `recover_orphans()` 方法，遍历所有 session JSON，
  - tmux session 不存在 → 删除 JSON
  - tmux session 存在但 agent 已死 → kill + 删除 JSON
  - 正常 session → 不动
- `mailcode/relay/email_listener.py` — `__init__()` 末尾调用 `recover_orphans()`

**验证：**
- 单元测试：mock session JSON + mock tmux 不存在 → 删 JSON
- 单元测试：mock session JSON + mock tmux 存活但 pane 无 agent → kill + 删 JSON
- 单元测试：mock session JSON + mock tmux 存活 + agent 正常 → 不删

---

## 任务 6：统一 _process_reply 清理逻辑

**改动文件：**
- `mailcode/relay/email_listener.py` — `_process_reply()`:
  - `session_exists()` 返回 False → `delete_session()` 清理孤儿
  - `inject()` 返回 False → 检查 tmux 是否还活着，死则清理
  - 清理后打 log

**验证：**
- 单元测试：模拟 session JSON 存在但 tmux 不存在 → 注入失败 + JSON 被删除
- 单元测试：模拟注入到死 pane 失败 → JSON 被删除

---

## 任务 7：回调注入失败时发送错误邮件

**改动文件：**
- `mailcode/relay/email_listener.py` — `_process_reply()` 和 `_process_new_session()` 失败时调用
  `_send_error_email(sender_email, reason)`
- 新增简单错误模板

**验证：**
- 单元测试：任意失败路径调用 `email_channel.send()`（mock SMTP）
- pipeline 测试：冷启动 key 错误 → 发错误邮件

---

## 任务 8：全量回归测试

**执行：**
```bash
venv/bin/python3 -m pytest tests/unit/ -v --tb=short
venv/bin/python3 -m pytest tests/integration/ -v --tb=short
```

**验证：** 所有现有测试 + 新增测试通过，无回归。

---

## 任务 9（延后）：Scheduler 接入 email_listener

- `email_listener.py` 中解析 cron/schedule 邮件字段
- `_inject_callback` 绑定到 `CommandInjector`
- 新建 scheduled task 或更新现有

**验证：** 集成测试：发 cron 邮件 → 等待触发 → 验证命令注入
