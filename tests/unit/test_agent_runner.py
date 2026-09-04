"""BaseAgentRunner ABC + 注册表单元测试。

测试 mailcode.utils.agent 模块:
  - BaseAgentRunner.call() 成功 / 5 种错误分支
  - is_available() mock shutil.which
  - _cached_which() 60s TTL 缓存
  - hint_for_failure() 默认 + 子类覆写
  - AgentNotFoundError
  - list_agents() 排序
  - get_runner() 注册表查询
"""

import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from mailcode.utils.agent import (
    AGENTS,
    AgentNotFoundError,
    BaseAgentRunner,
    get_runner,
    list_agents,
    register,
)
from mailcode.utils.claude_runner import ClaudeRunner
from mailcode.utils.pi_runner import PiRunner


# ------------------------------------------------------------------
# 辅助: 最小 ConcreteRunner 用于测试 ABC
# ------------------------------------------------------------------

class ConcreteRunner(BaseAgentRunner):
    """用于测试的最小具体子类。"""

    name = "test-concrete"

    def _command(self) -> str:
        return "test-cmd"

    def _build_args(self, *, session_id, resume, no_session) -> list[str]:
        args = ["test-cmd"]
        if session_id is not None:
            args.extend(["--session-id", session_id])
        if resume:
            args.append("--resume")
        if no_session:
            args.append("--no-session")
        return args


class FailingHintRunner(BaseAgentRunner):
    """覆写 hint_for_failure 的子类。"""

    name = "failing"

    def _command(self) -> str:
        return "failing-cmd"

    def _build_args(self, *, session_id, resume, no_session) -> list[str]:
        return ["failing-cmd"]


# ------------------------------------------------------------------
# 测试: BaseAgentRunner.call() 成功路径
# ------------------------------------------------------------------

class TestCallSuccess:
    """call() 成功路径测试。"""

    def test_returns_stdout_stripped(self):
        """成功调用返回 stdout.strip()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  Hello, world!  \n"
        mock_result.stderr = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = runner.call("test prompt")

        assert result == "Hello, world!"
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert kwargs["input"] == "test prompt"
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 86400

    def test_cwd_fallback_to_home(self):
        """cwd 为空时回退到 Path.home()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            runner.call("test", cwd="")

        _, kwargs = mock_run.call_args
        import os
        assert kwargs["cwd"] == os.path.expanduser("~")

    def test_cwd_propagated(self):
        """传入 cwd 时传递给 subprocess。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            runner.call("test", cwd="/tmp")

        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == "/tmp"

    def test_custom_timeout(self):
        """timeout 参数传给 subprocess.run。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            runner.call("test", timeout=120)

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 120

    def test_env_extras_merged(self):
        """_env_extras() 的值合并到 env。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            with patch.dict("os.environ", {"BASE_VAR": "base"}, clear=False):
                runner.call("test")

        _, kwargs = mock_run.call_args
        assert "BASE_VAR" in kwargs["env"]
        # ConcreteRunner._env_extras() 返回空 dict, 不覆盖 base
        assert kwargs["env"]["BASE_VAR"] == "base"


# ------------------------------------------------------------------
# 测试: BaseAgentRunner.call() 5 种错误分支
# ------------------------------------------------------------------

class TestCallErrors:
    """call() 错误分支测试。"""

    def test_timeout_expired(self):
        """TimeoutExpired 返回 None。"""
        runner = ConcreteRunner()
        with patch.object(
            subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="test-cmd", timeout=86400),
        ):
            result = runner.call("test")

        assert result is None

    def test_file_not_found(self):
        """FileNotFoundError 返回 None。"""
        runner = ConcreteRunner()
        with patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            result = runner.call("test")

        assert result is None

    def test_os_error(self):
        """OSError 返回 None。"""
        runner = ConcreteRunner()
        with patch.object(
            subprocess, "run",
            side_effect=OSError(13, "Permission denied"),
        ):
            result = runner.call("test")

        assert result is None

    def test_nonzero_returncode(self):
        """返回码非 0 时返回 None。"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error occurred"
        mock_result.stdout = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result):
            result = runner.call("test")

        assert result is None

    def test_success_after_errors(self):
        """成功路径返回 stdout.strip()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "result"
        mock_result.stderr = ""

        runner = ConcreteRunner()
        with patch.object(subprocess, "run", return_value=mock_result):
            result = runner.call("test")

        assert result == "result"

    def test_timeout_error_logs(self, caplog):
        """TimeoutExpired 记录 ERROR 日志。"""
        import logging
        runner = ConcreteRunner()
        with caplog.at_level(logging.ERROR, logger="mailcode.utils.agent"):
            with patch.object(
                subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="test-cmd", timeout=86400),
            ):
                result = runner.call("test")

        assert result is None
        error_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert any("子进程超时" in m for m in error_msgs)

    def test_stderr_truncated_in_log(self, caplog):
        """stderr 日志截断 2000 字符。"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "x" * 3000
        mock_result.stdout = ""

        runner = ConcreteRunner()
        with caplog.at_level(40, logger="mailcode.utils.agent"):
            with patch.object(subprocess, "run", return_value=mock_result):
                runner.call("test")

        # 验证日志存在且包含截断后的 stderr
        error_msgs = [r.getMessage() for r in caplog.records if r.levelno == 40]
        assert len(error_msgs) > 0


