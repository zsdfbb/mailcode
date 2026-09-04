"""BaseAgentRunner ABC + 模块级 AGENTS 注册表。

所有 agent runner 的抽象基类和注册/查询 API。
设计依据: docs/arch/multi-agent/design.md §4.2
"""

import abc
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class BaseAgentRunner(abc.ABC):
    """所有 agent runner 的抽象基类。"""

    name: str  # "claude" / "pi"

    # —— 并发: 所有 Runner 实例共享这个缓存 + 锁 ——
    _which_cache: dict[str, tuple[float, Optional[str]]] = {}
    _which_lock = threading.Lock()

    # —— 公开 API ——
    def is_available(self) -> bool:
        """检查二进制是否在 PATH 中（缓存 60s, 线程安全）。"""
        return self._cached_which() is not None

    def hint_for_failure(self) -> str:
        """未安装 / 调用失败时的可读 hint (供错误邮件正文)。
        子类可重写以提供更具体的指引 (例如 'npm i -g ...')。"""
        return f"{self.name} CLI 未安装或不在 PATH 中。请参考 README 安装指引。"

    def call(self, prompt: str, cwd: str = "",
             *, session_id: Optional[str] = None, resume: bool = False,
             no_session: bool = False, timeout: Optional[int] = None) -> Optional[str]:
        """主入口: 调子进程, 返回 stdout.strip() 或 None。

        模板方法: 拼 args + 跑 subprocess + 处理 5 类错误。
        所有 Runner 共享 -- 不要在子类重写。
        """
        cwd = cwd or str(Path.home())
        args = self._build_args(
            session_id=session_id, resume=resume, no_session=no_session,
        )
        env = {**os.environ, **self._env_extras()}

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                args, input=prompt, capture_output=True,
                text=True, timeout=timeout or 86400, cwd=cwd, env=env,
            )
        except subprocess.TimeoutExpired:
            logger.error("agent=%s 子进程超时 cwd=%s", self.name, cwd)
            return None
        except FileNotFoundError:
            logger.error("agent=%s 命令未找到", self.name)
            return None
        except OSError as e:
            logger.error("agent=%s OS 错误: %s", self.name, e)
            return None

        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            logger.error("agent=%s 失败: rc=%s elapsed=%.1fs stderr=%r",
                         self.name, result.returncode, elapsed, result.stderr[:2000])
            return None
        logger.info("agent=%s 成功: elapsed=%.1fs stdout_len=%d",
                    self.name, elapsed, len(result.stdout))
        return result.stdout.strip()

    # —— 子类必须实现 ——
    @abc.abstractmethod
    def _build_args(self, *, session_id: Optional[str], resume: bool,
                    no_session: bool) -> list[str]:
        """构造子进程 argv (不含 cwd / prompt / timeout -- 这些由 call() 模板统一处理)。"""

    @abc.abstractmethod
    def _command(self) -> str:
        """返回二进制名 (用于 shutil.which)。"""

    # —— 子类可重写 ——
    def _env_extras(self) -> dict[str, str]:
        """注入到子进程的环境变量。默认返回空 dict。"""
        return {}

    # —— 内部 ——
    def _cached_which(self) -> Optional[str]:
        """60s TTL 缓存 shutil.which (线程安全: 用 _which_lock 保护 read-modify-write)。"""
        cmd = self._command()
        with self._which_lock:
            now = time.monotonic()
            cached = self._which_cache.get(cmd)
            if cached and (now - cached[0]) < 60:
                return cached[1]
            path = shutil.which(cmd)
            self._which_cache[cmd] = (now, path)
            return path


# —— 自定义异常 ——
class AgentNotFoundError(KeyError):
    """请求的 agent 未注册 (拼写错 / v1 未实现)。"""


# —— 注册表 (模块级, import 后只读, 多线程读安全) ——
AGENTS: dict[str, BaseAgentRunner] = {}
_builtins_registered = False


def _ensure_builtins():
    """延迟注册内置 agent, 避免循环导入。"""
    global _builtins_registered
    if _builtins_registered:
        return
    _builtins_registered = True
    _register_builtins()


def get_runner(name: str) -> BaseAgentRunner:
    _ensure_builtins()
    if name not in AGENTS:
        raise AgentNotFoundError(
            f"unknown agent: {name!r}. available: {sorted(AGENTS)}. "
            f"配置 tasks[i].agent 请从可用列表选, 拼写错或 v1 未实现都会触发此异常。"
        )
    return AGENTS[name]


def list_agents() -> list[str]:
    _ensure_builtins()
    return sorted(AGENTS)


def register(name: str, runner: BaseAgentRunner):
    """运行时注册 (仅测试 / plugin 用)。"""
    AGENTS[name] = runner


# —— 启动时注册内置 agent ——
def _register_builtins():
    """注册内置 agent (claude / pi)。延迟导入避免循环依赖。"""
    try:
        from .claude_runner import ClaudeRunner
        AGENTS["claude"] = ClaudeRunner()
    except ImportError:
        logger.warning("claude_runner 不可用, 跳过 claude 注册")

    try:
        from .pi_runner import PiRunner
        AGENTS["pi"] = PiRunner()
    except ImportError:
        logger.warning("pi_runner 不可用, 跳过 pi 注册")
