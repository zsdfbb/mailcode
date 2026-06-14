"""Claude 子进程调用器 — 供 ConversationHandler / Scheduler 复用。

抽出此模块是为了避免 scheduler 与 conversation_handler 双份实现
``claude -p`` 调用逻辑导致行为漂移 (超时、参数、cwd 默认值等)。
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# claude 子进程超时 (秒) — 默认 24h, 覆盖定时任务写长文的场景
CLAUDE_TIMEOUT_SECONDS = 86400


def call_claude(prompt: str, cwd: str = "") -> Optional[str]:
    """调用 ``claude`` 子进程 (stdin 传 prompt)。失败返回 None。

    Args:
        prompt: 完整 prompt
        cwd: 工作目录 (默认 ``Path.home()``)
    """
    cwd = cwd or str(Path.home())
    try:
        result = subprocess.run(
            ["claude", "--dangerously-skip-permissions"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            cwd=cwd,
        )
        if result.returncode != 0:
            logger.error("claude stdin 失败: %s", result.stderr[:500])
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error("claude stdin 超时")
        return None
    except FileNotFoundError:
        logger.error("claude 命令未找到, 请确保已安装 Claude Code")
        return None