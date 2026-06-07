# 系统防挂死与错误日志优化

## 背景

当前系统在多个关键路径上存在挂死风险：IMAP/SMTP 网络操作无超时、子进程调用无 timeout、异常被静默吞掉无日志。需要系统性地修复这些问题。

## 设计

### 核心原则
1. **所有网络操作设置超时** — IMAP、SMTP 连接/登录/操作
2. **所有 subprocess.run 设置 timeout** — tmux 命令调用
3. **禁止裸 `except Exception: pass`** — 必须至少 `logger.exception`
4. **日志轮转** — relay.log 无限增长问题
5. **信号处理不绕过清理** — SIGINT/SIGTERM 走正常关闭路径
6. **`print()` 全部改为 `logger`** — 统一日志输出

### 涉及文件

| 目录 | 文件 | 变更 |
|------|------|------|
| `mailcode/utils/` | `logging.py` | 日志轮转 |
| `mailcode/channels/` | `email_channel.py` | SMTP timeout, print→logger |
| `mailcode/session/` | `manager.py` | except pass→logger |
| `mailcode/relay/` | `email_listener.py` | IMAP timeout, IDLE 异常日志, 信号处理 |
| `mailcode/relay/` | `injector.py` | subprocess timeout, 返回值, 引号转义, print→logger |
| `mailcode/relay/` | `session_launcher.py` | tmux set-option timeout |
| `mailcode/relay/` | `server.py` | 信号处理走正常关闭 |
| `mailcode/relay/` | `scheduler.py` | _run_loop 异常保护 |
| `mailcode/` | `config.py` | JSON 解析错误处理 |
| `mailcode/` | `health.py` | sock.settimeout 提前 |

## 波及文档

- `docs/design-final/design.md` — 无需更新（不涉及架构变更）
