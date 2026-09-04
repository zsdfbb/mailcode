"""路径模块测试。"""
from mailcode.utils.paths import (
    agent_home, transcripts_dir, conversations_dir, sessions_file,
    legacy_sessions_file, legacy_transcripts_dir, legacy_conversations_dir,
    _MAILCODE_HOME,
)


class TestAgentPaths:
    def test_agent_home_claude(self):
        assert agent_home("claude") == _MAILCODE_HOME / "claude"

    def test_agent_home_pi(self):
        assert agent_home("pi") == _MAILCODE_HOME / "pi"

    def test_transcripts_dir(self):
        assert transcripts_dir("claude") == _MAILCODE_HOME / "claude" / "transcripts"

    def test_conversations_dir(self):
        assert conversations_dir("pi") == _MAILCODE_HOME / "pi" / "conversations"

    def test_sessions_file(self):
        assert sessions_file("claude") == _MAILCODE_HOME / "claude" / "sessions.json"


class TestLegacyPaths:
    def test_legacy_sessions_file(self):
        assert legacy_sessions_file() == _MAILCODE_HOME / "claude_sessions.json"

    def test_legacy_transcripts_dir(self):
        assert legacy_transcripts_dir() == _MAILCODE_HOME / "transcripts"

    def test_legacy_conversations_dir(self):
        assert legacy_conversations_dir() == _MAILCODE_HOME / "conversations"

    def test_legacy_paths_have_no_agent_param(self):
        """旧路径不含 agent 参数 (全局共享)。"""
        assert legacy_sessions_file() == legacy_sessions_file()
