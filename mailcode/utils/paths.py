"""路径派生 — 集中所有 agent 数据目录路径, 替代各 handler 里的硬编码常量。"""
from pathlib import Path

_MAILCODE_HOME = Path.home() / ".config" / "mailcode"


def agent_home(agent: str) -> Path:
    """Agent 数据根目录: ~/.config/mailcode/<agent>/"""
    return _MAILCODE_HOME / agent


def transcripts_dir(agent: str) -> Path:
    """对话归档: ~/.config/mailcode/<agent>/transcripts/"""
    return agent_home(agent) / "transcripts"


def conversations_dir(agent: str) -> Path:
    """会话数据: ~/.config/mailcode/<agent>/conversations/"""
    return agent_home(agent) / "conversations"


def sessions_file(agent: str) -> Path:
    """映射文件: ~/.config/mailcode/<agent>/sessions.json"""
    return agent_home(agent) / "sessions.json"


# ---- 旧路径 (fallback 用) ----

def legacy_sessions_file() -> Path:
    """旧映射: ~/.config/mailcode/claude_sessions.json"""
    return _MAILCODE_HOME / "claude_sessions.json"


def legacy_transcripts_dir() -> Path:
    """旧归档: ~/.config/mailcode/transcripts/"""
    return _MAILCODE_HOME / "transcripts"


def legacy_conversations_dir() -> Path:
    """旧会话: ~/.config/mailcode/conversations/"""
    return _MAILCODE_HOME / "conversations"
