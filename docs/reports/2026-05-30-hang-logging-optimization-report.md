# 系统防挂死与错误日志优化 — 修改报告

## 变更摘要

对系统所有关键路径添加超时保护、错误日志、日志轮转，消除挂死风险和静默异常吞没。

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| M | mailcode/utils/logging.py | FileHandler→RotatingFileHandler, MAILCODE_LOG_LEVEL 环境变量, 防重复 handler |
| M | mailcode/channels/email_channel.py | SMTP timeout=15, print→logger, hasattr→getattr |
| M | mailcode/session/manager.py | 5 处 except pass→logger.exception |
| M | mailcode/relay/email_listener.py | IMAP sock.settimeout(15), IDLE 线程异常日志, print→logger |
| M | mailcode/relay/injector.py | subprocess timeout=10, returncode 检查, 引号转义修复, print→logger |
| M | mailcode/relay/session_launcher.py | tmux set-option timeout=5 |
| M | mailcode/relay/server.py | 信号处理走 listener.stop(), main() 外层 try/except |
| M | mailcode/relay/scheduler.py | _execute() try/except/finally, _run_loop 异常保护 |
| M | mailcode/config.py | json.load try/except json.JSONDecodeError |
| M | mailcode/health.py | IMAP4_SSL(timeout=10) |
| M | tests/unit/test_email_channel.py | 断言适配新增 timeout 参数 |

## 测试结果

| 类型 | 命令 | 结果 |
|------|------|------|
| UT | pytest tests/unit/ | ✅ 261 PASS |

## 修复清单

- ✅ IMAP 连接超时（15s）— email_listener.py
- ✅ SMTP 连接超时（15s）— email_channel.py
- ✅ 所有 subprocess.run 有 timeout（5-10s）— injector.py, session_launcher.py
- ✅ IDLE 线程异常日志（不再静默吞掉）— email_listener.py
- ✅ 信号处理走正常关闭路径 — server.py
- ✅ 调度器线程异常保护 — scheduler.py
- ✅ injector 检查 returncode + 正确引号转义 — injector.py
- ✅ session/manager 全部 except pass 替换 — manager.py
- ✅ 日志轮转 5MB×3 — logging.py
- ✅ MAILCODE_LOG_LEVEL 环境变量 — logging.py
- ✅ config JSON 损坏时友好降级 — config.py
- ✅ health.py IMAP 超时提前到构造函数 — health.py
- ✅ print() 全部改为 logger — 多文件
