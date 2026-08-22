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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 子进程默认超时 (秒) -- 24h 兜底, 实际调用方应传更短的值
# (Scheduler 默认 1800s, ConversationHandler 用 session.response_timeout_seconds)
DEFAULT_TIMEOUT_SECONDS = 86400

# 失败日志里 stdout / stderr 的截断长度
_STDOUT_TAIL = 200
_STDERR_TAIL = 500


class AgentErrorKind(str, Enum):
    """``call_ex`` 的失败分类 — Scheduler 据此决定是否重试。"""

    TIMEOUT = "timeout"        # 子进程超时 → 不重试 (已吃满超时, 重试只是翻倍耗时)
    NOT_FOUND = "not_found"    # CLI 未安装 → 不重试 (装了才能好)
    OS_ERROR = "os_error"      # OS 层错误 (BrokenPipe 等) → 可重试 (多为瞬时)
    NONZERO_EXIT = "nonzero_exit"  # 子进程非零退出, 通常上游 API 错误 → 可重试


@dataclass
class AgentResult:
    """``call_ex`` 的返回值 — 结构化携带成功输出或失败详情。

    Attributes:
        ok: True 表示调用成功 (``output`` 有值, 可能为空串)
        output: 成功时的 ``stdout.strip()``
        kind: 失败分类 (成功时为 None), 见 ``AgentErrorKind``
        error: 人可读失败详情, 可直接写进 ``last_error`` / 错误邮件
        exit_code: 非零退出时的 returncode
        stdout_tail: stdout 截断尾部 (失败排查用)
        stderr_tail: stderr 截断尾部 (失败排查用)
        elapsed: 子进程耗时 (秒)
    """

    ok: bool
    output: Optional[str] = None
    kind: Optional[AgentErrorKind] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    elapsed: float = 0.0


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
        """简单版入口: 调子进程, 成功返回 stdout.strip(), 失败返回 None。

        只关心"有没有结果"的调用方用这个; 需要失败分类/详情做重试或精细
        错误处理时改用 ``call_ex``。
        """
        result = self.call_ex(
            prompt, cwd, session_id=session_id, resume=resume,
            no_session=no_session, timeout=timeout,
        )
        return result.output if result.ok else None

    def call_ex(self, prompt: str, cwd: str = "",
                *, session_id: Optional[str] = None, resume: bool = False,
                no_session: bool = False, timeout: Optional[int] = None) -> AgentResult:
        """结构化版入口: 调子进程, 返回 ``AgentResult``。

        模板方法: 拼 args + 跑 subprocess + 处理 4 类错误。失败不再压扁成
        ``None``, 而是带 ``kind``/``error``, 供调用方做瞬态重试、把真实原因
        写入持久化状态和错误邮件。
        所有 Runner 共享 -- 不要在子类重写。
        """
        cwd = cwd or str(Path.home())
        args = self._build_args(
            session_id=session_id, resume=resume, no_session=no_session,
        )
        env = {**os.environ, **self._env_extras()}
        effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS

        logger.info(
            "%s 子进程启动: prompt_len=%d, timeout=%ds, cwd=%s, args=%s",
            self.name, len(prompt), effective_timeout, cwd, args,
        )
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                args, input=prompt, capture_output=True,
                text=True, timeout=effective_timeout, cwd=cwd, env=env,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            logger.error(
                "%s 子进程超时 (>%ds, elapsed=%.1fs, cwd=%s)",
                self.name, effective_timeout, elapsed, cwd,
            )
            return AgentResult(
                ok=False,
                kind=AgentErrorKind.TIMEOUT,
                error=f"{self.name} 子进程超时 (>={effective_timeout}s, elapsed={elapsed:.1f}s)",
                elapsed=elapsed,
            )
        except FileNotFoundError:
            logger.error("%s 命令未找到", self.name)
            return AgentResult(
                ok=False,
                kind=AgentErrorKind.NOT_FOUND,
                error=f"{self.name} 命令未找到, 请确保已安装",
            )
        except OSError as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "%s 子进程 OS 错误: errno=%s msg=%s elapsed=%.1fs args=%s",
                self.name, getattr(e, "errno", None), e, elapsed, args,
            )
            return AgentResult(
                ok=False,
                kind=AgentErrorKind.OS_ERROR,
                error=(
                    f"{self.name} 子进程 OS 错误: {e} "
                    f"(errno={getattr(e, 'errno', None)})"
                ),
                elapsed=elapsed,
            )

        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            logger.error(
                "%s 子进程失败: returncode=%s, elapsed=%.1fs, "
                "stderr[:500]=%r, stdout[:200]=%r, args=%s",
                self.name, result.returncode, elapsed,
                result.stderr[:_STDERR_TAIL], result.stdout[:_STDOUT_TAIL], args,
            )
            # error 详情取 stdout 优先, 空再取 stderr, 保证上游 API 错误文案进 last_error
            detail = (result.stdout or result.stderr or "").strip()
            return AgentResult(
                ok=False,
                kind=AgentErrorKind.NONZERO_EXIT,
                error=(
                    f"{self.name} 子进程失败 "
                    f"returncode={result.returncode}: {detail[:500]}"
                ),
                exit_code=result.returncode,
                stdout_tail=result.stdout[:_STDOUT_TAIL],
                stderr_tail=result.stderr[:_STDERR_TAIL],
                elapsed=elapsed,
            )
        logger.info("%s 子进程成功: elapsed=%.1fs, stdout_len=%d",
                    self.name, elapsed, len(result.stdout))
        return AgentResult(ok=True, output=result.stdout.strip(), elapsed=elapsed)

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
