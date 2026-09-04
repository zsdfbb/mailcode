"""迁移工具单元测试"""

import json

import pytest

from mailcode.utils.migrate import (
    _compute_target,
    _scan_legacy_files,
    cleanup_dangling_symlinks,
    migrate_legacy,
)


@pytest.fixture
def mock_mailcode_home(tmp_path, monkeypatch):
    """临时 _MAILCODE_HOME 目录, 隔离测试。"""
    monkeypatch.setattr(
        "mailcode.utils.migrate._MAILCODE_HOME", tmp_path
    )
    monkeypatch.setattr(
        "mailcode.utils.migrate._MARKER_FILE", tmp_path / "legacy-claude-migration.json"
    )
    return tmp_path


class TestScanLegacyFiles:
    """_scan_legacy_files 扫描旧版文件。"""

    def test_no_files(self, mock_mailcode_home):
        files = _scan_legacy_files()
        assert files == []

    def test_finds_sessions_file(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        files = _scan_legacy_files()
        assert len(files) == 1
        assert files[0][1] == "sessions_file"

    def test_finds_transcripts(self, mock_mailcode_home):
        tdir = mock_mailcode_home / "transcripts"
        tdir.mkdir()
        (tdir / "t1.json").write_text("{}")
        (tdir / "t2.json").write_text("{}")
        files = _scan_legacy_files()
        types = [f[1] for f in files]
        assert types.count("transcript") == 2

    def test_finds_conversations(self, mock_mailcode_home):
        cdir = mock_mailcode_home / "conversations"
        cdir.mkdir()
        (cdir / "session_abc.json").write_text("{}")
        (cdir / "session_def.json").write_text("{}")
        files = _scan_legacy_files()
        types = [f[1] for f in files]
        assert types.count("conversation") == 2

    def test_finds_index(self, mock_mailcode_home):
        cdir = mock_mailcode_home / "conversations"
        cdir.mkdir()
        (cdir / "index.json").write_text("{}")
        files = _scan_legacy_files()
        types = [f[1] for f in files]
        assert types.count("index") == 1


class TestComputeTarget:
    """_compute_target 计算迁移目标路径。"""

    def test_sessions_file(self, mock_mailcode_home):
        old = mock_mailcode_home / "claude_sessions.json"
        target = _compute_target(old, "sessions_file", "claude")
        assert target == mock_mailcode_home / "claude" / "sessions.json"

    def test_transcript(self, mock_mailcode_home):
        old = mock_mailcode_home / "transcripts" / "t1.json"
        target = _compute_target(old, "transcript", "claude")
        assert target == mock_mailcode_home / "claude" / "transcripts" / "t1.json"

    def test_conversation(self, mock_mailcode_home):
        old = mock_mailcode_home / "conversations" / "session_abc.json"
        target = _compute_target(old, "conversation", "claude")
        assert target == mock_mailcode_home / "claude" / "conversations" / "session_abc.json"

    def test_index(self, mock_mailcode_home):
        old = mock_mailcode_home / "conversations" / "index.json"
        target = _compute_target(old, "index", "claude")
        assert target == mock_mailcode_home / "claude" / "conversations" / "index.json"


class TestMigrateLegacyDryRun:
    """dry_run 模式不改文件。"""

    def test_dry_run_no_files(self, mock_mailcode_home):
        result = migrate_legacy(dry_run=True)
        assert result["moved"] == 0
        assert result["dry_run"] is True

    def test_dry_run_with_files(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        result = migrate_legacy(dry_run=True)
        assert result["moved"] == 0
        assert result["dry_run"] is True
        # 原文件还在
        assert sessions.exists()
        # 新文件不存在
        assert not (mock_mailcode_home / "claude" / "sessions.json").exists()

    def test_dry_run_does_not_create_marker(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        migrate_legacy(dry_run=True)
        marker = mock_mailcode_home / "legacy-claude-migration.json"
        assert not marker.exists()


class TestMigrateLegacyApply:
    """apply 模式移动文件并创建 symlink。"""

    def test_apply_moves_sessions_file(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text('{"threads": {"t1": {}}}')
        result = migrate_legacy(dry_run=False, agent="claude")
        assert result["moved"] == 1

        target = mock_mailcode_home / "claude" / "sessions.json"
        assert target.exists()
        assert json.loads(target.read_text()) == {"threads": {"t1": {}}}

    def test_apply_creates_symlink(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        migrate_legacy(dry_run=False, agent="claude")
        assert sessions.is_symlink()
        assert sessions.read_text() == "{}"

    def test_apply_creates_marker(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        migrate_legacy(dry_run=False, agent="claude")
        marker = mock_mailcode_home / "legacy-claude-migration.json"
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["agent"] == "claude"
        assert data["moved_count"] == 1
        assert len(data["files"]) == 1

    def test_apply_moves_transcripts(self, mock_mailcode_home):
        tdir = mock_mailcode_home / "transcripts"
        tdir.mkdir()
        (tdir / "t1.json").write_text('{"id": "t1"}')
        migrate_legacy(dry_run=False, agent="claude")
        target = mock_mailcode_home / "claude" / "transcripts" / "t1.json"
        assert target.exists()
        assert json.loads(target.read_text()) == {"id": "t1"}

    def test_apply_moves_conversations(self, mock_mailcode_home):
        cdir = mock_mailcode_home / "conversations"
        cdir.mkdir()
        (cdir / "session_abc.json").write_text('{"id": "abc"}')
        migrate_legacy(dry_run=False, agent="claude")
        target = mock_mailcode_home / "claude" / "conversations" / "session_abc.json"
        assert target.exists()

    def test_apply_moves_index(self, mock_mailcode_home):
        cdir = mock_mailcode_home / "conversations"
        cdir.mkdir()
        (cdir / "index.json").write_text('{"threads": {}}')
        migrate_legacy(dry_run=False, agent="claude")
        target = mock_mailcode_home / "claude" / "conversations" / "index.json"
        assert target.exists()

    def test_apply_skips_existing_target(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text('{"old": true}')
        target_dir = mock_mailcode_home / "claude"
        target_dir.mkdir(parents=True)
        (target_dir / "sessions.json").write_text('{"existing": true}')
        migrate_legacy(dry_run=False, agent="claude")
        # target already exists, skip
        assert json.loads((target_dir / "sessions.json").read_text()) == {"existing": True}


class TestCleanupDanglingSymlinks:
    """30 天过期检测。"""

    def test_no_marker_returns_zero(self, mock_mailcode_home):
        removed = cleanup_dangling_symlinks()
        assert removed == 0

    def test_recent_marker_not_cleaned(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        migrate_legacy(dry_run=False, agent="claude")
        # marker is fresh (0 days old), should not clean
        removed = cleanup_dangling_symlinks(max_age_days=30)
        assert removed == 0
        assert sessions.is_symlink()

    def test_old_marker_cleaned(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        migrate_legacy(dry_run=False, agent="claude")

        # Fake old marker
        marker = mock_mailcode_home / "legacy-claude-migration.json"
        data = json.loads(marker.read_text())
        data["migrated_at"] = "2020-01-01T00:00:00"
        marker.write_text(json.dumps(data))

        removed = cleanup_dangling_symlinks(max_age_days=30)
        assert removed == 1
        assert not sessions.exists()
        assert not marker.exists()

    def test_non_symlink_not_removed(self, mock_mailcode_home):
        """如果旧路径不是 symlink (例如手动恢复了文件), 不删除。"""
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text('{"real": true}')
        # Create marker without actual migration
        marker = mock_mailcode_home / "legacy-claude-migration.json"
        marker.write_text(json.dumps({
            "migrated_at": "2020-01-01T00:00:00",
            "files": [{"old": str(sessions), "new": "x", "type": "sessions_file"}],
        }))
        removed = cleanup_dangling_symlinks(max_age_days=30)
        assert removed == 0
        assert sessions.exists()  # real file, not touched


class TestMarkerPreventsReMigration:
    """已迁移过时, apply 模式拒绝重复迁移。"""

    def test_apply_refuses_after_marker(self, mock_mailcode_home):
        sessions = mock_mailcode_home / "claude_sessions.json"
        sessions.write_text("{}")
        migrate_legacy(dry_run=False, agent="claude")

        # Create another legacy file
        sessions2 = mock_mailcode_home / "claude_sessions.json.bak"
        sessions2.write_text('{"backup": true}')

        # The marker blocks re-migration
        result = migrate_legacy(dry_run=False, agent="claude")
        assert result["moved"] == 0
