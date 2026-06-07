# MailCode Review Fix 修改报告

## 变更摘要
针对 review 报告中的 P0 严重 bug + tmux 工具模块抽取 + 死代码删除，共修改 13 个生产文件 + 5 个测试文件，新增 1 个工具模块，删除 1 个测试文件，+约 250 / -约 200 行。**所有 280 个单元测试通过，ruff 全过**。

## 涉及 review 报告项
| Review ID | 描述 | 修复方式 |
|---|---|---|
| S1 | notify.py 配置读取反了 | 简化分支，始终用 get_smtp_config()/get_email_config() |
| S2 | scheduler 60s 循环漏触发 | 1s Event.wait + (minute, hour) 去重 |
| S3 | security 白名单 false positive | 全邮箱 + @domain 后缀匹配 |
| S4 | email_listener sender 正则脆弱 | 改用 email.utils.parseaddr |
| S5 | email_listener._reconnect 死循环 | 重建 _idle_ready event 并 set |
| S7 | server.py 模块导入副作用 | 移到 main() 内 |
| S8 | health.py return 缺值 | 补 return all_ok |
| S11 | PluginBase.on_after_inject 参数不匹配 | 默认参数 + dispatch_void 透传 kwargs |
| L1 | 3 处死代码 | 删除 + 改测试 |
| M2 | tmux 25+ 处重复 | 抽 mailcode/utils/tmux.py |

## 文件变更清单
| 操作 | 文件 | 说明 |
|---|---|---|
| A | mailcode/utils/tmux.py | 新建公共 tmux 封装（has_session/kill_session/send_keys/list_sessions/capture_pane）|
| M | mailcode/relay/injector.py | 删 clipboard 死代码 + 改用 tmux 工具 + on_after_inject 传 success |
| M | mailcode/relay/session_launcher.py | 改用 tmux 工具（launch_agent/session_exists/stop）|
| M | mailcode/relay/scheduler.py | 修 cron 循环（1s Event + 去重）|
| M | mailcode/relay/email_listener.py | parseaddr + reconnect 修复 |
| M | mailcode/relay/server.py | import 副作用移到 main() |
| M | mailcode/relay/security.py | 重写 is_sender_allowed |
| M | mailcode/session/manager.py | 5 处 tmux 调用改用工具 |
| M | mailcode/channels/email_channel.py | 删 _send_raw |
| M | mailcode/notify.py | 配置读取简化 |
| M | mailcode/health.py | return all_ok |
| M | mailcode/plugins/_base.py | 删 get_templates + on_after_inject 默认参数 |
| M | mailcode/plugins/_registry.py | dispatch_void 透传 kwargs |
| M | mailcode/cli.py | 1 处 tmux kill 用工具 |
| M | tests/integration/test_email_roundtrip.py | 2 处 _send_raw → send() |
| M | tests/integration/test_opencode_execution.py | 1 处 |
| M | tests/integration/test_coldstart_real.py | 3 处 |
| M | tests/integration/test_smoke.py | 2 处 |
| M | tests/unit/test_plugins.py | 删 get_templates 断言 |
| D | tests/unit/test_injector.py | 仅测已删的 _detect_clipboard_cmd（Task 2 顺手删）|

## 测试结果
| 类型 | 命令 | 结果 |
|---|---|---|
| UT | `pytest tests/unit/ -q` | ✅ 280 passed in 0.53s |
| Lint | `ruff check mailcode/ tests/` | ✅ All checks passed! |

## 关键决策
- **tmux 工具模块用函数 + 模块常量**，不抽类；保留 TmuxMonitor 不动（管 pane 内容）
- **scheduler 修 1s 循环 + (minute, hour) 去重**，避免漏触发和重复触发
- **白名单行为变更**：`you@example.com` 精确匹配，`@example.com` 后缀匹配；旧的 `you` / `example.com` 写法从"模糊包含"改为"永不匹配"。需在 release note 提示用户
- **`on_after_inject` 保留 success 参数**，dispatch_void 透传 kwargs；基类 `success: bool = True` 默认参数保证未重写子类正常工作
- **集成测试 `_send_raw` → `send()`**，覆盖真实生产路径
- **Task 2 顺手删了 test_injector.py**（仅测已删的 _detect_clipboard_cmd）

## 风险与注意事项
- 集成测试未跑（需 test_config.json + 真实邮箱），仅 import/parse 验证
- 白名单行为变更对老配置是**破坏性**的，建议在 release note 提醒
- `_send_raw` 改 `send()` 后，集成测试的 raw byte 控制能力减弱（如需特殊 header，已通过 EmailMessage 构造再 .get_content() 提取）
- `server.py` import 副作用移除后，若有别处 `import mailcode.relay.server` 依赖副作用，需改为显式调用 `main()`

## 未做的项目
- M3 拆 email_listener.py（1033 行）
- M4 异常吞掉改具体类型
- M5 抽 jsonio.py
- M6 内联 import 清理
- M10 session/manager.py 三个清理方法去重
- M9 _ensure_user_config 双 fallback
- 中等/低级别其他项
