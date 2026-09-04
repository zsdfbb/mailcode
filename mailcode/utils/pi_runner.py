"""Pi agent runner -- 通过 `pi -p` 子进程调用 Pi coding agent。"""
import logging
from typing import Optional

from mailcode.utils.agent import BaseAgentRunner

logger = logging.getLogger(__name__)


class PiRunner(BaseAgentRunner):
    """Pi coding agent runner (pi 0.84.4+)。

    注意: Pi 不硬编码 --provider -- 用户环境差异大 (直连 anthropic vs openrouter vs google)。
    用户在 config agents.pi.extra_args 自填 provider。
    """

    name = "pi"

    def _command(self) -> str:
        return "pi"

    def _build_args(self, *, session_id: Optional[str], resume: bool,
                    no_session: bool) -> list[str]:
        args = ["pi", "-p"]
        if session_id is not None:
            args.extend(["--session-id", session_id])
        if resume:
            args.append("-c")
        if no_session:
            args.append("--no-session")
        return args

    def hint_for_failure(self) -> str:
        return (
            "Pi CLI 未安装或不在 PATH 中。"
            "请运行 'npm i -g @mariozechner/pi-coding-agent' 安装, "
            "然后 'pi /login' 配置 provider。"
            "注意 Pi 默认 provider 是 google, 推荐配 'openrouter' 或 'anthropic' 走 Claude 模型。"
        )
