# 邮件 ↔ tmux 长连接回收机制 — 改进设计 <!-- [zh] 内部设计文档，未翻译 -->

## 背景

当前版本（v0.x）tmux 会话仅靠 **24h 绝对过期** 和 **10 次命令数上限** 两个维度回收，
缺少闲置超时、agent 存活检测、异常恢复等关键回收机制。

## 改进目标

在不引入后台线程的前提下，在现有 poll/IDLE 循环中引入**分层的会话健康检查**，
实现及时回收无用 tmux 会话，防止资源泄漏和注入到死 pane。

## 新增回收维度

| 维度 | 触发方式 | 策略 |
|------|---------|------|
| 闲置超时 | `_health_check()` 定时扫 | 最后活跃时间超过 N 分钟 → kill + delete |
| Agent 崩溃 | 注入前检查 + 定时扫 | pane 中无 agent 进程 → kill + delete |
| 命令数上限 | `_health_check()` + 注入前 | 达到上限后**主动回收**（原来只拒绝） |
| 时间过期 | `_health_check()` 定时扫 | 维持现有逻辑 |
| 启动恢复 | `IMAPListener.__init__` | 全量 session 健康检查，不一致项清理 |

## 新增 Session 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `last_active_at` | ISO datetime | 最后一次命令注入时间，创建时 = `created_at` |

## 新增配置项

```jsonc
// email 段
"session_idle_timeout_minutes": 30   // 闲置超时（分钟），0 = 禁用
```

## 架构变更概览

```
                     ┌──────────────────────────┐
                     │   IMAPListener._health_check()   │
                     │   (代替 _cleanup_expired)         │
                     │                                  │
                     │   for 每个 session JSON:          │
                     │     ├─ 时间过期? ──→ kill+delete  │
                     │     ├─ 命令数满? ──→ kill+delete  │
                     │     ├─ 闲置超时? ──→ kill+delete  │
                     │     └─ agent 死? ──→ kill+delete  │
                     └──────────────────────────┘
                                  ▲
                                  │ 每 12 次 poll/IDLE 循环
                                  │
┌──────────────┐      ┌──────────────────────┐
│ 注入前检查    │      │  启动恢复 (__init__)   │
│ (单 session) │      │  全量 session 扫描     │
│              │      │  清理不一致项          │
│ agent 死? ──→│      └──────────────────────┘
│ kill+delete  │
│ 命令数满? ──→│
│ kill+delete  │
│              │
│ 通过 ──→ inject()
└──────────────┘
```

## 清理对称性

- `_process_reply` 失败时：如果 tmux session 不存在 → `delete_session()`（清理孤儿元数据）
- `_process_new_session` 失败时：维持现有 `stop()` + `delete_session()`
- `cleanup_all()` 添加安全检查，只杀存活 tmux session，不重复杀

## Scheduler 集成

- Scheduler 本身已经是独立 daemon thread，维持现状
- 后续任务：在 `email_listener.py` 中解析 `cron:` / `schedule:` 邮件字段，注册到 Scheduler
