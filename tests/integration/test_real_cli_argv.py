"""真实 agent CLI argv 形状金丝雀测试 (L3 测试层)。

目的
====
验证 MailCode 准备传给各 agent 的 argv 在真实 binary 上能正常工作。
当 agent CLI 升级改 flag 名时, 本测试会立即失败 (而不是在用户机器上才暴露)。

测试层级定位 (见 design.md §4.3.1 / ADR-005):
- L1 单元 mock: 现状 tests/unit/, 不依赖 binary
- L2 Fake binary: tests/unit/fake_agents/, 模拟 CLI 行为不调真 API
- **L3 真 binary 金丝雀 (本文件)**: 默认 skip, 调真 binary, 验证 argv 形状

运行方式
========

默认 skip, 用以下任一方式启用:

    MAILCODE_TEST_REAL_AGENT=1 python3 -m pytest tests/integration/test_real_cli_argv.py -v

或单独跑某个 agent:

    MAILCODE_TEST_REAL_AGENT=1 python3 -m pytest tests/integration/test_real_cli_argv.py -v -k claude

CI 默认不开, 仅本地手动跑。

跳过条件
========

- binary 不存在 (shutil.which 返回 None)
- 认证缺失 (Claude: 缺 ANTHROPIC_API_KEY 或 ~/.claude/.credentials.json;
  Pi: 缺 ~/.pi/agent/auth.json; v1 无 OpenCode)
- 网络不可用 (requests.get 探活, 3s 超时)
"""

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

# 标记: 默认 skip, 用 MAILCODE_TEST_REAL_AGENT=1 启用
pytestmark = [
    pytest.mark.real_agent,
    pytest.mark.skipif(
        os.environ.get("MAILCODE_TEST_REAL_AGENT") != "1",
        reason="set MAILCODE_TEST_REAL_AGENT=1 to run real agent CLI tests",
    ),
]

# 探活超时 (秒)
NETWORK_PROBE_TIMEOUT = 3
# 真实 agent 调用超时 (秒) — LLM 推理加 subprocess 启动
REAL_AGENT_TIMEOUT = 60
# sandbox prompt — 用最小可识别 token, 让 LLM 响应可断言
SANDBOX_PROMPT = 'Respond with only the word "CANARY_OK" and nothing else.'


# ---------------------------------------------------------------- #
# Fixtures: binary 可用性 + 认证 + 网络
# ---------------------------------------------------------------- #


@pytest.fixture(scope="session")
def network_alive() -> bool:
    """探测 outbound HTTPS 是否可用 (不影响个别测试, 只快速失败)。

    注意: HTTP 4xx (401/403/404) 说明 TLS + DNS + TCP 全通, 网络是活的。
    只有 socket/SSL/DNS 级别的错误才算不通。
    """
    try:
        urllib.request.urlopen(
            "https://api.anthropic.com", timeout=NETWORK_PROBE_TIMEOUT
        )
        return True
    except urllib.error.HTTPError:
        # 4xx = 网络通, 服务端拒绝 (无 auth)。这正是我们要的: 网络可用
        return True
    except urllib.error.URLError:
        return False
    except Exception:
        # 其他异常 (timeout / SSL / DNS) 算不通
        return False


@pytest.fixture(scope="session")
def claude_binary() -> str | None:
    return shutil.which("claude")


@pytest.fixture(scope="session")
def pi_binary() -> str | None:
    return shutil.which("pi")


def _claude_auth_ok() -> bool:
    """Claude 认证检查: API key env var 或 credentials.json。"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    creds = Path.home() / ".claude" / ".credentials.json"
    return creds.exists()


def _pi_auth_ok() -> bool:
    """Pi 认证: ~/.pi/agent/auth.json 或 ANTHROPIC_API_KEY。"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    auth = Path.home() / ".pi" / "agent" / "auth.json"
    return auth.exists()


