"""第五篇：每周五的周末准备提醒，固定上海时区。默认关闭；CLI 不发送消息。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from assistant import ROOT, Store, make_client, run_agent

LOG = logging.getLogger("xiaodazi.reminders")
ZONE_NAME = "Asia/Shanghai"
GRACE = timedelta(minutes=15)


def utc_now():
    return datetime.now(timezone.utc)


def local_time(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("时间必须携带时区")
    return now.astimezone(ZoneInfo(ZONE_NAME))


def check_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("请使用 HH:MM，例如 20:00")
    if not "08:00" <= value <= "22:59":
        raise ValueError("教学版提醒时间限于 08:00–22:59，23:00–08:00 为免打扰时间")
    return value


class ReminderStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS settings (
                owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                hhmm TEXT NOT NULL DEFAULT '20:00', mode TEXT NOT NULL DEFAULT 'fixed',
                revision INTEGER NOT NULL DEFAULT 0, active_from TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                owner TEXT NOT NULL, day TEXT NOT NULL, due_at TEXT NOT NULL,
                revision INTEGER NOT NULL, status TEXT NOT NULL,
                content TEXT, error TEXT, PRIMARY KEY(owner, day))""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self, owner):
        with self.connect() as db:
            row = db.execute("SELECT * FROM settings WHERE owner=?", (owner,)).fetchone()
        return dict(row) if row else {"owner": owner, "enabled": 0, "hhmm": "20:00",
                                     "mode": "fixed", "revision": 0, "active_from": ""}

    def configure(self, owner, now, *, enabled=None, hhmm=None, mode=None):
        local_time(now)
        if not owner:
            raise ValueError("必须指定本应用中自己的 open_id")
        if hhmm is not None:
            check_time(hhmm)
        if mode is not None and mode not in ("fixed", "ai"):
            raise ValueError("mode 只支持 fixed / ai")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO settings(owner, active_from) VALUES (?, ?)", (owner, now.isoformat()))
            old = db.execute("SELECT * FROM settings WHERE owner=?", (owner,)).fetchone()
            new_enabled = int(enabled) if enabled is not None else old["enabled"]
            new_time, new_mode = hhmm or old["hhmm"], mode or old["mode"]
            if (new_enabled, new_time, new_mode) != (old["enabled"], old["hhmm"], old["mode"]):
                db.execute("""UPDATE settings SET enabled=?, hhmm=?, mode=?, revision=revision+1,
                              active_from=? WHERE owner=?""",
                           (new_enabled, new_time, new_mode, now.isoformat(), owner))
        return self.settings(owner)

    def claim(self, owner, now):
        current = local_time(now)
        if current.weekday() != 4:
            return None  # 只在上海时间周五检查，不在周末或下周补发。
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM settings WHERE owner=?", (owner,)).fetchone()
            if not row or not row["enabled"]:
                return None
            hour, minute = map(int, row["hhmm"].split(":"))
            due = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < due or datetime.fromisoformat(row["active_from"]) > due:
                return None
            # 当天最多领取一次；离线很久时不循环补发历史日期。
            day = current.date().isoformat()
            if db.execute("SELECT 1 FROM deliveries WHERE owner=? AND day=?", (owner, day)).fetchone():
                return None
            missed = now > due + GRACE or current.hour >= 23 or current.hour < 8
            status = "missed" if missed else "running"
            db.execute("INSERT INTO deliveries VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                       (owner, day, due.isoformat(), row["revision"], status))
            if missed:
                return None
            return {**dict(row), "day": day, "due_at": due.isoformat()}

    def before_send(self, job, content, now):
        current = local_time(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            setting = db.execute("SELECT * FROM settings WHERE owner=?", (job["owner"],)).fetchone()
            record = db.execute("SELECT status FROM deliveries WHERE owner=? AND day=?", (job["owner"], job["day"])).fetchone()
            if not record or record["status"] != "running":
                return False
            due = datetime.fromisoformat(job["due_at"])
            valid = (setting and setting["enabled"] and setting["revision"] == job["revision"]
                     and due <= now <= due + GRACE and 8 <= current.hour < 23)
            db.execute("UPDATE deliveries SET status=?, content=? WHERE owner=? AND day=?",
                       ("sending" if valid else "cancelled", content, job["owner"], job["day"]))
        return bool(valid)

    def finish(self, job, status, error=None):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status=?, error=? WHERE owner=? AND day=?",
                       (status, error, job["owner"], job["day"]))

    def recover(self, owner):
        # 仅在单实例进程启动时调用。发送可能已发生，不自动补发。
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status='unknown', error='interrupted' WHERE owner=? AND status IN ('running','sending')", (owner,))

    def history(self, owner):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM deliveries WHERE owner=? ORDER BY day DESC LIMIT 10", (owner,))]


def fixed_content(folder, now):
    store = Store(Path(folder) / "assistant.db")
    try:
        tasks = store.list_tasks()
    finally:
        store.close()
    lines = [f"周末准备提醒 · {local_time(now):%Y-%m-%d}"]
    if tasks:
        lines.append("已记录的待办（尚未实现完成状态）：")
        lines.extend(f"{task['id']}. {task['title']}" for task in tasks[:5])
        if len(tasks) > 5:
            lines.append(f"另有 {len(tasks) - 5} 条，可在聊天中查询。")
    else:
        lines.append("目前没有记录待办。可以先选一个周末想去的地方或想做的活动。")
    lines.extend(["出发前请核对：开放与预约信息、交通、天气和预算；这些实时信息尚未查询。", "暂停提醒：/提醒 暂停"])
    return "\n".join(lines)


def compose(folder, job, now):
    text = fixed_content(folder, now)
    if job["mode"] != "ai":
        return text
    client = None
    store = Store(Path(folder) / "assistant.db")
    try:
        client = make_client()
        result = run_agent(client, os.environ["OPENAI_MODEL"],
            "为周末生成一条简短准备建议：查询已记录待办，选一个候选活动；"
            "按需读取活动笔记并注明文件名，考虑已保存的交通、时长和预算偏好。"
            "没有待办或缺少出行条件时明确说明。教学虚构地点只能作为练习草稿。"
            "本次不能联网，不要声称查过天气、实时交通、票价或预约状态；列出待核对项。"
            "不保存新待办，不配置提醒，不声称完成预约或购票。",
            store, version=2, read_only=True, max_rounds=4, max_calls=4)
        if result["status"] != "completed":
            raise RuntimeError("建议生成未完成")
        return text + "\n\n可选的 AI 准备建议（未核对实时出行信息）：\n" + result["answer"][:900]
    except Exception as exc:
        LOG.warning("suggestion_fallback type=%s", type(exc).__name__)
        return text + "\n（本次 AI 建议未生成，已使用固定提醒。）"
    finally:
        store.close()
        if client is not None:
            client.close()


def delivery_uuid(job):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "xiaodazi:friday:" + job["owner"] + ":" + job["day"]))


def build_send_request(owner, text, request_uuid):
    # 延迟导入，离线预览和时间测试不需要飞书 SDK。
    from feishu_bot import load_lark
    load_lark()
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    return (CreateMessageRequest.builder().receive_id_type("open_id")
            .request_body(CreateMessageRequestBody.builder().receive_id(owner).msg_type("text")
                          .content(json.dumps({"text": text}, ensure_ascii=False)).uuid(request_uuid).build())
            .build())


def tick(db, owner, content_fn, send_fn, clock=utc_now):
    job = db.claim(owner, clock())
    if job is None:
        return "idle"
    try:
        content = content_fn(job, clock())
    except Exception as exc:
        db.finish(job, "failed", type(exc).__name__)
        return "failed"
    if not db.before_send(job, content, clock()):
        return "cancelled"
    try:
        # 只发给设置绑定的 owner，模型不能选择收件人或次数。
        send_fn(owner, content, delivery_uuid(job))
    except Exception as exc:
        db.finish(job, "unknown", type(exc).__name__)
        LOG.error("reminder_send_unknown type=%s", type(exc).__name__)
        return "unknown"
    db.finish(job, "sent")
    return "sent"


HELP = "提醒命令：/提醒 开启、/提醒 暂停、/提醒 时间 20:00、/提醒 模式 固定、/提醒 模式 建议、/提醒 状态。"


def handle_command(text, db, owner, now=None):
    if not text.startswith("/提醒"):
        return None
    now = now or utc_now()
    parts = text.split()
    try:
        if parts == ["/提醒", "开启"]:
            db.configure(owner, now, enabled=True)
        elif parts == ["/提醒", "暂停"]:
            db.configure(owner, now, enabled=False)
        elif len(parts) == 3 and parts[:2] == ["/提醒", "时间"]:
            db.configure(owner, now, hhmm=parts[2])
        elif len(parts) == 3 and parts[:2] == ["/提醒", "模式"] and parts[2] in ("固定", "建议"):
            db.configure(owner, now, mode="ai" if parts[2] == "建议" else "fixed")
        elif parts != ["/提醒", "状态"]:
            return HELP
    except ValueError as exc:
        return str(exc)
    setting = db.settings(owner)
    return (f"每周五提醒：{'开启' if setting['enabled'] else '暂停'}；{setting['hhmm']}（{ZONE_NAME}）；"
            f"{'AI建议模式，会调用模型' if setting['mode'] == 'ai' else '固定模式，不调用模型'}。\n"
            "设置变化从下一次尚未到点的周五时刻生效；每个周五最多尝试一条。暂停不能撤回已发出的消息。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "enable", "pause", "status", "history", "time", "mode"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("AGENT_DATA_DIR", str(ROOT / "data/feishu"))))
    args = parser.parse_args()
    if args.action == "preview":
        print(fixed_content(args.data_dir, utc_now()))
        return
    owner = os.getenv("FEISHU_OWNER_OPEN_ID")
    if not owner:
        parser.error("需要 FEISHU_OWNER_OPEN_ID，必须与第四篇的机器人配置一致")
    db = ReminderStore(args.data_dir / "reminders.db")
    if args.action == "history":
        print(json.dumps(db.history(owner), ensure_ascii=False, indent=2))
        return
    command = {"enable": "/提醒 开启", "pause": "/提醒 暂停", "status": "/提醒 状态",
               "time": "/提醒 时间 " + (args.value or ""),
               "mode": "/提醒 模式 " + {"fixed": "固定", "ai": "建议"}.get(args.value, "")}[args.action]
    print(handle_command(command, db, owner))


if __name__ == "__main__":
    main()
