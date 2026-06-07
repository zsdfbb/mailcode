# 冷启动安全重构 — 执行计划 <!-- [zh] 内部执行计划，未翻译 -->

> 依赖设计: `design-plans/coldstart-security-redesign.md`
> 预计改动: 9 个文件

---

## 执行顺序

```
Phase A: 删除 session_key (易，减少后续冲突)
Phase B: symlink 默认目录 (中，核心安全)
Phase C: 确认邮件 (重，新增功能)
Phase D: 测试 & 文档
```

---

## Phase A: 删除 session_key

### A1. `mailcode/resources/default.json`

删除 `security.session_key` 字段。

### A2. `mailcode/config.py`

- 删除 `get_session_key()` 函数
- 删除 `_ensure_user_config()` 后备字典中的 `session_key` 行
- 新增 `is_coldstart_confirm_enabled()`:
  ```python
  def is_coldstart_confirm_enabled() -> bool:
      config = load_config()
      return config.get("security", {}).get("coldstart_confirm", True)
  ```

### A3. `mailcode/relay/security.py`

- 删除 `validate_session_key()` 静态方法（第 62-78 行）
- 删除 `from mailcode.config import get_session_key` 导入（第 4 行）

### A4. `mailcode/relay/email_listener.py`

`_extract_coldstart_info` 移除 key 解析:

```python
# 之前: 返回 (key, project_dir, command)
# 之后: 返回 (project_dir, command)

def _extract_coldstart_info(self, body: str) -> Tuple[Optional[str], str]:
    lines = body.strip().split("\n")
    project_dir = None
    idx = 0

    if idx < len(lines):
        match = re.match(r"^project:\s*(.+)$", lines[idx].strip(), re.IGNORECASE)
        if match:
            project_dir = match.group(1).strip()
            idx += 1

    remaining = "\n".join(lines[idx:]).strip()
    return project_dir, remaining
```

`_process_new_session` 删除 key 校验:

```python
# 删除:
session_key, email_project_dir, command_body = self._extract_coldstart_info(body)
if not session_key:
    return False, "未找到安全 Key..."
session_key_valid, key_reason = SecurityChecker.validate_session_key(session_key)
if not session_key_valid:
    return False, f"安全 Key 校验失败: {key_reason}"
```

**验证：** 单测通过，`test_coldstart.py` 中 E2E-12/13 更新或删除。

---

## Phase B: symlink 默认目录

### B1. `mailcode/resources/default.json`

```diff
- "default_project_dir": ""
+ "default_project_dir": "~/projects/current"
```

新增 `coldstart_confirm`:
```json
"coldstart_confirm": true
```

### B2. `mailcode/relay/email_listener.py`

在 `_process_new_session` 中，原 key 校验位置之后、bridge 插件部署之前插入：

```python
# --- 工作目录校验 ---
default_dir = os.path.expanduser(
    get_default_project_dir() or "~/projects/current"
)
if not os.path.islink(default_dir):
    return False, (
        f"工作目录符号链接不存在: {default_dir}\n"
        f"请创建: ln -sfn /path/to/your/project {default_dir}"
    )

resolved_dir = os.path.realpath(default_dir)
if not os.path.isdir(resolved_dir):
    return False, f"符号链接指向的目录不存在: {resolved_dir}"

if project_dir:
    project_real = os.path.realpath(os.path.expanduser(project_dir))
    if project_real != resolved_dir:
        return False, (
            f"project: 与当前工作目录不匹配\n"
            f"  邮件指定: {project_real}\n"
            f"  当前目录: {resolved_dir}"
        )

project_dir = resolved_dir
# --- 工作目录校验结束 ---
```

---

## Phase C: 确认邮件

### C1. 新增模板 `mailcode/templates/email_templates/coldstart_confirm.txt`

```
MailCode Remote 命令执行确认
============================

收到您的冷启动请求，请核对以下信息：

  项目: {project_name}
  路径: {project_path}
  分支: {git_branch}
  命令: {command_summary}

确认码: {confirm_code}

---
如需执行，请回复此邮件并在正文中输入：
  confirm: {confirm_code}

此确认码 5 分钟内有效，一次使用。
```