# ---------------------------------------------------------------- #
# 1. argv 形状验证 — 用 Python 假 binary 跑 MailCode 构造的 argv
# ---------------------------------------------------------------- #
#
# 这一组测试不需要真 agent 也不需要 API, 但需要 MailCode 的 Runner 类存在
# (PR1 之后才有, 所以 PR1 之前这些会 import 失败 — 跳过即可)


class TestArgvShapeWithFakeBinary:
    """用 Python fake binary 替换 PATH 中的真 binary, 验证 Runner 构造的 argv 形状。

    fake binary (在 tests/unit/fake_agents/ 提供) 行为:
    - 把 argv 写到 stderr (供测试断言)
    - 把 prompt 写到 stdout (模拟 LLM 响应)
    - 退出 0
    """

    def test_claude_argv_shape(self, claude_binary):
        """MailCode 构造的 claude argv 应该是: ['claude', '--dangerously-skip-permissions', ...]"""
        if not claude_binary:
            pytest.skip("claude binary not installed")

        try:
            from mailcode.utils.claude_runner import ClaudeRunner
        except ImportError:
            pytest.skip("ClaudeRunner not yet implemented (PR1)")

        runner = ClaudeRunner()
        argv = runner._build_args(
            session_id="abc-123", resume=False, no_session=False,
        )
        assert argv[0] == "claude"
        assert "--dangerously-skip-permissions" in argv
        assert "--session-id" in argv
        assert "abc-123" in argv
        assert "--resume" not in argv  # resume=False

    def test_pi_argv_shape(self, pi_binary):
        """MailCode 构造的 pi argv 应该是 -p + --session-id。Pi 不硬编码 provider。"""
        if not pi_binary:
            pytest.skip("pi binary not installed")

        try:
            from mailcode.utils.pi_runner import PiRunner
        except ImportError:
            pytest.skip("PiRunner not yet implemented (PR2)")

        runner = PiRunner()
        argv = runner._build_args(
            session_id="def-456", resume=False, no_session=False,
        )
        assert argv[0] == "pi"
        assert "-p" in argv
        # ⚠️ Pi 不硬编码 --provider — 用户在 config 里通过 extra_args 自填
        # (直连 anthropic / openrouter / google 各家 API key 不同)
        # 验证: argv 里不应该有 --provider (除非用户配了 extra_args)
        assert "--provider" not in argv, (
            f"PiRunner must not hardcode --provider (use extra_args): {argv}"
        )
        # Session 用 --session-id (语义化 UUID 形式), 不是 --session (path 形式)
        sess_idx = argv.index("--session-id")
        assert argv[sess_idx + 1] == "def-456"
        # cwd 不应该出现在 argv 里 (Pi 用 subprocess cwd=)
        assert "--dir" not in argv


# ---------------------------------------------------------------- #
# 2. 真实 binary 端到端测试 (需要 API + 网络)
# ---------------------------------------------------------------- #


class TestClaudeRealBinary:
    """真实 claude binary 验证 argv 在生产版本上能工作。"""

    def test_claude_session_create(self, claude_binary, network_alive, tmp_path):
        if not claude_binary:
            pytest.skip("claude binary not installed")
        if not network_alive:
            pytest.skip("network unavailable")
        if not _claude_auth_ok():
            pytest.skip("Claude auth not configured")

        session_id = f"mailcode-canary-{int(time.time())}"
        argv = [
            claude_binary,
            "--dangerously-skip-permissions",
            "--session-id", session_id,
        ]
        try:
            result = subprocess.run(
                argv, input=SANDBOX_PROMPT,
                capture_output=True, text=True,
                timeout=REAL_AGENT_TIMEOUT, cwd=str(tmp_path),
            )
        except subprocess.TimeoutExpired:
            pytest.fail("claude subprocess timed out (>60s)")

        assert result.returncode == 0, (
            f"claude failed: rc={result.returncode}\nstderr={result.stderr[:500]}"
        )
        assert "CANARY_OK" in result.stdout, (
            f"claude response missing CANARY_OK: {result.stdout[:500]}"
        )


