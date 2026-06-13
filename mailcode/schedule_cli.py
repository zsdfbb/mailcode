"""mailcode schedule 子命令的 CLI 格式化与呈现。

提供 8 个 cmd_schedule_* 函数, 风格对齐 session_cli.py:
- 错误信息 → stderr + sys.exit(1)
- 用户取消 → print + return
- 表格 / 详情 → print() 到 stdout, f-string 固定列宽
"""

import datetime
import sys
from typing import Optional

from mailcode.relay.scheduler import (
    SCHEDULE_DAILY,
    SCHEDULE_INTERVAL,
    SCHEDULE_MONTHLY,
    SCHEDULE_WEEKLY,
    ScheduleStore,
    Task,
    compute_next_run,
    parse_schedule,
)


# ------------------------------------------------------------------ #
# helper
# ------------------------------------------------------------------ #


def _format_dt(dt) -> str:
    """datetime → 'YYYY-MM-DD HH:MM:SS'。None 透传 '-'。"""
    if dt is None:
        return "-"
    if isinstance(dt, str):
        # ISO8601 字符串, 尝试解析
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except (ValueError, TypeError):
            return dt
    if not isinstance(dt, datetime.datetime):
        return str(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_dt_or_dash(dt) -> str:
    """同 _format_dt, None 一律 '-'。"""
    return _format_dt(dt)


def _format_schedule_str(task: Task) -> str:
    """把 task.schedule 转成可读字符串, 用于表格列展示。

    - interval: "3600s"
    - daily: "09:00"
    - weekly: "mon 10:00" (0=今天, 6=6 天后 — 用相对偏移标签)
    - monthly: "day=1 08:00"
    """
    s = task.schedule
    if s.type == SCHEDULE_INTERVAL:
        return f"{s.interval_seconds or 0}s"
    if s.type == SCHEDULE_DAILY:
        return s.time or "-"
    if s.type == SCHEDULE_WEEKLY:
        # day_of_week: 0=mon, 1=tue, ..., 6=sun
        DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        dow = s.day_of_week if s.day_of_week is not None else 0
        tag = DAY_NAMES[dow] if 0 <= dow < len(DAY_NAMES) else f"dow={dow}"
        return f"{tag} {s.time or '-'}"
    if s.type == SCHEDULE_MONTHLY:
        return f"day={s.day_of_month} {s.time or '-'}"
    return s.type


def shorten(text: str, width: int) -> str:
    """按显示宽度截断。仿 session_cli.shorten。"""
    if text is None:
        return ""
    text = str(text).replace("\n", " ").replace("\r", " ").strip()
    if width <= 0:
        return text
    if len(text) <= width:
        return text
    return text[: max(width - 1, 1)] + "…"


def _prompt_required(label: str) -> Optional[str]:
    """交互式补全必填字段。EOFError 容错 → 返回 None。"""
    try:
        val = input(f"{label}: ").strip()
    except EOFError:
        return None
    return val or None


# ------------------------------------------------------------------ #
# list / show
# ------------------------------------------------------------------ #


def cmd_schedule_list(store: ScheduleStore):
    """列出所有定时任务, 按 next_run_at 升序。"""
    tasks = store.list_tasks()
    if not tasks:
        print("暂无定时任务")
        return

    def _sort_key(t: Task):
        # None 排到末尾
        if not t.next_run_at:
            return (1, "")
        return (0, t.next_run_at)

    tasks.sort(key=_sort_key)

    header = (
        f"{'NAME':<20}  {'TYPE':<9}  {'SCHEDULE':<18}  "
        f"{'LAST RUN':<19}  {'NEXT RUN':<19}  STATUS"
    )
    print(header)
    print("-" * len(header))
    for t in tasks:
        prefix = "" if t.enabled else "[OFF] "
        name_col = (prefix + shorten(t.name, 18)).ljust(20)
        type_col = shorten(t.schedule.type, 9).ljust(9)
        sched_col = shorten(_format_schedule_str(t), 18).ljust(18)
        last_run = _format_dt_or_dash(t.last_run_at)
        next_run = _format_dt_or_dash(t.next_run_at)
        status = t.last_status or "-"
        print(
            f"{name_col}  {type_col}  {sched_col}  "
            f"{last_run:<19}  {next_run:<19}  {status}"
        )


def cmd_schedule_show(store: ScheduleStore, name: str):
    """查看单个定时任务详情。"""
    task = store.get(name)
    if task is None:
        print(f"未找到定时任务: {name}", file=sys.stderr)
        sys.exit(1)

    print(f"Name:           {task.name}")
    print(f"ID:             {task.id}")
    print(f"Enabled:        {'yes' if task.enabled else 'no'}")
    print(f"Type:           {task.schedule.type}")
    s = task.schedule
    if s.type == SCHEDULE_INTERVAL:
        print(f"Interval (s):   {s.interval_seconds}")
    if s.time:
        print(f"Time:           {s.time}")
    if s.day_of_week is not None:
        DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        name = DAY_NAMES[s.day_of_week] if 0 <= s.day_of_week < 7 else f"day_of_week={s.day_of_week}"
        print(f"Day of week:    {name} ({s.day_of_week})")
    if s.day_of_month is not None:
        print(f"Day of month:   {s.day_of_month}")
    print(f"Schedule str:   {_format_schedule_str(task)}")
    print(f"Cwd:            {task.cwd or '-'}")
    print(f"To email:       {task.to_email}")
    print(f"Subject prefix: {task.subject_prefix}")
    print(f"Prompt:         {shorten(task.prompt, 200)}")
    print()
    print(f"Last Run:       {_format_dt_or_dash(task.last_run_at)}")
    print(f"Last Status:    {task.last_status or '-'}")
    print(f"Last Error:     {task.last_error or '-'}")
    print(f"Next Run:       {_format_dt_or_dash(task.next_run_at)}")
    print(f"Created:        {_format_dt_or_dash(task.created_at)}")
    print(f"Updated:        {_format_dt_or_dash(task.updated_at)}")


# ------------------------------------------------------------------ #
# add
# ------------------------------------------------------------------ #


def cmd_schedule_add(
    store: ScheduleStore,
    name: str,
    schedule_type: str,
    *,
    interval_seconds: Optional[int] = None,
    time: Optional[str] = None,
    day_of_week: Optional[int] = None,
    day_of_month: Optional[int] = None,
    prompt: Optional[str] = None,
    to_email: Optional[str] = None,
    cwd: Optional[str] = None,
    subject_prefix: Optional[str] = None,
    interactive: bool = True,
):
    """添加一个新定时任务。

    - 校验 name 唯一 / schedule 完整 (按 type) / email 含 @
    - 生成 sched_<8hex> id, 写文件
    - interactive=True 时缺必填走 input() 补全 (EOFError 容错)
    """
    # ---- 校验 name ----
    if not name or not name.strip():
        print("task.name 不能为空", file=sys.stderr)
        sys.exit(1)
    if store.get(name) is not None:
        print(f"task.name 已存在: {name!r}", file=sys.stderr)
        sys.exit(1)

    # ---- 交互补全 (按需) ----
    if interactive:
        if not prompt:
            prompt = _prompt_required("Prompt")
        if not to_email:
            to_email = _prompt_required("To email")
        if schedule_type == SCHEDULE_INTERVAL and interval_seconds is None:
            val = _prompt_required("Interval seconds")
            if val:
                try:
                    interval_seconds = int(val)
                except ValueError:
                    print(f"interval_seconds 不是整数: {val!r}", file=sys.stderr)
                    sys.exit(1)
        if schedule_type in (SCHEDULE_DAILY, SCHEDULE_WEEKLY, SCHEDULE_MONTHLY) and not time:
            time = _prompt_required("Time (HH:MM)")
        if schedule_type == SCHEDULE_WEEKLY and day_of_week is None:
            val = _prompt_required("Day of week (0=今天, 6=6天后)")
            if val:
                try:
                    day_of_week = int(val)
                except ValueError:
                    print(f"day_of_week 不是整数: {val!r}", file=sys.stderr)
                    sys.exit(1)
        if schedule_type == SCHEDULE_MONTHLY and day_of_month is None:
            val = _prompt_required("Day of month (1-31)")
            if val:
                try:
                    day_of_month = int(val)
                except ValueError:
                    print(f"day_of_month 不是整数: {val!r}", file=sys.stderr)
                    sys.exit(1)

    # ---- 校验必填 ----
    if not prompt:
        print("prompt 不能为空", file=sys.stderr)
        sys.exit(1)
    if not to_email or "@" not in to_email:
        print(f"to_email 非法 (必须含 @): {to_email!r}", file=sys.stderr)
        sys.exit(1)

    # ---- 解析 schedule ----
    spec_dict: dict = {"type": schedule_type}
    if interval_seconds is not None:
        spec_dict["interval_seconds"] = interval_seconds
    if time is not None:
        spec_dict["time"] = time
    if day_of_week is not None:
        spec_dict["day_of_week"] = day_of_week
    if day_of_month is not None:
        spec_dict["day_of_month"] = day_of_month

    try:
        spec = parse_schedule(spec_dict)
    except ValueError as e:
        print(f"schedule 校验失败: {e}", file=sys.stderr)
        sys.exit(1)

    # ---- 计算 next_run_at ----
    try:
        next_dt = compute_next_run(spec, datetime.datetime.now())
        next_iso = next_dt.astimezone().isoformat()
    except Exception as e:
        print(f"计算 next_run_at 失败: {e}", file=sys.stderr)
        sys.exit(1)

    # ---- 生成 id, 写入 ----
    from mailcode.relay.scheduler import _new_task_id  # type: ignore
    task = Task(
        id=_new_task_id(),
        name=name,
        enabled=True,
        schedule=spec,
        prompt=prompt,
        cwd=cwd or "",
        to_email=to_email,
        subject_prefix=subject_prefix or "Schedule:",
        next_run_at=next_iso,
    )

    try:
        store.add(task)
    except ValueError as e:
        print(f"保存失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"已添加: {task.name} (id={task.id})")


# ------------------------------------------------------------------ #
# enable / disable / delete
# ------------------------------------------------------------------ #


def cmd_schedule_enable(store: ScheduleStore, name: str):
    """启用一个定时任务。"""
    task = store.set_enabled(name, True)
    if task is None:
        print(f"未找到定时任务: {name}", file=sys.stderr)
        sys.exit(1)
    print(f"已启用: {task.name}")


def cmd_schedule_disable(store: ScheduleStore, name: str):
    """停用一个定时任务。"""
    task = store.set_enabled(name, False)
    if task is None:
        print(f"未找到定时任务: {name}", file=sys.stderr)
        sys.exit(1)
    print(f"已停用: {task.name}")


def cmd_schedule_delete(store: ScheduleStore, name: str, assume_yes: bool = False):
    """删除一个定时任务, 含 y/N 确认。"""
    task = store.get(name)
    if task is None:
        print(f"未找到定时任务: {name}", file=sys.stderr)
        sys.exit(1)

    if not assume_yes:
        print(f"即将删除定时任务: {task.name}")
        print(f"  ID:       {task.id}")
        print(f"  Type:     {task.schedule.type}")
        print(f"  Schedule: {_format_schedule_str(task)}")
        print(f"  To email: {task.to_email}")
        try:
            confirm = input("确认删除? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("已取消")
            return

    ok = store.delete(name)
    if ok:
        print(f"已删除定时任务: {task.name}")
    else:
        print(f"删除失败: {task.name}", file=sys.stderr)
        sys.exit(1)


# ------------------------------------------------------------------ #
# run-now — 同步阻塞执行
# ------------------------------------------------------------------ #


def cmd_schedule_run_now(
    store: ScheduleStore,
    name: str,
    email_channel=None,
    call_claude_fn=None,
):
    """立即触发一个任务, 同步等待结果。

    - call_claude_fn: 默认从 mailcode.utils.claude_runner 导入 call_claude
    - email_channel: 默认 None, 此时只跑 Claude 不发邮件
    - 不写 store.last_* (run-now 不算计划触发, 避免污染统计)
    """
    task = store.get(name)
    if task is None:
        print(f"未找到定时任务: {name}", file=sys.stderr)
        sys.exit(1)

    if call_claude_fn is None:
        from mailcode.utils.claude_runner import call_claude as call_claude_fn  # noqa: F811

    print(f"[run-now] task={task.name} (id={task.id})")
    print(f"[claude]   cwd={task.cwd or '(default)'}")
    try:
        response = call_claude_fn(task.prompt, task.cwd)
    except Exception as e:
        print(f"[claude]   失败: {e}", file=sys.stderr)
        sys.exit(1)

    if response is None:
        print("[claude]   失败: 返回 None", file=sys.stderr)
        sys.exit(1)
    print(f"[claude]   ok ({len(response)} chars)")

    if email_channel is not None:
        subject = f"{task.subject_prefix} {task.name}"
        print(f"[email]    to={task.to_email} subject={shorten(subject, 60)}")
        try:
            ok, _msg_id = email_channel.send_reply(
                to_email=task.to_email,
                subject=subject,
                body=response,
            )
        except Exception as e:
            print(f"[email]    失败: {e}", file=sys.stderr)
            sys.exit(1)
        if not ok:
            print("[email]    失败: send_reply 返回 False", file=sys.stderr)
            sys.exit(1)
        print("[email]    ok")
    else:
        print("[email]    skip (未提供 email_channel)")

    print("[status]   success")
    print("完成")


# ------------------------------------------------------------------ #
# validate — 不改文件, 只校验
# ------------------------------------------------------------------ #


def cmd_schedule_validate(store: ScheduleStore):
    """校验 schedules.json 中所有任务。

    逐条校验: name 唯一 / schedule 完整 / to_email 含 @ / prompt 非空。
    不修改文件, 只打印 ✅ / ❌ 汇总。
    """
    tasks = store.list_tasks()
    if not tasks:
        print("暂无定时任务 (无需校验)")
        return

    ok_count = 0
    fail_count = 0
    seen_names: dict[str, str] = {}

    for t in tasks:
        issues: list[str] = []

        # name 非空
        if not t.name or not t.name.strip():
            issues.append("name 为空")

        # name 唯一 (不区分大小写)
        key = (t.name or "").strip().lower()
        if key:
            if key in seen_names:
                issues.append(
                    f"name 与 {seen_names[key]} 重复"
                )
            else:
                seen_names[key] = t.id

        # schedule 完整
        try:
            spec_dict = t.schedule.to_dict()
            parse_schedule(spec_dict)
        except Exception as e:
            issues.append(f"schedule 不合法: {e}")

        # to_email 含 @
        if not t.to_email or "@" not in t.to_email:
            issues.append(f"to_email 非法: {t.to_email!r}")

        # prompt 非空
        if not t.prompt or not t.prompt.strip():
            issues.append("prompt 为空")

        if issues:
            fail_count += 1
            print(f"❌ {t.name} (id={t.id})")
            for it in issues:
                print(f"     - {it}")
        else:
            ok_count += 1
            print(f"✅ {t.name} (id={t.id})")

    print()
    print(f"汇总: {ok_count} ok, {fail_count} fail (共 {len(tasks)})")
    if fail_count > 0:
        sys.exit(1)