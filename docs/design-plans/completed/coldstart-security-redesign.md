# 冷启动安全重构设计 <!-- [zh] 内部设计文档，未翻译 -->

> 状态: 设计阶段 · 目标版本: 待定
> 关联: `docs/design/design.md` 第 39-48 行冷启动流程

---

## 1. 动机

当前冷启动存在三个安全问题和一个体验痛点：

| 问题 | 严重度 | 说明 |
|------|--------|------|
| 工作目录无限制 | 高 | 邮件 `project:` 可指定任意路径，系统会往该路径写入 bridge 插件并以此作为 tmux 工作目录 |
| 无执行确认 | 中 | session_key 校验通过后直接创建会话并执行命令，无退路 |
| `$HOME` 兜底回退 | 低 | `default_project_dir` 未配置时回退到 `$HOME`，不经意间暴露家目录 |
| session_key 体验差 | — | 每次冷启动需复制粘贴 16 位 hex，且邮箱泄露场景下它是弱防护 |

---

## 2. 安全模型演进

### 之前

```
邮件到达
  → DKIM/SPF 校验
  → sender 白名单
  → session_key        ← 移除
  → command 黑名单
```

### 之后

```
邮件到达
  → 确认邮件（两阶段，意图确认）      ← 新增
  → symlink 目录限制（~/projects/current） ← 新增
  → DKIM strict（建议 auth_policy: "strict"）
  → sender 白名单
  → command 黑名单
```

移除 `session_key` 的理由：确认邮件 + DKIM strict 组合已等效覆盖其唯一有效防护场景（邮箱账户泄露）。即使攻击者拥有邮箱密码发送冷启动邮件，确认回复也会回到你的收件箱，你会看到并拒绝。

---

## 3. 设计详情

### 3.1 符号链接默认目录

用户创建一个固定路径的符号链接，daemon 仅认可这个入口：

```
~/
  projects/
    current → /Users/zs/Develop/MailCode   # 用户手动切换项目时更新
```

**配置（`default.json`）：**

```json
{
  "email": {
    "default_project_dir": "~/projects/current"
  }
}
```

**冷启动校验逻辑：**

```
1. default_dir = expanduser("~/projects/current")
2. os.path.islink(default_dir) → 否: 拒绝
3. real_dir = os.path.realpath(default_dir)
4. os.path.isdir(real_dir)     → 否: 拒绝
5. 若邮件提供 project: 字段:
     project_real = realpath(expanduser(email_project))
     project_real == real_dir  → 否: 拒绝
6. project_dir = real_dir
```

**语义变更：**

| 内容 | 之前 | 之后 |
|------|------|------|
| `project:` 在邮件中 | 覆盖工作目录 | 可选交叉验证（提供则必须匹配） |
| 兜底回退 | `$HOME` | 无，symlink 不存在则拒绝 |
| `default_project_dir` | 空字符串 | `"~/projects/current"` |

### 3.2 执行前确认邮件（两阶段冷启动）

**阶段一 — 发起请求：**

用户发送冷启动邮件：

```
project: MailCode
npm run test
```

系统：
1. 校验 symlink 目录
2. 校验命令安全
3. 采集项目上下文（名称、路径、git 分支）
4. 生成 6 字符 hex 确认码
5. 存储 `PendingColdstart`（5 分钟过期）
6. 发送确认邮件

**确认邮件模板：**

```
MailCode Remote 命令执行确认
============================

收到您的冷启动请求，请核对以下信息：

  项目: MailCode
  路径: /Users/zs/Develop/MailCode
  分支: main
  命令: npm run test

确认码: a3f8b2

---
如需执行，请回复此邮件并在正文中输入：
  confirm: a3f8b2

此确认码 5 分钟内有效，一次使用。
```

**阶段二 — 确认执行：**

用户回复 `confirm: a3f8b2`，系统：
1. 查找 `PendingColdstart`（按确认码）
2. 校验发送者与原始请求一致
3. 校验未过期、未消费
4. 消费记录（删除）
5. 创建 tmux 会话 → 注入命令 → 发送会话创建邮件

### 3.3 PendingColdstart 数据结构

```python
@dataclass
class PendingColdstart:
    confirm_code: str       # 6 字符 hex
    command: str            # 用户原始命令
    project_dir: str        # 解析后的真实工作目录
    sender_email: str       # 原始请求发送者
    created_at: float       # time.time()
```

存储: `IMAPListener._pending_coldstarts: dict[str, PendingColdstart]`

清理: 每次 `fetch_unread_emails()` 轮询时顺带删除过期条目。

### 3.4 process_email 路由

```python
def process_email(self, email_entry, dry_run=False):
    body = email_entry.get("body", "")

    # 确认回复（优先，无 key 无 token）
    if re.search(r'confirm:\s*([a-f0-9]{6})', body, re.IGNORECASE):
        return self._process_confirm(email_entry, dry_run)

    token = email_entry.get("token")
    if token:
        return self._process_reply(email_entry, dry_run)
    return self._process_new_session(email_entry, dry_run)
```

### 3.5 配置项总览

```json
{
  "email": {
    "default_project_dir": "~/projects/current",
    "agent_type": "opencode",
    "check_interval": 5,
    "session_expiry_hours": 24,
    "max_commands_per_session": 10
  },
  "security": {
    "allowed_senders": [],
    "blocked_commands": [
      "rm -rf /",
      "sudo rm",
      "chmod 777",
      "curl.*|.*sh",
      "wget.*|.*sh"
    ],
    "auth_policy": "warn",
    "coldstart_confirm": true
  }
}
```

**移除的配置项：** `security.session_key`

**新增的配置项：** `security.coldstart_confirm`（默认 `true`）

### 3.6 邮件正文格式变更

```
# 之前
key: a1b2c3d4e5f6a7b8
project: /path/to/project       # 覆盖工作目录
npm run test

# 之后
project: MailCode                # 可选，交叉验证
npm run test
```

---

## 4. 向后兼容

| 场景 | 影响 |
|------|------|
| `project:` 从覆盖降级为验证 | 现有用户在目录不匹配时会收到错误提示 |
| 去掉 `$HOME` 兜底 | 用户需创建 `~/projects/current` symlink |
| 去掉 `session_key` | 配置文件中该字段会被忽略（不报错） |
| `coldstart_confirm` 默认 `true` | 升级后首次使用即触发确认流程 |

---

## 5. 待决问题

无。
