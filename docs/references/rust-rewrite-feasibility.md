# Rust 重写可行性分析

> 记录日期：2026-06-01
> 评估维度：技术可行性、架构映射、Crate 选型、实施路径
> 决策状态：暂缓实施，保留参考

---

## 动机

| 维度 | 优先级 | 说明 |
|------|--------|------|
| 单二进制分发 | 首要 | 消除 Python 运行时依赖，`pip install` → 下载即用 |
| 性能与资源 | 次要 | 更低内存占用，更快启动，适合长驻进程场景 |
| 类型安全 | 次要 | Rust 编译期保证减少邮件解析/网络状态机等边界错误 |

---

## 总体评估

**可行，但有三处关键取舍：**

### 适合 Rust 的方面

- 项目规模适中（~3,800 LoC Python），无深度 Python 特有模式
- IMAP/SMTP 有成熟 Rust crate（`async-imap`, `lettre`）
- tmux 管理用 `subprocess`，映射到 `std::process::Command` 直接
- 零第三方 Python 依赖，映射到 Rust crate 后依赖更可控
- 纯单二进制对此类运维工具确实有价值

### 需慎重处理的方面

| 模块 | 风险 | 建议 |
|------|------|------|
| 插件系统 | 动态加载在 Rust 中最难复现 | v1 砍掉动态加载，仅保留内置 hook trait |
| MIME 解析 | 多编码/国际化邮件头边界情况 | `mailparse` + 自建 DKIM/SPF 头解析 |
| IMAP IDLE | async 重连/超时/并发安全 | tokio + async-imap，充分测试 |
| 测试重写 | 现有 ~5,100 行测试 | 核心逻辑必覆盖，集成测试渐进补 |

### 不建议 Rust 实现的方面

- 运行期插件动态性 — Python 的 `importlib` 无可比等价物
- OpenCode bridge JS / Claude hooks 部署 — 保持独立文件分发，Rust 只负责 copy

---

## 模块映射

```
Python 模块                       → Rust 模块                    → Crate 依赖
──────────────────────────────────────────────────────────────────────────────
cli.py (argparse)                → cli.rs                       → clap
config.py (JSON)                 → config.rs                    → serde, serde_json, dirs
relay/email_listener.py (993 LoC)→ relay/listener.rs            → async-imap, async-native-tls, mailparse
relay/security.py (114 LoC)      → relay/security.rs            → regex
relay/injector.py (126 LoC)      → relay/injector.rs            → std::process::Command
relay/session_launcher.py (152)  → relay/session_launcher.rs    → std::process::Command
relay/server.py (74)             → relay/server.rs              → tokio
relay/scheduler.py (259)         → scheduler/cron.rs            → cron crate
session/manager.py (301)         → session/manager.rs           → serde, chrono
channels/email_channel.py (118)  → channels/email.rs            → lettre
channels/webhook_channel.py (146)→ channels/webhook.rs          → reqwest
notify.py (121)                  → notify.rs                    → (聚合各模块)
health.py (128)                  → health.rs                    → async-imap, lettre
plugins/* (269)                  → plugins/hooks.rs             → 简化为静态 trait
templates/* (18 + 4 .txt)       → templates.rs                 → built-in 或 handlebars
utils/tmux_monitor.py (91)       → utils/tmux_monitor.rs        → std::process::Command
utils/logging.py (42)            → utils/logging.rs             → tracing + tracing-subscriber
```

### 建议项目结构

```
mailcode-rs/
├── Cargo.toml
├── src/
│   ├── main.rs                  # 入口 + CLI dispatch
│   ├── cli.rs                   # clap 子命令定义
│   ├── config.rs                # 配置加载/保存
│   ├── relay/
│   │   ├── mod.rs
│   │   ├── server.rs            # 主循环 + signal 处理
│   │   ├── listener.rs          # IMAP 监听 (IDLE + polling)
│   │   ├── security.rs          # 命令黑名单 + 发件人校验
│   │   ├── injector.rs          # tmux 命令注入
│   │   └── session_launcher.rs  # tmux session 创建
│   ├── session/
│   │   ├── mod.rs
│   │   └── manager.rs           # Session CRUD + token 管理
│   ├── channels/
│   │   ├── mod.rs
│   │   ├── email.rs             # SMTP 发送
│   │   └── webhook.rs           # Webhook 多平台
│   ├── scheduler/
│   │   ├── mod.rs
│   │   └── cron.rs              # Cron 解析 + 调度
│   ├── notify.rs                # 通知工作流
│   ├── health.rs                # SMTP/IMAP 连通性检查
│   ├── plugins/
│   │   ├── mod.rs
│   │   └── hooks.rs             # 内置 hook trait + 空实现
│   ├── templates.rs             # 邮件模板
│   └── utils/
│       ├── mod.rs
│       ├── tmux_monitor.rs      # tmux pane 捕获
│       └── logging.rs           # tracing 初始化
```

---

## Crate 选型清单

| 功能 | Crate | 理由 |
|------|-------|------|
| 异步运行时 | `tokio` (full) | async-imap / reqwest / lettre 均依赖 |
| CLI | `clap` (derive) | 行业标准，derive API 简洁 |
| 序列化 | `serde` + `serde_json` | 配置/会话持久化 |
| IMAP | `async-imap` + `async-native-tls` | IDLE 支持 |
| SMTP | `lettre` | 唯一活跃的 Rust SMTP crate |
| HTTP | `reqwest` | Webhook 发送 |
| 邮件解析 | `mailparse` | MIME 解析 |
| 正则 | `regex` | 命令黑名单 |
| 时间 | `chrono` | 过期时间/日志 |
| 日志 | `tracing` + `tracing-subscriber` | 结构化日志 |
| 模板 | `handlebars` (可选) | 邮件模板渲染 |
| 路径 | `dirs` | XDG 目录解析 |

---

## 插件系统处理策略

**v1 不实现动态加载。** 原因：
- 实际仅有 1 个示例 hello_world 插件
- 运行期动态加载需要共享库 C ABI 或 Wasm 运行时（wasmtime），增加依赖体积
- 违背"单二进制、零依赖"的核心动机

替代方案：将 8 个 hook 点定义为 Rust trait，内置空默认实现。后续可通过 `#[cfg(feature = "plugin-wasm")]` 引入 Wasm 支持。

---

## 分阶段实施建议

| Phase | 内容 | 预估工时 | 可独立验证 |
|-------|------|----------|-----------|
| 1 | 骨架：Cargo + config + CLI + logging | 1 天 | `mailcode config show` / `--help` |
| 2 | Session 管理 + 安全模块 | 1 天 | 单元测试 |
| 3 | SMTP/Webhook/Health 通道 | 1 天 | `mailcode health --send` |
| 4 | IMAP 监听 + tmux 注入 + 冷启动协议 | 2-3 天 | `mailcode serve` 完整流程 |
| 5 | tmux monitor + cron 调度 + notify | 1 天 | 定时触发 + 通知链 |
| 6 | 模板 + setup + 发布配置 | 0.5-1 天 | 端到端测试 |

**总计：约 7-10 个工作日**（全时 2 周，业余 3-4 周）。

---

## 不重写的功能（v1 跳过）

- 动态插件系统 → 静态 trait hooks
- License 校验 → 始终返回可用
- OpenCode bridge JS → 文件分发，Rust 只负责 copy
- Claude Code hooks 部署 → 同上

---

## 参考

- [项目价值评估](project-value-assessment.md) — 产品定位与用户画像背景
