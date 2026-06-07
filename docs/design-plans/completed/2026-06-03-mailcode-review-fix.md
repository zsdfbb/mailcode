# MailCode Review Fix 设计计划

## 背景

完成 MailCode 项目的全面 review，识别出 32 项问题（8 严重 / 10 中 / 14 低）。本计划针对 P0 严重 bug + 中等级别的 tmux 工具模块抽取 + 死代码删除，确保项目主路径不再有确定性 bug，工具函数不再散落。

**用户已确认范围**：
- P0 严重 bug（8 项）
- 抽 `mailcode/utils/tmux.py` 公共模块
- 删除死代码
- 验证方式：单元测试 + ruff

**本期不做**（留待后续）：
- 拆 `email_listener.py`（1033 行）
- 抽 `template.py` / `jsonio.py`
- 重构 scheduler 业务逻辑
- 修中等级别问题（M4 异常吞掉、M5 JSON 重复、M6 内联 import、M10 重复逻辑）

## 设计

### 整体方案

按 4 个独立的修复轨道并行推进：

```
1. 工具层：新建 mailcode/utils/tmux.py，封装 4 类 tmux subprocess 调用
2. Bug 修复：7 个独立 bug 修复（详见任务清单）
3. 死代码删除：3 处死代码 + 对应测试断言
4. 验证：单元测试 + ruff
```

### 架构决策

#### 决策 1：tmux 工具用函数 + 模块常量，不用类

`mailcode/utils/tmux.py` 用模块级函数 + 命名常量，原因：
- 现有 `TmuxMonitor`（`mailcode/utils/tmux_monitor.py`）已用 `@staticmethod` 风格
- 函数比类更轻，import 路径短
- 保留 `TmuxMonitor` 不动（仍负责 `capture-pane` / `display-message` / pane 内容解析），新模块只管 `has-session` / `kill-session` / `send-keys` / `list-sessions`

API 形状：
```python
def has_session(name: str) -> bool
def kill_session(name: str) -> bool
def send_keys(session: str, *keys: str, timeout: int = 10) -> bool
def list_sessions() -> list[str]
def capture_pane(session: str, lines: int = 500) -> str  # TmuxMonitor 已有，wrapper 一致性
```

`tmux` 命令不存在时（罕见环境）所有函数返回 safe default（`False` / `""` / `[]`）。

#### 决策 2：scheduler 修复用 `threading.Event` + 1s 循环 + "上次触发 (minute, hour) 组合" 去重

- 改用 `self._stop_event.wait(timeout=1.0)` 替代 `time.sleep(60)`，可立即响应 stop
- 维护 `self._last_fired: dict[str, tuple[int, int]]`（task_id → (minute, hour)）
- 每秒检查一次 cron 匹配，匹配成功且与 `_last_fired` 不同时才触发
- 1s 循环 + 1s 内多次匹配仍只触发 1 次（minute 不变）

#### 决策 3：白名单 false positive 修复用"全邮箱匹配 + 后缀 @domain 匹配"二分

```python
def is_sender_allowed(self, sender_email: str) -> bool:
    sender_lower = sender_email.lower().strip()
    if "@" not in sender_lower:
        return False
    for allowed in self.allowed_senders:
        a = allowed.lower().strip()
        if a.startswith("@"):
            # 后缀匹配：sender 必须以 @<domain> 结尾
            if sender_lower.endswith(a):
                return True
        else:
            # 全邮箱匹配（必须含 @）
            if sender_lower == a:
                return True
    return False
```

行为变更：
- `allowed = "you@example.com"` → 精确匹配 `you@example.com`
- `allowed = "@example.com"` → 匹配任何 `xxx@example.com`
- 删除原"双向 `in`"导致的"前缀误中"和"后缀误中"

#### 决策 4：PluginBase.on_after_inject 保留 success 参数；dispatch_void 改为 `(method, ctx, **kwargs)` 调用

```python
# _base.py
def on_after_inject(self, ctx: InjectContext, success: bool = True):
    pass

# _registry.py dispatch_void
def dispatch_void(self, hook_name: str, ctx, **kwargs):
    ...
    method(ctx, **kwargs)
```

调用方传入 success 显式标记成功/失败。

#### 决策 5：notify.py 配置读取永远走合并函数

```python
# notify.py
from mailcode.config import get_smtp_config, get_email_config

ec = EmailChannel(
    smtp_config=get_smtp_config(),
    email_config=get_email_config(),
)
```

`get_smtp_config()` / `get_email_config()` 已做 provider 探测 + 字段合并 + user/pass 回退，是配置读取的唯一入口。

#### 决策 6：EmailChannel._send_raw 改名为可导出但仅测试用的 helper

