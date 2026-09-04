#!/usr/bin/env python3
"""mailcode chat — 终端交互模式，直接与 Claude 对话"""

import logging
import uuid
from pathlib import Path

from mailcode.utils.agent import get_runner

logger = logging.getLogger(__name__)

# 会话映射文件路径
_CHAT_MAPPING = Path.home() / ".config" / "mailcode" / "chat_sessions.json"


def cmd_chat(args):
    """启动交互式 chat REPL。"""
    agent_name = getattr(args, "agent", "claude")
    runner = get_runner(agent_name)

    session_id = args.session_id or str(uuid.uuid4())
    resume = bool(args.session_id)  # if session_id provided, resume

    print(f"MailCode Chat -- 直接与 {agent_name} 对话")
    if resume:
        print(f"   恢复对话: {session_id[:12]}")
    else:
        print(f"   新对话: {session_id[:12]}")
    print("   输入 /exit 退出, /new 开新对话, /session 显示会话 ID")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input == "/exit":
            break
        elif user_input == "/new":
            session_id = str(uuid.uuid4())
            resume = False
            print(f"新对话已开始: {session_id[:12]}")
            continue
        elif user_input == "/session":
            print(f"会话 ID: {session_id}")
            continue

        # Call agent
        print("", end="", flush=True)
        response = runner.call(user_input, cwd=args.cwd or str(Path.home()),
                               session_id=session_id, resume=resume)

        if response is None:
            print(f"Agent 调用失败 (agent={agent_name}, 检查是否已安装)")
            continue

        if not response:
            print("(无回复)")
            continue

        print(response)
        resume = True  # Subsequent calls use --resume

    print("再见")
