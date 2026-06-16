# Exec Plan: MailCode 交互改进

## Task List
### Phase 1: claude --resume 架构
| ID | Type | Files | Description |
|---|---|---|---|
| P1-1-impl | impl | mailcode/utils/claude_runner.py | 修改 `call_claude()`，增加 `session_id` 和 `resume` 可选参数。session_id 非空时传 `--session-id <id>`，resume=True 时追加 `--resume`。旧调用方式（无参数）行为不变。 |
| P1-1-test | test | tests/unit/test_claude_runner.py | 新增测试用例：①无参数向后兼容 ②--session-id 参数 ③--session-id + --resume ④错误处理一致性 |
| P1-2-impl | impl | mailcode/relay/resume_handler.py (NEW) | 新建 ResumeConversationHandler 类。核心方法 handle_email()：①查映射文件找 Claude session UUID ②新对话用 --session-id，续旧用 --resume ③写 transcript 文件追加 incoming+outgoing ④SMTP 发回复。映射文件路径 ~/.config/mailcode/claude_sessions.json。Transcript 路径 ~/.config/mailcode/transcripts/<uuid>.json。 |
| P1-2-test | test | tests/unit/test_resume_handler.py (NEW) | 新建测试文件：①映射文件读写 ②transcript 追加 ③handle_email 新对话和续旧对话 ④cwd 提取（复用 conversation_handler.py 的 extract_cwd）⑤错误处理 |
| P1-3-impl | impl | mailcode/relay/email_listener.py | process_email() 增加 resume 路由：当 force_session 或 is_session_enabled() 时走 resume_handler 而非 conversation_handler |
| P1-3-test | test | tests/unit/test_listener_lifecycle.py | 增加 resume 分发路径的测试 |

### Phase 2: 邮件交互增强
| ID | Type | Files | Description |
|---|---|---|---|
| P2-1-impl | impl | resume_handler.py | 每封回复末尾拼接会话脚注："\n──────────────────────────────────────────\n📬 MailCode · 对话 {session_id}（第 {n} 轮）\n回复此邮件继续 · 发「status」查系统状态" |
| P2-2-impl | impl | email_listener.py | process_email() 入口处加 _is_system_command(subject) 判断：status/help/sessions 主题直接回复不调 Claude |
| P2-3-impl | impl | email_listener.py | fetch_unread_emails() 处理每封邮件后立即 send "已收悉" 通知 |
| P2-4-impl | impl | resume_handler.py | call_claude 返回 None → "技术问题"；返回空字符串 → "无回复内容"；超时 → "超时" |
| P2-4-test | test | tests/unit/test_resume_handler.py | 新增错误文案测试用例 |
| P2-5-impl | impl | resume_handler.py | 清理过期 session 时给最后发件人发结束通知 |

### Phase 3: CLI 增强
| ID | Type | Files | Description |
|---|---|---|---|
| P3-1-impl | impl | server.py + email_listener.py | IMAPListener 加 event_callbacks 字典，关键节点（收到邮件、调 Claude、发送回复、心跳）触发回调。server.py 注册打印机回调。 |
| P3-2-impl | impl | mailcode/cli_chat.py (NEW) + cli.py | 新增 chat 子命令，input() 循环 + call_claude()，支持 --session-id/--resume |
| P3-3-impl | impl | mailcode/session_cli.py | add --wide, --filter <keyword>, cleanup --dry-run show IDs, stats command |

### Phase 4: 快速修复
| ID | Type | Files | Description |
|---|---|---|---|
| P4-1-impl | impl | cli.py + health.py | 修复 --send 死代码：传 args.send 给 run_health()，加参数控制 |
| P4-2-impl | impl | health.py | 增加 allowed_senders 为空时的警告提示 |
| P4-3-impl | impl | config.py | JSONDecodeError 不静默重置为 {}，改为 print 错误信息后 sys.exit(1) |
| P4-4-impl | impl | cli.py | cmd_serve 错误列表末尾加 "💡 运行 mailcode health 检查连通性" |
| P4-5-impl | impl | email_listener.py | _connect() 失败时 print 到 stderr |
| P4-6-impl | impl | install.sh | hash -r 之后加 `command -v claude` 检查 |

## Dependencies
- P1-1 是 P1-2 的前置条件
- P1-2 是 P1-3 的前置条件
- Phase 1 全部完成后才能开始 Phase 2（resume_handler 依赖）
- Phase 4 独立，可随时穿插执行
- Phase 3 的 P3-2 (chat) 依赖 P1-1 但可不依赖 P1-2

## Total effort estimate
- Phase 1: ~2 天
- Phase 2: ~1.5 天  
- Phase 3: ~2 天
- Phase 4: ~0.5 天