### C2. `mailcode/relay/email_listener.py`

**新增导入：**
```python
import secrets
import subprocess
import time
from dataclasses import dataclass
```

**新增数据结构：**
```python
@dataclass
class PendingColdstart:
    confirm_code: str
    command: str
    project_dir: str
    sender_email: str
    created_at: float  # time.time()
```

**`IMAPListener.__init__` 新增属性：**
```python
self._pending_coldstarts: dict[str, PendingColdstart] = {}
```

**`process_email` 新增 confirm 路由（在 token 判断之前）：**
```python
def process_email(self, email_entry, dry_run=False):
    body = email_entry.get("body", "")

    match = re.search(r'confirm:\s*([a-f0-9]{6})', body, re.IGNORECASE)
    if match:
        return self._process_confirm(email_entry, match.group(1), dry_run)

    token = email_entry.get("token")
    if token:
        return self._process_reply(email_entry, dry_run)
    return self._process_new_session(email_entry, dry_run)
```

**`_process_new_session` 确认分支：**

在 symlink 校验通过、命令安全校验通过后：

```python
if is_coldstart_confirm_enabled():
    confirm_code = secrets.token_hex(3)  # 6 字符

    project_name = os.path.basename(project_dir)
    git_branch = self._get_git_branch(project_dir)

    self._pending_coldstarts[confirm_code] = PendingColdstart(
        confirm_code=confirm_code,
        command=command_body,
        project_dir=project_dir,
        sender_email=sender_email,
        created_at=time.time()
    )

    self._send_confirmation_email(
        sender_email, confirm_code,
        project_name, project_dir, git_branch,
        command_body[:200]
    )
    return True, f"确认邮件已发送，请回复 confirm: {confirm_code}"
```

**新增方法 `_get_git_branch`：**
```python
@staticmethod
def _get_git_branch(project_dir: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5
        )
        branch = result.stdout.strip()
        return branch if branch else "(detached HEAD)"
    except Exception:
        return "(非 git 仓库)"
```

**新增方法 `_send_confirmation_email`：**
```python
def _send_confirmation_email(self, to_email, confirm_code,
                              project_name, project_path,
                              git_branch, command_summary):
    from importlib import resources
    template_path = resources.files("src") / "templates" / "email_templates" / "coldstart_confirm.txt"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        template = "确认码: {confirm_code}\n项目: {project_name}\n命令: {command_summary}"

    body = template.format(
        confirm_code=confirm_code,
        project_name=project_name,
        project_path=project_path,
        git_branch=git_branch,
        command_summary=command_summary
    )
    subject = f"[MailCode] 命令执行确认 — {project_name}"
    self.email_channel.send(to_email, subject, body, None)
```

**新增方法 `_process_confirm`：**
```python
def _process_confirm(self, email_entry, confirm_code, dry_run=False):
    sender_email = email_entry.get("sender_email", "")
    pending = self._pending_coldstarts.pop(confirm_code, None)

    if not pending:
        return False, "确认码无效或已使用"

    if time.time() - pending.created_at > 300:  # 5 分钟
        return False, "确认码已过期"

    if pending.sender_email.lower() != sender_email.lower():
        return False, "确认邮件发送者与原始请求不匹配"

    if dry_run:
        print(f"确认执行\n项目: {pending.project_dir}\n命令: {pending.command}")
        return True, "Dry-run 确认通过"

    # 执行冷启动
    self._ensure_bridge_plugin(pending.project_dir)
    session_name = SessionLauncher.launch(pending.project_dir)
    if not session_name:
        return False, "创建 tmux 会话失败"

    session = self.session_manager.create_session(
        tmux_session=session_name,
        cwd=pending.project_dir,
        question=pending.command[:200],
        response_summary="",
        trace=""
    )
    token = session.get("token")

    from mailcode.relay.injector import CommandInjector
    injector = CommandInjector()
    if not injector.check_agent_alive(session_name):
        SessionLauncher.stop(session_name)
        self.session_manager.delete_session(session.get("id"))
        return False, "Agent 已离线，会话已清理"

    success = injector.inject(session, pending.command)
    if success:
        self.session_manager.increment_command_count(session.get("id"))
        self.session_manager.touch_session(session.get("id"))
        self._record_sent_message(
            email_entry.get("message_id"),
            session.get("id"), token
        )
        self._save_processed_uids()
        self._send_session_created_email(
            sender_email, token, session_name, pending.command[:100]
        )
        return True, f"新会话已创建 (Token: {token})"

    SessionLauncher.stop(session_name)
    self.session_manager.delete_session(session.get("id"))
    return False, "命令注入失败，会话已清理"
```

