"""Claude 子进程调用器 — 供 ConversationHandler / Scheduler 复用。

抽出此模块是为了避免 scheduler 与 conversation_handler 双份实现
``claude -p`` 调用逻辑导致行为漂移 (超时、参数、cwd 默认值等)。
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# claude 子进程默认超时 (秒) — 24h 兜底, 实际调用方应传更短的值
# (Scheduler 默认 1800s, ConversationHandler 用 session.response_timeout_seconds)
CLAUDE_TIMEOUT_SECONDS = 86400


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
        return None
    except FileNotFoundError:
        logger.error("claude 命令未找到, 请确保已安装 Claude Code")
        return None
    except OSError as e:
        elapsed = time.monotonic() - t0
        logger.error(
            "claude 子进程 OS 错误: errno=%s msg=%s elapsed=%.1fs args=%s",
            getattr(e, "errno", None), e, elapsed, args,
        )
        return None

    elapsed = time.monotonic() - t0
    if result.returncode != 0:
        logger.error(
            "claude 子进程失败: returncode=%s, elapsed=%.1fs, stderr[:500]=%r, stdout[:200]=%r, args=%s",
            result.returncode, elapsed, result.stderr[:500], result.stdout[:200], args,
        )
        return None
    logger.info(
        "claude 子进程成功: elapsed=%.1fs, stdout_len=%d",
        elapsed, len(result.stdout),
    )
    return result.stdout.strip()