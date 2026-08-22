"""Claude 子进程调用器 -- 供 ConversationHandler / Scheduler 复用。

子进程调用逻辑本身在 ``mailcode.utils.agent.BaseAgentRunner`` (多 agent 共享),
本模块只提供 Claude 专属的 argv 拼装 + 安装指引。

两个模块级入口 (供邮件 handler / CLI 等直接调 Claude 的调用方使用):
  - ``call_claude``: 简单版 — 成功返回 ``stdout.strip()``, 失败返回 ``None``。
    契约不变。
  - ``call_claude_ex``: 结构化版 — 返回 ``AgentResult``, 携带失败分类
    (kind) 与真实详情 (error/stdout/stderr tail)。供 Scheduler 判断瞬态
    失败重试、把真实原因写进 ``last_error`` 与错误邮件。
"""

from typing import Optional

from .agent import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentErrorKind,
    AgentResult,
    BaseAgentRunner,
)

# claude 子进程默认超时 (秒) -- 24h 兜底, 实际调用方应传更短的值
# (Scheduler 默认 1800s, ConversationHandler 用 session.response_timeout_seconds)
CLAUDE_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS

# 历史名称 (本模块对外的说法仍是 "claude 的失败分类"), 实现在 agent.py
ClaudeErrorKind = AgentErrorKind
ClaudeResult = AgentResult


class ClaudeRunner(BaseAgentRunner):
    """Claude Code agent runner。"""

    name = "claude"

    def _command(self) -> str:
        return "claude"

    def _build_args(self, *, session_id: Optional[str], resume: bool,
                    no_session: bool) -> list[str]:
        args = ["claude", "--dangerously-skip-permissions"]
        if session_id is not None:
            args.extend(["--session-id", session_id])
        if resume:
            args.append("--resume")
        return args

    def hint_for_failure(self) -> str:
        return (
            "Claude Code 未安装或不在 PATH 中。"
            "请运行 'claude --version' 验证, 或访问 "
            "https://docs.anthropic.com/zh-CN/docs/claude-code 安装。"
        )


# 模块级单例 — 供下面两个函数式入口复用, 免去每次查注册表
# (ClaudeRunner 无实例状态, which 缓存挂在类上, 共享安全)
_claude_runner = ClaudeRunner()


def call_claude(
    prompt: str,
    cwd: str = "",
    *,
    session_id: Optional[str] = None,
    resume: bool = False,
    timeout: Optional[int] = None,
) -> Optional[str]:
    """调用 ``claude`` 子进程 (stdin 传 prompt)。失败返回 None。

    Args:
        prompt: 完整 prompt
        cwd: 工作目录 (默认 ``Path.home()``)
        session_id: 会话 ID, 传 ``--session-id`` 参数
        resume: 续传已有会话 (需同时设置 session_id), 传 ``--resume`` 参数
        timeout: 子进程超时 (秒); None 表示用 ``CLAUDE_TIMEOUT_SECONDS`` (24h 兜底)
    """
    return _claude_runner.call(
        prompt, cwd,
        session_id=session_id, resume=resume, timeout=timeout,
    )


def call_claude_ex(
    prompt: str,
    cwd: str = "",
    *,
    session_id: Optional[str] = None,
    resume: bool = False,
    timeout: Optional[int] = None,
) -> AgentResult:
    """调用 ``claude`` 子进程 (stdin 传 prompt), 返回结构化结果。

    与 ``call_claude`` 的区别: 失败不再压扁成 ``None``, 而是返回带
    ``kind``/``error`` 的 ``AgentResult``, 供调用方做瞬态重试、
    把真实原因写入持久化状态和错误邮件。

    Args / 语义同 ``call_claude``。
    """
    return _claude_runner.call_ex(
        prompt, cwd,
        session_id=session_id, resume=resume, timeout=timeout,
    )