**定期清理过期 PendingColdstart：**

在 `_listen_poll` 和 `_listen_idle` 的轮询循环中加入：
```python
# 清理过期 pending
now = time.time()
expired = [
    code for code, p in self._pending_coldstarts.items()
    if now - p.created_at > 300
]
for code in expired:
    self._pending_coldstarts.pop(code, None)
```

---

## Phase D: 测试 & 文档

### D1. `tests/unit/test_coldstart.py`

| 旧用例 | 动作 |
|--------|------|
| `test_e2e_11` | 重写：project 不再覆盖，改为验证 symlink |
| `test_e2e_12` (key 错误) | 删除 |
| `test_e2e_13` (key 缺失) | 删除 |
| `test_e2e_14` (命令黑名单) | 适配新函数签名，保留 |
| `test_e2e_15` (opencode 缺失) | 适配，保留 |
| `test_e2e_16` (bridge 部署) | 保留 |
| `test_e2e_17` (project 优先级) | 重写为"project 交叉验证" |
| `test_e2e_18` (不可写路径) | 删除（已由 symlink 校验覆盖） |

新增用例：

| 新用例 | 测试内容 |
|--------|----------|
| `test_symlink_not_exists` | `~/projects/current` 不存在 → 拒绝 |
| `test_symlink_broken_target` | symlink 指向不存在的目录 → 拒绝 |
| `test_symlink_valid` | symlink 有效 → 通过 |
| `test_project_field_mismatch` | `project:` 不匹配当前 symlink → 拒绝 |
| `test_project_field_match` | `project:` 匹配 → 通过 |
| `test_confirm_code_generation` | 确认模式下生成确认码并存储 |
| `test_confirm_code_consumed` | 确认码一次性消费 |
| `test_confirm_code_expired` | 过期确认码被拒绝 |
| `test_confirm_code_wrong_sender` | 非原始发送者回复确认码 → 拒绝 |
| `test_git_branch_detached` | detached HEAD → "(detached HEAD)" |
| `test_git_branch_not_repo` | 非 git 仓库 → "(非 git 仓库)" |

### D2. 文档更新

| 文件 | 改动 |
|------|------|
| `docs/design/design.md` | 更新第 39-48 行冷启动流程；删除 session_key 相关描述；补充 symlink + 确认邮件说明 |
| `AGENTS.md` | 新增 `design-plans/` 和 `exec-plans/` 目录索引 |
| `CHANGELOG.md` | 记录移除 session_key、新增确认邮件、symlink 默认目录 |

---

## 改动文件总览

| # | 文件 | Phase | 类型 |
|---|------|-------|------|
| 1 | `mailcode/resources/default.json` | A, B | 配置 |
| 2 | `mailcode/config.py` | A | 代码 |
| 3 | `mailcode/relay/security.py` | A | 代码 |
| 4 | `mailcode/relay/email_listener.py` | A, B, C | 代码 |
| 5 | `mailcode/templates/email_templates/coldstart_confirm.txt` | C | 模板 |
| 6 | `tests/unit/test_coldstart.py` | D | 测试 |
| 7 | `docs/design/design.md` | D | 文档 |
| 8 | `AGENTS.md` | D | 文档 |
| 9 | `CHANGELOG.md` | D | 文档 |
