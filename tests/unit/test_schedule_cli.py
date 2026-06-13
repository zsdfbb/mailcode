"""schedule_cli 单元测试 —— 覆盖 8 个 cmd_schedule_* 函数 (含 validate)。

与 session_cli 测试保持同一风格:
- 错误信息 → stderr + sys.exit(1)  →  用 capsys + pytest.raises(SystemExit) 验证
- 成功输出 → stdout, f-string 表格 / 详情
- ScheduleStore 用临时 schedules.json 隔离
- input() 用 monkeypatch.setattr('builtins.input', ...) 替换
"""

from unittest.mock import MagicMock, patch

import pytest

from mailcode.relay import scheduler as sched_module
from mailcode.relay.scheduler import (
    SCHEDULE_DAILY,
    SCHEDULE_INTERVAL,
    ScheduleSpec,
    ScheduleStore,
    Task,
)
from mailcode.schedule_cli import (
    cmd_schedule_add,
    cmd_schedule_delete,
    cmd_schedule_disable,
    cmd_schedule_enable,
    cmd_schedule_list,
    cmd_schedule_run_now,
    cmd_schedule_show,
    cmd_schedule_validate,
)


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def mock_email_channel():
    """Mock EmailChannel: send_reply 返回成功。"""
    channel = MagicMock()
    channel.send_reply.return_value = (True, "<reply-xyz@mailcode>")
    return channel


@pytest.fixture
def store(schedules_path):
    """临时 ScheduleStore: 路径隔离, 单测完即丢弃。"""
    with patch.object(sched_module, "_SCHEDULES_PATH", schedules_path):
        s = ScheduleStore(schedules_path)
        yield s


def _make_task(
    name: str = "demo",
    enabled: bool = True,
    to_email: str = "user@test.com",
    prompt: str = "test prompt",
    schedule: ScheduleSpec = None,
) -> Task:
    """工厂: 构造一个最小可用的 Task。"""
    if schedule is None:
        schedule = ScheduleSpec(type=SCHEDULE_INTERVAL, interval_seconds=3600)
    return Task(
        id=sched_module._new_task_id(),
        name=name,
        enabled=enabled,
        schedule=schedule,
        prompt=prompt,
        cwd="/tmp",
        to_email=to_email,
        next_run_at=None,
    )


# ------------------------------------------------------------------ #
# list
# ------------------------------------------------------------------ #


def test_cmd_schedule_list_empty(store, capsys):
    """空 store → print '暂无定时任务', exit 0 (无 sys.exit 调用)。"""
    cmd_schedule_list(store)
    out = capsys.readouterr().out
    assert "暂无定时任务" in out


def test_cmd_schedule_list_with_tasks(store, capsys):
    """2 个 task → 表格输出含 name/type/schedule, 行数 ≥ 2。"""
    store.add(_make_task(name="alpha"))
    store.add(_make_task(name="beta"))

    cmd_schedule_list(store)
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out
    # 表头含 NAME / TYPE / SCHEDULE
    assert "NAME" in out
    assert "TYPE" in out
    assert "SCHEDULE" in out


def test_cmd_schedule_list_off_prefix(store, capsys):
    """enabled=False 的 task → 行首有 '[OFF]'。"""
    store.add(_make_task(name="disabled-task", enabled=False))

    cmd_schedule_list(store)
    out = capsys.readouterr().out
    assert "[OFF]" in out
    assert "disabled-task" in out


# ------------------------------------------------------------------ #
# show
# ------------------------------------------------------------------ #


def test_cmd_schedule_show_existing(store, capsys):
    """add 后 show → 输出 name/id/enabled/schedule 字段。"""
    store.add(_make_task(name="show-me"))

    cmd_schedule_show(store, "show-me")
    out = capsys.readouterr().out
    assert "Name:" in out
    assert "show-me" in out
    assert "ID:" in out
    assert "Enabled:" in out
    assert "Schedule str:" in out
    # id 以 sched_ 开头
    assert "sched_" in out


def test_cmd_schedule_show_not_found(store, capsys):
    """不存在的 name → stderr + exit(1)。"""
    with pytest.raises(SystemExit) as exc:
        cmd_schedule_show(store, "nope")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "未找到" in err
    assert "nope" in err


# ------------------------------------------------------------------ #
# add
# ------------------------------------------------------------------ #


def test_cmd_schedule_add_success(store, capsys):
    """传完整参数 → store 有 1 个 task, id 以 sched_ 开头。"""
    cmd_schedule_add(
        store,
        name="daily-9",
        schedule_type=SCHEDULE_DAILY,
        time="09:00",
        prompt="run me",
        to_email="user@test.com",
        interactive=False,
    )
    out = capsys.readouterr().out
    assert "已添加" in out
    assert "daily-9" in out

    # store 中确实有这条
    t = store.get("daily-9")
    assert t is not None
    assert t.id.startswith("sched_")
    assert t.schedule.type == SCHEDULE_DAILY
    assert t.schedule.time == "09:00"