虽然 5 处集成测试用，但生产代码无用。决定：
- 删 `_send_raw` 方法
- 集成测试改用 `EmailChannel().send()` 配合手工构造的 `MIMEMultipart` 邮件

`MIMEMultipart` + `msg.as_bytes()` 的 raw 字节就是 `sendmail()` 接受的格式，可以直接传入。如果测试要测试"不通过模板的 raw 发送"，改为直接测试 `send()` 路径（参数 + 行为相同）。

#### 决策 7：CommandInjector.inject_via_clipboard 整段删除

无任何调用方，0 风险。`_detect_clipboard_cmd` 一并删除。

#### 决策 8：PluginBase.get_templates 删除 + 改测试断言

仅 `tests/unit/test_plugins.py:103` 引用，删方法 + 删该行测试断言。

### 数据流 / 接口变更

无对外 API 变化。所有修改对调用方透明。

```
email_listener.py       ──┐
injector.py             ──┤
session_launcher.py     ──┼─→ mailcode/utils/tmux.py
session/manager.py      ──┤
cli.py                  ──┘
```

## 涉及文件

### 新增
- `mailcode/utils/tmux.py` — tmux subprocess 公共封装（~80 行）

### 修改
- `mailcode/relay/injector.py` — 删 clipboard、改用 tmux 工具
- `mailcode/relay/scheduler.py` — 修 cron 循环、改用 tmux 工具
- `mailcode/relay/session_launcher.py` — 改用 tmux 工具
- `mailcode/relay/email_listener.py` — 修 reconnect、修 sender 正则、改用 tmux 工具
- `mailcode/relay/server.py` — 模块导入副作用（移到 main()）
- `mailcode/relay/security.py` — 修白名单匹配
- `mailcode/channels/email_channel.py` — 删 `_send_raw`
- `mailcode/notify.py` — 配置读取走合并函数
- `mailcode/health.py` — return 缺值修复
- `mailcode/plugins/_base.py` — 删 `get_templates`、改 `on_after_inject` 默认参数
- `mailcode/plugins/_registry.py` — `dispatch_void` 透传 `**kwargs`
- `mailcode/session/manager.py` — 改用 tmux 工具
- `mailcode/cli.py` — 改用 tmux 工具
- `tests/integration/test_email_roundtrip.py` — 改用 `EmailChannel().send()` 替换 `_send_raw`
- `tests/integration/test_opencode_execution.py` — 同上
- `tests/integration/test_coldstart_real.py` — 同上
- `tests/integration/test_smoke.py` — 同上
- `tests/unit/test_plugins.py` — 删 `get_templates` 断言

### 删除
- `mailcode/relay/injector.py:_detect_clipboard_cmd`（injector.py:95-109）
- `mailcode/relay/injector.py:inject_via_clipboard`（injector.py:111-122）
- `mailcode/channels/email_channel.py:_send_raw`（email_channel.py:37-52）
- `mailcode/plugins/_base.py:get_templates`（_base.py:90-91）

## 测试策略

- **测试范围**：单元测试（不跑集成测试，集成测试需要真实邮箱）
- **测试命令**：`source .venv/bin/activate && python3 -m pytest tests/unit/ -q`
- **Lint 命令**：`source .venv/bin/activate && python3 -m ruff check mailcode/ tests/`
- **验收标准**：
  - 所有现有单测通过
  - ruff check 无 issue
  - 修改的函数被对应测试覆盖

## 波及文档

- `docs/design-final/design.md` — 追加章节，描述 tmux 工具模块的统一封装
- 无需更新 README

## 风险与注意事项

1. **`_send_raw` 改测试需保证字节内容等价**：测试用 `_send_raw(raw_bytes, ...)` 发送手工构造的 MIME 字节。改用 `EmailChannel().send()` 需保证最终 `sendmail()` 收到的字节与原 `_send_raw` 一致（或至少能完成测试意图）。需要用同样的 `MIMEMultipart` 构造路径。
2. **scheduler 改 1s 循环**：每分钟检查 60 次（vs 之前 1 次）CPU 影响可忽略（一次 dict 查找）。`threading.Event.wait(1.0)` 是可中断 sleep，stop() 立即生效。
3. **白名单匹配行为变更**：用户旧配置里如果写 `you`（无 `@`）或 `example.com`（无 `@`），会从"任意包含"变成"永不匹配"。需在 release note / commit message 提示用户改用 `you@example.com` 或 `@example.com`。
4. **tmux 工具的异常处理**：保留现有"捕获 Exception 返回 False/空"风格，避免一处 tmux 失败导致整条邮件循环挂掉。
5. **server.py 副作用迁移**：`setup_logging` 移到 `main()`，但 import `mailcode.relay.server` 仍然有副作用（顶部 `import` 没动）。需要确认 server.py 只通过 `python3 -m mailcode.relay.server` 入口调用，不在别处 import。
