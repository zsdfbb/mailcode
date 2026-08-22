"""Claude 子进程调用器 — 供 ConversationHandler / Scheduler 复用。

抽出此模块是为了避免 scheduler 与 conversation_handler 双份实现
``claude -p`` 调用逻辑导致行为漂移 (超时、参数、cwd 默认值等)。

两个入口:
  - ``call_claude``: 简单版 — 成功返回 ``stdout.strip()``, 失败返回 ``None``。
    供邮件 handler / CLI 等只关心"有没有结果"的调用方使用 (契约不变)。
  - ``call_claude_ex``: 结构化版 — 返回 ``ClaudeResult``, 携带失败分类
    (kind) 与真实详情 (error/stdout/stderr tail)。供 Scheduler 判断瞬态
    失败重试、把真实原因写进 ``last_error`` 与错误邮件。
"""

import logging
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# claude 子进程默认超时 (秒) — 24h 兜底, 实际调用方应传更短的值
# (Scheduler 默认 1800s, ConversationHandler 用 session.response_timeout_seconds)
CLAUDE_TIMEOUT_SECONDS = 86400

# 失败日志里 stdout / stderr 的截断长度
_STDOUT_TAIL = 200
_STDERR_TAIL = 500


class ClaudeErrorKind(str, Enum):
    """``call_claude_ex`` 的失败分类 — Scheduler 据此决定是否重试。"""

    TIMEOUT = "timeout"        # 子进程超时 → 不重试 (已吃满超时, 重试只是翻倍耗时)
    NOT_FOUND = "not_found"    # claude 命令未安装 → 不重试 (装了才能好)
    OS_ERROR = "os_error"      # OS 层错误 (BrokenPipe 等) → 可重试 (多为瞬时)
    NONZERO_EXIT = "nonzero_exit"  # 子进程非零退出, 通常上游 API 错误 → 可重试


@dataclass
class ClaudeResult:
    """``call_claude_ex`` 的返回值 — 结构化携带成功输出或失败详情。

    Attributes:
        ok: True 表示调用成功 (``output`` 有值, 可能为空串)
        output: 成功时的 ``stdout.strip()``
        kind: 失败分类 (成功时为 None), 见 ``ClaudeErrorKind``
        error: 人可读失败详情, 可直接写进 ``last_error`` / 错误邮件
        exit_code: 非零退出时的 returncode
        stdout_tail: stdout 截断尾部 (失败排查用)
        stderr_tail: stderr 截断尾部 (失败排查用)
        elapsed: 子进程耗时 (秒)
    """

    ok: bool
    output: Optional[str] = None
    kind: Optional[ClaudeErrorKind] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    elapsed: float = 0.0


def call_claude_ex(
    prompt: str,
    cwd: str = "",
    *,
    session_id: Optional[str] = None,
    resume: bool = False,
    timeout: Optional[int] = None,
) -> ClaudeResult:
    """调用 ``claude`` 子进程 (stdin 传 prompt), 返回结构化结果。

    与 ``call_claude`` 的区别: 失败不再压扁成 ``None``, 而是返回带
    ``kind``/``error`` 的 ``ClaudeResult``, 供调用方做瞬态重试、
    把真实原因写入持久化状态和错误邮件。

    Args:
        prompt: 完整 prompt
        cwd: 工作目录 (默认 ``Path.home()``)
        session_id: 会话 ID, 传 ``--session-id`` 参数
        resume: 续传已有会话 (需同时设置 session_id), 传 ``--resume`` 参数
        timeout: 子进程超时 (秒); None 表示用 ``CLAUDE_TIMEOUT_SECONDS`` (24h 兜底)
    """
    cwd = cwd or str(Path.home())
    args = ["claude", "--dangerously-skip-permissions"]
    if session_id is not None:
        args.extend(["--session-id", session_id])
    if resume:
        args.append("--resume")

    effective_timeout = timeout if timeout is not None else CLAUDE_TIMEOUT_SECONDS

    logger.info(
        "claude 子进程启动: prompt_len=%d, timeout=%ds, cwd=%s, args=%s",
        len(prompt), effective_timeout, cwd, args,
    )
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        logger.error(
            "claude 子进程超时 (>%ds, elapsed=%.1fs)",
            effective_timeout, elapsed,
        )
        return ClaudeResult(
            ok=False,
            kind=ClaudeErrorKind.TIMEOUT,
            error=f"claude 子进程超时 (>={effective_timeout}s, elapsed={elapsed:.1f}s)",
            elapsed=elapsed,
        )
    except FileNotFoundError:
        logger.error("claude 命令未找到, 请确保已安装 Claude Code")
        return ClaudeResult(
            ok=False,
            kind=ClaudeErrorKind.NOT_FOUND,
            error="claude 命令未找到, 请确保已安装 Claude Code",
        )
    except OSError as e:
        elapsed = time.monotonic() - t0
        logger.error(
            "claude 子进程 OS 错误: errno=%s msg=%s elapsed=%.1fs args=%s",
            getattr(e, "errno", None), e, elapsed, args,
        )
        return ClaudeResult(
            ok=False,
            kind=ClaudeErrorKind.OS_ERROR,
            error=f"claude 子进程 OS 错误: {e} (errno={getattr(e, 'errno', None)})",
            elapsed=elapsed,
        )

    elapsed = time.monotonic() - t0
    if result.returncode != 0:
        logger.error(
            "claude 子进程失败: returncode=%s, elapsed=%.1fs, stderr[:500]=%r, stdout[:200]=%r, args=%s",
            result.returncode, elapsed, result.stderr[:_STDERR_TAIL],
            result.stdout[:_STDOUT_TAIL], args,
        )
        # error 详情取 stdout 优先, 空再取 stderr, 保证上游 API 错误文案进 last_error
        detail = (result.stdout or result.stderr or "").strip()
        return ClaudeResult(
            ok=False,
            kind=ClaudeErrorKind.NONZERO_EXIT,
            error=f"claude 子进程失败 returncode={result.returncode}: {detail[:500]}",
            exit_code=result.returncode,
            stdout_tail=result.stdout[:_STDOUT_TAIL],
            stderr_tail=result.stderr[:_STDERR_TAIL],
            elapsed=elapsed,
        )
    logger.info(
        "claude 子进程成功: elapsed=%.1fs, stdout_len=%d",
        elapsed, len(result.stdout),
    )
    return ClaudeResult(ok=True, output=result.stdout.strip(), elapsed=elapsed)


def call_claude(
    prompt: str,
    cwd: str = "",
    *,
    session_id: Optional[str] = None,
    resume: bool = False,
    timeout: Optional[int] = None,
) -> Optional[str]:
    """调用 ``claude`` 子进程 (stdin 传 prompt)。失败返回 None (向后兼容简单版)。

    只关心"有没有结果"的调用方用这个; 需要失败分类/详情做重试或精细错误
    处理时改用 ``call_claude_ex``。
    """
    result = call_claude_ex(
        prompt, cwd,
        session_id=session_id, resume=resume, timeout=timeout,
    )
    return result.output if result.ok else None