def test_cmd_schedule_add_duplicate_name(store, capsys):
    """重复 add 同 name → stderr + exit(1)。"""
    store.add(_make_task(name="dup"))

    with pytest.raises(SystemExit) as exc:
        cmd_schedule_add(
            store,
            name="dup",
            schedule_type=SCHEDULE_INTERVAL,
            interval_seconds=60,
            prompt="x",
            to_email="u@t.com",
            interactive=False,
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "已存在" in err


def test_cmd_schedule_add_invalid_email(store, capsys):
    """to_email 不含 @ → stderr + exit(1)。"""
    with pytest.raises(SystemExit) as exc:
        cmd_schedule_add(
            store,
            name="bad-email",
            schedule_type=SCHEDULE_INTERVAL,
            interval_seconds=60,
            prompt="x",
            to_email="not-an-email",
            interactive=False,
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "@" in err
    assert "to_email" in err


def test_cmd_schedule_add_missing_schedule_field(store, capsys):
    """daily 缺 time → stderr + exit(1) (parse_schedule 抛 ValueError)。"""
    with pytest.raises(SystemExit) as exc:
        cmd_schedule_add(
            store,
            name="missing-time",
            schedule_type=SCHEDULE_DAILY,
            # 故意不传 time
            prompt="x",
            to_email="u@t.com",
            interactive=False,
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "schedule" in err.lower() or "time" in err.lower()


# ------------------------------------------------------------------ #
# enable / disable
# ------------------------------------------------------------------ #


def test_cmd_schedule_enable_disable(store, capsys):
    """切换 enabled 状态, store.get 返回正确 enabled 值。"""
    store.add(_make_task(name="toggle", enabled=False))

    # enable
    cmd_schedule_enable(store, "toggle")
    out = capsys.readouterr().out
    assert "已启用" in out
    assert store.get("toggle").enabled is True

    # disable
    cmd_schedule_disable(store, "toggle")
    out = capsys.readouterr().out
    assert "已停用" in out
    assert store.get("toggle").enabled is False


def test_cmd_schedule_enable_not_found(store, capsys):
    """未找到 → stderr + exit(1)。"""
    with pytest.raises(SystemExit) as exc:
        cmd_schedule_enable(store, "ghost")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "未找到" in err


# ------------------------------------------------------------------ #
# delete
# ------------------------------------------------------------------ #


def test_cmd_schedule_delete_confirm_yes(store, capsys, monkeypatch):
    """monkeypatch input 返回 'y' → store 中 task 被删。"""
    store.add(_make_task(name="del-yes"))
    monkeypatch.setattr("builtins.input", lambda _: "y")

    cmd_schedule_delete(store, "del-yes", assume_yes=False)
    out = capsys.readouterr().out
    assert "已删除" in out
    assert store.get("del-yes") is None


def test_cmd_schedule_delete_confirm_no(store, capsys, monkeypatch):
    """monkeypatch input 返回 'n' → store 中 task 仍在。"""
    store.add(_make_task(name="del-no"))
    monkeypatch.setattr("builtins.input", lambda _: "n")

    cmd_schedule_delete(store, "del-no", assume_yes=False)
    out = capsys.readouterr().out
    assert "已取消" in out
    assert store.get("del-no") is not None


def test_cmd_schedule_delete_with_yes_flag(store, capsys, monkeypatch):
    """assume_yes=True 跳过 input, 直接删。"""
    store.add(_make_task(name="del-flag"))

    # 若不跳过 input, 此处 input 没被 monkeypatch, 会阻塞 → 测的就是不会阻塞
    cmd_schedule_delete(store, "del-flag", assume_yes=True)
    out = capsys.readouterr().out
    assert "已删除" in out
    assert store.get("del-flag") is None


# ------------------------------------------------------------------ #
# run-now
# ------------------------------------------------------------------ #


def test_cmd_schedule_run_now_success(store, capsys, mock_email_channel):
    """mock call_claude + email_channel → print 含 [claude]/[email]/完成, call 1 次。"""
    store.add(_make_task(name="run-me"))

    cmd_schedule_run_now(
        store,
        "run-me",
        email_channel=mock_email_channel,
        call_claude_fn=lambda prompt, cwd: "claude says hi",
    )

    out = capsys.readouterr().out
    assert "[claude]" in out
    assert "[email]" in out
    assert "完成" in out
    # 调了 1 次
    assert mock_email_channel.send_reply.call_count == 1


def test_cmd_schedule_run_now_not_found(store, capsys):
    """未找到 → exit(1)。"""
    with pytest.raises(SystemExit) as exc:
        cmd_schedule_run_now(
            store,
            "ghost",
            email_channel=None,
            call_claude_fn=lambda p, c: "x",
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "未找到" in err


def test_cmd_schedule_run_now_no_email_channel(store, capsys):
    """不传 email_channel → 仍调 call_claude 但不调 send_reply。"""
    store.add(_make_task(name="run-no-email"))
    fake_claude = MagicMock(return_value="hello from claude")

    cmd_schedule_run_now(
        store,
        "run-no-email",
        email_channel=None,
        call_claude_fn=fake_claude,
    )

    out = capsys.readouterr().out
    # call_claude 被调 1 次
    assert fake_claude.call_count == 1
    # 输出含 skip 提示
    assert "skip" in out.lower()
    assert "[email]" in out


# ------------------------------------------------------------------ #
# validate
# ------------------------------------------------------------------ #


def test_cmd_schedule_validate_clean(store, capsys):
    """合法 task → print ✅, 汇总全 ok。"""
    store.add(_make_task(name="good-1"))
    store.add(_make_task(name="good-2"))

    cmd_schedule_validate(store)
    out = capsys.readouterr().out
    assert "✅" in out
    assert "good-1" in out
    assert "good-2" in out
    assert "汇总" in out
    assert "2 ok" in out


def test_cmd_schedule_validate_bad_email(store, capsys):
    """含 @ 错误的 task → print ❌, exit(1)。"""
    store.add(_make_task(name="bad", to_email="not-an-email"))

    with pytest.raises(SystemExit) as exc:
        cmd_schedule_validate(store)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "❌" in out
    assert "bad" in out
    assert "汇总" in out
    assert "1 fail" in out


def test_cmd_schedule_validate_empty(store, capsys):
    """空 store → '暂无定时任务 (无需校验)', exit 0。"""
    cmd_schedule_validate(store)
    out = capsys.readouterr().out
    assert "暂无定时任务" in out
    assert "无需校验" in out