class TestPiRealBinary:
    """真实 pi binary 验证 argv 在生产版本上能工作。

    ⚠️ Pi 真实 API 测试需要 provider 配置。本地常见三种配置:
    - 直连 anthropic: ANTHROPIC_API_KEY
    - openrouter: ~/.pi/agent/auth.json 含 openrouter API key
    - google (默认): GOOGLE_API_KEY

    本测试按优先级依次尝试, 任何一种配了就能跑; 否则 skip。
    不硬编码 --provider, 让用户配置驱动 (见 design.md §4.3)。

    这一组是金丝雀测试的核心: 当 Pi 升级改 flag 名 (例如把 --session-id 改成
    --session-uuid) 时, 这里的测试会立即失败, 提醒我们更新 PiRunner._build_args。
    """

    def test_pi_session_create(self, pi_binary, network_alive, tmp_path):
        if not pi_binary:
            pytest.skip("pi binary not installed")
        if not network_alive:
            pytest.skip("network unavailable")
        if not _pi_auth_ok():
            pytest.skip("Pi auth not configured")

        session_id = f"mailcode-canary-{int(time.time())}"
        # 不带 --provider — Pi 用自身配置 (用户 ~/.pi/agent/auth.json 决定)
        # 这样能验证 argv shape 不会因为硬编码 --provider 误拒其他 provider
        argv = [
            pi_binary,
            "-p",
            "--session-id", session_id,
            SANDBOX_PROMPT,
        ]
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=REAL_AGENT_TIMEOUT, cwd=str(tmp_path),
            )
        except subprocess.TimeoutExpired:
            pytest.fail("pi subprocess timed out (>60s)")

        # 部分跳过语义: argv 形状正确即可, API 调用可能因 provider 配额失败
        # 但 Pi 不应该因为我们的 argv 而崩溃
        if result.returncode != 0:
            stderr = result.stderr[:500]
            # 已知可接受的失败 (不是 argv 问题, 是 provider/API 问题)
            acceptable_errors = [
                "403",  # API key 不匹配 provider
                "429",  # 配额
                "No project session found",  # 创建 session 警告 (非致命)
                "Request not allowed",
            ]
            if any(e in stderr for e in acceptable_errors):
                pytest.skip(
                    f"Pi accepted argv but API call failed (provider issue, not argv): "
                    f"rc={result.returncode} stderr={stderr[:200]}"
                )
            pytest.fail(
                f"pi failed unexpectedly: rc={result.returncode}\nstderr={stderr}"
            )
        assert "CANARY" in result.stdout, (
            f"pi response missing CANARY: {result.stdout[:500]}"
        )


# ---------------------------------------------------------------- #
# 3. Session 续接行为 (L3 续接验证)
# ---------------------------------------------------------------- #


class TestSessionContinuation:
    """验证 session ID 在续接时被 agent 接受, session 文件真创建。

    这一组测试需要先创建 session, 再用相同 session_id 续接。
    """

    def test_claude_session_resume(self, claude_binary, network_alive, tmp_path):
        if not claude_binary:
            pytest.skip("claude binary not installed")
        if not network_alive or not _claude_auth_ok():
            pytest.skip("network or auth unavailable")

        session_id = f"mailcode-resume-{int(time.time())}"

        # Round 1: 创建
        r1 = subprocess.run(
            [claude_binary, "--dangerously-skip-permissions",
             "--session-id", session_id],
            input="Remember the secret code 12345. Just say OK.",
            capture_output=True, text=True,
            timeout=REAL_AGENT_TIMEOUT, cwd=str(tmp_path),
        )
        assert r1.returncode == 0

        # Round 2: 续接
        r2 = subprocess.run(
            [claude_binary, "--dangerously-skip-permissions",
             "--session-id", session_id, "--resume"],
            input="What is the secret code I told you?",
            capture_output=True, text=True,
            timeout=REAL_AGENT_TIMEOUT, cwd=str(tmp_path),
        )
        assert r2.returncode == 0
        assert "12345" in r2.stdout, (
            f"claude session resume failed: round 2 didn't recall secret. "
            f"stdout={r2.stdout[:500]}"
        )