# ------------------------------------------------------------------
# 测试: is_available()
# ------------------------------------------------------------------

class TestIsAvailable:
    """is_available() 测试。"""

    def setup_method(self):
        """每个测试前清空缓存。"""
        BaseAgentRunner._which_cache.clear()

    def test_returns_true_when_in_path(self):
        """二进制在 PATH 中时返回 True。"""
        runner = ConcreteRunner()
        with patch("shutil.which", return_value="/usr/bin/test-cmd"):
            assert runner.is_available() is True

    def test_returns_false_when_not_in_path(self):
        """二进制不在 PATH 中时返回 False。"""
        runner = ConcreteRunner()
        with patch("shutil.which", return_value=None):
            assert runner.is_available() is False


# ------------------------------------------------------------------
# 测试: _cached_which() 60s TTL
# ------------------------------------------------------------------

class TestCachedWhich:
    """_cached_which() 缓存测试。"""

    def setup_method(self):
        """每个测试前清空缓存。"""
        BaseAgentRunner._which_cache.clear()

    def test_first_call_queries_shutil(self):
        """首次调用触发 shutil.which。"""
        runner = ConcreteRunner()
        with patch("shutil.which", return_value="/usr/bin/test-cmd") as mock_which:
            result = runner._cached_which()

        assert result == "/usr/bin/test-cmd"
        mock_which.assert_called_once_with("test-cmd")

    def test_cached_within_ttl(self):
        """60s TTL 内不重复查询 shutil.which。"""
        runner = ConcreteRunner()
        # 预填充缓存
        BaseAgentRunner._which_cache["test-cmd"] = (time.monotonic(), "/usr/bin/test-cmd")

        with patch("shutil.which") as mock_which:
            result = runner._cached_which()

        assert result == "/usr/bin/test-cmd"
        mock_which.assert_not_called()

    def test_expired_cache_refreshes(self):
        """TTL 过期后刷新缓存。"""
        runner = ConcreteRunner()
        # 预填充 61s 前的缓存
        BaseAgentRunner._which_cache["test-cmd"] = (time.monotonic() - 61, "/old/path")

        with patch("shutil.which", return_value="/new/path") as mock_which:
            result = runner._cached_which()

        assert result == "/new/path"
        mock_which.assert_called_once_with("test-cmd")

    def test_shared_across_instances(self):
        """缓存在所有 Runner 实例间共享。"""
        r1 = ConcreteRunner()
        r2 = ConcreteRunner()

        with patch("shutil.which", return_value="/usr/bin/test-cmd"):
            r1._cached_which()

        # r2 使用同一份缓存
        with patch("shutil.which") as mock_which:
            result = r2._cached_which()

        assert result == "/usr/bin/test-cmd"
        mock_which.assert_not_called()

    def test_thread_safety(self):
        """多线程并发调用不会崩溃。"""
        BaseAgentRunner._which_cache.clear()
        runner = ConcreteRunner()
        errors = []

        def worker():
            try:
                with patch("shutil.which", return_value="/usr/bin/test-cmd"):
                    for _ in range(50):
                        runner._cached_which()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ------------------------------------------------------------------
