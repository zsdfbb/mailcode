"""旧版 agent 数据迁移工具。

将 ~/.config/mailcode/ 下的旧版文件 (claude_sessions.json / transcripts / conversations)
迁移到 ~/.config/mailcode/<agent>/ 目录结构, 旧路径创建 symlink 指向新路径。

设计依据: docs/arch/multi-agent/design.md §4.3
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

_MAILCODE_HOME = Path.home() / ".config" / "mailcode"
_MARKER_FILE = _MAILCODE_HOME / "legacy-claude-migration.json"


def _scan_legacy_files() -> List[Tuple[Path, str]]:
    """扫描旧版文件, 返回 (旧路径, 类型) 列表。

    类型: "sessions_file" | "transcript" | "conversation" | "index"
    """
    files = []

    # claude_sessions.json (旧映射文件)
    legacy_sessions = _MAILCODE_HOME / "claude_sessions.json"
    if legacy_sessions.exists():
        files.append((legacy_sessions, "sessions_file"))

    # transcripts/*.json
    legacy_transcripts = _MAILCODE_HOME / "transcripts"
    if legacy_transcripts.is_dir():
        for f in sorted(legacy_transcripts.glob("*.json")):
            files.append((f, "transcript"))

    # conversations/session_*.json
    legacy_conversations = _MAILCODE_HOME / "conversations"
    if legacy_conversations.is_dir():
        for f in sorted(legacy_conversations.glob("session_*.json")):
            files.append((f, "conversation"))

    # conversations/index.json
    legacy_index = legacy_conversations / "index.json"
    if legacy_index.exists():
        files.append((legacy_index, "index"))

    return files


def _compute_target(old_path: Path, file_type: str, agent: str) -> Path:
    """计算迁移目标路径。"""
    agent_home = _MAILCODE_HOME / agent

    if file_type == "sessions_file":
        # claude_sessions.json → <agent>/sessions.json
        return agent_home / "sessions.json"
    elif file_type == "transcript":
        # transcripts/foo.json → <agent>/transcripts/foo.json
        return agent_home / "transcripts" / old_path.name
    elif file_type == "conversation":
        # conversations/session_xxx.json → <agent>/conversations/session_xxx.json
        return agent_home / "conversations" / old_path.name
    elif file_type == "index":
        # conversations/index.json → <agent>/conversations/index.json
        return agent_home / "conversations" / "index.json"
    else:
        raise ValueError(f"未知文件类型: {file_type}")


def migrate_legacy(dry_run: bool = True, agent: str = "claude") -> dict:
    """迁移旧版 agent 数据。

    Args:
        dry_run: True 时不修改磁盘, 仅列出计划
        agent: 目标 agent 名 (默认 claude)

    Returns:
        {"dry_run": bool, "files": [...], "moved": int, "skipped": int}
    """
    files = _scan_legacy_files()

    if not files:
        print("未发现旧版数据文件, 无需迁移。")
        return {"dry_run": dry_run, "files": [], "moved": 0, "skipped": 0}

    # 检测是否已迁移过
    if _MARKER_FILE.exists():
        try:
            marker = json.loads(_MARKER_FILE.read_text(encoding="utf-8"))
            print(f"⚠️  检测到已有迁移记录 ({marker.get('migrated_at', '?')})")
            print(f"   已迁移 {len(marker.get('files', []))} 个文件")
            if not dry_run:
                print("   如需重新迁移, 请先删除: " + str(_MARKER_FILE))
                return {"dry_run": dry_run, "files": [], "moved": 0, "skipped": 0}
        except Exception:
            pass

    # 目标目录
    agent_home = _MAILCODE_HOME / agent
    target_dir = agent_home

    print(f"{'[DRY-RUN] ' if dry_run else ''}迁移旧版数据到 {target_dir}/")
    print()

    plan = []
    moved = 0
    skipped = 0

    for old_path, file_type in files:
        target_path = _compute_target(old_path, file_type, agent)
        if target_path == old_path:
            skipped += 1
            continue

        plan.append({
            "old": str(old_path),
            "new": str(target_path),
            "type": file_type,
        })

        icon = "📁" if file_type in ("transcript", "conversation") else "📄"
        print(f"  {icon} {old_path.name}")
        print(f"     → {target_path}")

    if not plan:
        print("所有文件已在正确位置, 无需迁移。")
        return {"dry_run": dry_run, "files": [], "moved": 0, "skipped": skipped}

    print()
    print(f"共 {len(plan)} 个文件待迁移, {skipped} 个已跳过")

    if dry_run:
        print()
        print("这是 dry-run 模式, 未修改任何文件。")
        print("加 --apply 执行实际迁移。")
        return {"dry_run": True, "files": plan, "moved": 0, "skipped": skipped}

    # ---- apply 模式: 执行迁移 ----
    target_dir.mkdir(parents=True, exist_ok=True)
    if file_type == "transcript" or any(p["type"] == "conversation" for p in plan):
        (target_dir / "transcripts").mkdir(parents=True, exist_ok=True)
        (target_dir / "conversations").mkdir(parents=True, exist_ok=True)

    for item in plan:
        old_path = Path(item["old"])
        new_path = Path(item["new"])

        # 创建目标目录
        new_path.parent.mkdir(parents=True, exist_ok=True)

        if new_path.exists():
            logger.info("目标已存在, 跳过: %s", new_path)
            continue

        try:
            shutil.move(str(old_path), str(new_path))
            moved += 1
            logger.info("已移动: %s → %s", old_path, new_path)
        except Exception as e:
            logger.error("移动失败: %s → %s: %s", old_path, new_path, e)
            continue

        # 在旧路径创建 symlink 指向新路径
        try:
            if old_path.exists() or old_path.is_symlink():
                old_path.unlink()
            old_path.symlink_to(new_path)
            logger.info("已创建 symlink: %s → %s", old_path, new_path)
        except Exception as e:
            logger.warning("创建 symlink 失败 (非致命): %s → %s: %s", old_path, new_path, e)

    # 写 marker 文件
    marker = {
        "migrated_at": datetime.now().isoformat(),
        "agent": agent,
        "files": plan,
        "moved_count": moved,
    }
    _MARKER_FILE.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"✅ 迁移完成: {moved} 个文件已迁移")
    print(f"   marker: {_MARKER_FILE}")

    return {"dry_run": False, "files": plan, "moved": moved, "skipped": skipped}


def cleanup_dangling_symlinks(max_age_days: int = 30) -> int:
    """清理超过 max_age_days 的 dangling symlink。

    如果 marker 文件存在且超过 30 天, 删除旧路径的 symlink。

    Returns:
        删除的 symlink 数量
    """
    if not _MARKER_FILE.exists():
        return 0

    try:
        marker = json.loads(_MARKER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0

    migrated_at = marker.get("migrated_at")
    if not migrated_at:
        return 0

    try:
        ts = datetime.fromisoformat(migrated_at)
        age_days = (datetime.now() - ts).days
    except Exception:
        return 0

    if age_days < max_age_days:
        return 0

    removed = 0
    for item in marker.get("files", []):
        old_path = Path(item["old"])
        if old_path.is_symlink():
            try:
                old_path.unlink()
                removed += 1
                logger.info("已清理 dangling symlink: %s", old_path)
            except Exception as e:
                logger.warning("清理 symlink 失败: %s: %s", old_path, e)

    if removed > 0:
        # 删除 marker 文件本身
        try:
            _MARKER_FILE.unlink()
        except Exception:
            pass

    return removed
