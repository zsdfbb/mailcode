# 配置简化：mailcode_bot + provider 自动识别

## 背景

当前配置需要用户手动填写 SMTP/IMAP 地址端口，不直观。`account` 段名含义不清，`email.to` 字段多余（完成任务回复发件人即可）。

## 设计

### 核心变更

1. `account` → `mailcode_bot`：MailCode 机器人邮箱段
2. Provider 自动识别：从邮箱域名自动判断 SMTP/IMAP 配置，用户只需填邮箱和密码
3. 移除 `email.to`、`email.from`、`email.from_name`、`email.agent_type`、`email.check_interval`、`email.session_expiry_hours`、`email.default_project_dir`、`notification` 等可选默认字段
4. 向后兼容：旧配置 `account` 段自动适配到 `mailcode_bot`

### 域名→Provider 映射

| 域名 | provider | SMTP | IMAP |
|------|----------|------|------|
| qq.com | qq | smtp.qq.com:465 SSL | imap.qq.com:993 SSL |
| 126.com | 126 | smtp.126.com:465 SSL | imap.126.com:993 SSL |
| 163.com | 163 | smtp.163.com:465 SSL | imap.163.com:993 SSL |
| gmail.com | gmail | smtp.gmail.com:587 SSL | imap.gmail.com:993 SSL |
| outlook.com / hotmail.com / live.com | outlook | smtp-mail.outlook.com:587 SSL | outlook.office365.com:993 SSL |

### 新配置格式

```json
{
  "mailcode_bot": {
    "email": "your@qq.com",
    "password": "授权码"
  },
  "security": {
    "allowed_senders": ["your@qq.com"],
    "blocked_commands": ["rm -rf /", "sudo rm", "chmod 777", "curl.*|.*sh", "wget.*|.*sh"],
    "auth_policy": "warn",
    "coldstart_confirm": true
  }
}
```

### 数据流

`get_smtp_config()`:
1. 读取 `smtp` 段（用户手动覆盖）
2. 从 `mailcode_bot.email` 解析域名 → 查 provider 映射表 → 填充 smtp host/port/secure
3. 从 `mailcode_bot` 合并 email/password → 填充 smtp user/pass
4. 手动设置的同名字段覆盖自动填充值

### 向后兼容

- 读取 `mailcode_bot` 不存在时回退到 `account`
- smtp/imap 段不存在时由 provider 填充

## 涉及文件

- `mailcode/config.py`
- `mailcode/resources/default.json`
- `mailcode/cli.py`
- `mailcode/health.py`
- `mailcode/channels/email_channel.py`（send() 的 to_email fallback）
- `tests/unit/test_config.py`
- `README.md`、`README.en.md`

## 波及文档

- `docs/design-final/design.md` — 第 5 章配置设计需更新
- `AGENTS.md` — 无变更