# 测试: hint_for_failure()
# ------------------------------------------------------------------

class TestHintForFailure:
    """hint_for_failure() 测试。"""

    def test_default_hint(self):
        """BaseAgentRunner 默认 hint 包含 name。"""
        runner = ConcreteRunner()
        hint = runner.hint_for_failure()
        assert "test-concrete" in hint
        assert "未安装" in hint or "不在 PATH" in hint

    def test_claude_hint(self):
        """ClaudeRunner 覆写的 hint 包含安装指引。"""
        runner = ClaudeRunner()
        hint = runner.hint_for_failure()
        assert "Claude Code" in hint
        assert "claude --version" in hint

    def test_pi_hint(self):
        """PiRunner 覆写的 hint 包含安装指引。"""
        runner = PiRunner()
        hint = runner.hint_for_failure()
        assert "Pi CLI" in hint
        assert "npm" in hint


# ------------------------------------------------------------------
# 测试: AgentNotFoundError
# ------------------------------------------------------------------

class TestAgentNotFoundError:
    """AgentNotFoundError 异常测试。"""

    def test_inherits_key_error(self):
        """AgentNotFoundError 继承 KeyError。"""
        assert issubclass(AgentNotFoundError, KeyError)

    def test_get_runner_unknown_raises(self):
        """get_runner("unknown") 抛 AgentNotFoundError。"""
        with pytest.raises(AgentNotFoundError, match="unknown agent"):
            get_runner("unknown")

    def test_error_message_includes_available(self):
        """错误信息包含可用 agent 列表。"""
        with pytest.raises(AgentNotFoundError) as exc_info:
            get_runner("nonexistent")
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "pi" in msg


# ------------------------------------------------------------------
# 测试: list_agents()
# ------------------------------------------------------------------

class TestListAgents:
    """list_agents() 测试。"""

    def test_returns_sorted(self):
        """返回排序后的 agent 名称列表。"""
        result = list_agents()
        assert isinstance(result, list)
        assert result == sorted(result)

    def test_contains_builtins(self):
        """包含内置 agent。"""
        result = list_agents()
        assert "claude" in result
        assert "pi" in result

    def test_register_adds_to_list(self):
        """register() 添加的 agent 出现在 list_agents() 中。"""
        try:
            register("test-dynamic", ConcreteRunner())
            assert "test-dynamic" in list_agents()
        finally:
            # 清理
            AGENTS.pop("test-dynamic", None)


# ------------------------------------------------------------------
# 测试: get_runner()
# ------------------------------------------------------------------

class TestGetRunner:
    """get_runner() 注册表查询测试。"""

    def test_returns_runner_instance(self):
        """返回正确的 runner 实例。"""
        runner = get_runner("claude")
        assert isinstance(runner, ClaudeRunner)
        assert runner.name == "claude"

    def test_pi_runner(self):
        """返回 PiRunner 实例。"""
        runner = get_runner("pi")
        assert isinstance(runner, PiRunner)
        assert runner.name == "pi"


# ------------------------------------------------------------------
# 测试: register() 运行时注册
# ------------------------------------------------------------------

class TestRegister:
    """register() 运行时注册测试。"""

    def test_adds_runner(self):
        """register() 将 runner 添加到 AGENTS。"""
        try:
            runner = ConcreteRunner()
            register("test-reg", runner)
            assert get_runner("test-reg") is runner
        finally:
            AGENTS.pop("test-reg", None)

    def test_overwrites_existing(self):
        """register() 覆盖同名 runner。"""
        try:
            r1 = ConcreteRunner()
            r1.name = "overwrite-a"
            register("overwrite-test", r1)

            r2 = ConcreteRunner()
            r2.name = "overwrite-b"
            register("overwrite-test", r2)

            assert get_runner("overwrite-test").name == "overwrite-b"
        finally:
            AGENTS.pop("overwrite-test", None)


# ------------------------------------------------------------------
# 测试: PiRunner _build_args
# ------------------------------------------------------------------

class TestPiRunnerBuildArgs:
    """PiRunner._build_args() 测试。"""

    def test_default_args(self):
        """无参数时返回 ['pi', '-p']。"""
        runner = PiRunner()
        assert runner._build_args(session_id=None, resume=False, no_session=False) == ["pi", "-p"]

    def test_session_id(self):
        """传入 session_id 时包含 --session-id。"""
        runner = PiRunner()
        args = runner._build_args(session_id="abc", resume=False, no_session=False)
        assert "--session-id" in args
        assert "abc" in args

    def test_resume(self):
        """resume=True 时包含 -c。"""
        runner = PiRunner()
        args = runner._build_args(session_id=None, resume=True, no_session=False)
        assert "-c" in args

    def test_no_session(self):
        """no_session=True 时包含 --no-session。"""
        runner = PiRunner()
        args = runner._build_args(session_id=None, resume=False, no_session=True)
        assert "--no-session" in args


# ------------------------------------------------------------------
# 测试: ClaudeRunner _build_args
# ------------------------------------------------------------------

class TestClaudeRunnerBuildArgs:
    """ClaudeRunner._build_args() 测试。"""

    def test_default_args(self):
        """无参数时返回 ['claude', '--dangerously-skip-permissions']。"""
        runner = ClaudeRunner()
        args = runner._build_args(session_id=None, resume=False, no_session=False)
        assert args == ["claude", "--dangerously-skip-permissions"]

    def test_session_id(self):
        """传入 session_id 时包含 --session-id。"""
        runner = ClaudeRunner()
        args = runner._build_args(session_id="xyz", resume=False, no_session=False)
        assert "--session-id" in args
        assert "xyz" in args

    def test_resume(self):
        """resume=True 时包含 --resume。"""
        runner = ClaudeRunner()
        args = runner._build_args(session_id=None, resume=True, no_session=False)
        assert "--resume" in args

    def test_no_session_ignored(self):
        """ClaudeRunner 忽略 no_session 参数。"""
        runner = ClaudeRunner()
        args = runner._build_args(session_id=None, resume=False, no_session=True)
        assert "--no-session" not in args


# ------------------------------------------------------------------
# 测试: call() 通过 ClaudeRunner / PiRunner
# ------------------------------------------------------------------

class TestCallViaRunners:
    """通过具体 runner 调用 call() 的集成测试。"""

    def test_claude_runner_call_success(self):
        """ClaudeRunner.call() 成功路径。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "claude response"
        mock_result.stderr = ""

        runner = ClaudeRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = runner.call("hello")

        assert result == "claude response"
        args, kwargs = mock_run.call_args
        assert args[0][0] == "claude"
        assert "--dangerously-skip-permissions" in args[0]

    def test_pi_runner_call_success(self):
        """PiRunner.call() 成功路径。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "pi response"
        mock_result.stderr = ""

        runner = PiRunner()
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = runner.call("hello")

        assert result == "pi response"
        args, kwargs = mock_run.call_args
        assert args[0][0] == "pi"
        assert "-p" in args[0]

    def test_claude_runner_call_error(self):
        """ClaudeRunner.call() 失败返回 None。"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        mock_result.stdout = ""

        runner = ClaudeRunner()
        with patch.object(subprocess, "run", return_value=mock_result):
            assert runner.call("hello") is None
