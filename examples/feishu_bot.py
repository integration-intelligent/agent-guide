"""第四篇：单人飞书长连接入口。事件快速落盘，单工作线程处理任务。"""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from assistant import ROOT, Store, make_client, run_agent
from reminders import ReminderStore, build_send_request, compose, handle_command, tick
from approvals import Approvals, handle_approval_command

LOG = logging.getLogger("xiaodazi")


def load_lark():
    # SDK 导入时会获取事件循环；为较新 Python 显式准备当前线程的循环。
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    import lark_oapi as lark
    return lark


class Inbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS inbox (
                message_id TEXT PRIMARY KEY, text TEXT NOT NULL,
                status TEXT NOT NULL, answer TEXT, error TEXT)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, message_id, text):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM inbox WHERE message_id=?", (message_id,)).fetchone():
                return False
            active = db.execute("SELECT COUNT(*) FROM inbox WHERE status IN ('queued','running')").fetchone()[0]
            if active >= 20:
                raise RuntimeError("任务队列已满")  # 交给 SDK 返回事件失败；不确认已受理。
            db.execute("INSERT INTO inbox VALUES (?, ?, 'queued', NULL, NULL)", (message_id, text))
        return True

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE inbox SET status='needs_review', error='interrupted' WHERE status='running'")

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM inbox WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE inbox SET status='running' WHERE message_id=?", (row["message_id"],))
                return dict(row)
        return None

    def update(self, message_id, status, answer=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE inbox SET status=?, answer=?, error=? WHERE message_id=?",
                       (status, answer, error, message_id))

    def rows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM inbox ORDER BY rowid")]


def accept_event(data, owner, inbox):
    event = data.event
    sender, message = event.sender, event.message
    if sender.sender_type != "user" or message.chat_type != "p2p":
        return False
    if sender.sender_id.open_id != owner:
        return False
    if message.message_type != "text":
        return False
    try:
        content = json.loads(message.content)
        text = content.get("text")
    except (ValueError, AttributeError, TypeError):
        return False
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return False
    if not message.message_id:
        return False
    return inbox.enqueue(message.message_id, text.strip())


def process_one(inbox, answer_fn, reply_fn):
    job = inbox.claim()
    if not job:
        return False
    mid = job["message_id"]
    try:
        answer = answer_fn(job["text"])
    except Exception as exc:
        error = type(exc).__name__
        # 可能已有部分工具写入，不自动重跑 Agent。
        inbox.update(mid, "needs_review", error=error)
        LOG.error("task_failed id=%s type=%s", mid, error)
        try:
            reply_fn(mid, "处理未完成，可能已有部分待办写入。请先查询待办，再决定是否重试。")
        except Exception:
            LOG.error("error_reply_failed id=%s", mid)
        return True
    answer = str(answer)
    if len(answer) > 2500:
        answer = answer[:2500] + "\n（回答过长已截断，请缩小问题后再问。）"
    # 先落盘答案，再发送。重启后不重跑已经执行过的 Agent。
    inbox.update(mid, "reply_pending", answer=answer)
    try:
        reply_fn(mid, answer)
        inbox.update(mid, "done", answer=answer)
    except Exception as exc:
        inbox.update(mid, "reply_failed", answer=answer, error=type(exc).__name__)
        LOG.error("reply_failed id=%s type=%s", mid, type(exc).__name__)
    return True


def build_reply_request(message_id, text):
    load_lark()
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
    return (ReplyMessageRequest.builder().message_id(message_id)
            .request_body(ReplyMessageRequestBody.builder().msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .uuid(str(uuid.uuid5(uuid.NAMESPACE_URL, "xiaodazi:" + message_id)))
                .build()).build())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--identify", action="store_true", help="只打印单聊发送者 open_id，不调用模型或回复")
    mode.add_argument("--echo", action="store_true", help="仅原样回复授权用户，不调用模型")
    mode.add_argument("--jobs", action="store_true", help="本地查看消息处理状态，不联网")
    parser.add_argument("--confirm-writes", action="store_true", help="第六篇：模型添加待办时先生成提案，用户确认后写入")
    parser.add_argument("--web", action="store_true", help="为普通聊天启用上海文旅官网资料联网工具；不影响定时提醒")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    folder = Path(os.getenv("AGENT_DATA_DIR", str(ROOT / "data/feishu")))
    if args.jobs:
        inbox = Inbox(folder / "inbox.db")
        print(json.dumps([{k: row[k] for k in ("message_id", "status", "error")} for row in inbox.rows()], indent=2))
        return
    app_id, secret = os.getenv("FEISHU_APP_ID"), os.getenv("FEISHU_APP_SECRET")
    owner = os.getenv("FEISHU_OWNER_OPEN_ID")
    if not app_id or not secret or (not args.identify and not owner):
        parser.error("需要 FEISHU_APP_ID、FEISHU_APP_SECRET；非 identify 模式还需要 FEISHU_OWNER_OPEN_ID")
    lark = load_lark()
    inbox = Inbox(folder / "inbox.db")

    if not args.identify:
        # 固定提醒和管理命令不需要模型；普通任务首次执行时才检查模型配置。
        model_client = None
        api = lark.Client.builder().app_id(app_id).app_secret(secret).timeout(15).log_level(lark.LogLevel.WARNING).build()
        inbox.recover()
        reminders = ReminderStore(folder / "reminders.db")
        approvals = Approvals(folder / "assistant.db") if args.confirm_writes and not args.echo else None

        def answer(text):
            nonlocal model_client
            if args.echo:
                return "连接测试收到：" + text
            if approvals is not None:
                approval_reply = handle_approval_command(text, approvals, owner)
                if approval_reply is not None:
                    return approval_reply
            elif text.startswith(("/确认", "/取消", "/待确认", "/操作记录")):
                return "当前未启用确认模式；请停止进程后使用 --confirm-writes 启动。"
            command_reply = handle_command(text, reminders, owner)
            if command_reply is not None:
                return command_reply
            if model_client is None:
                model_client = make_client()
            store = Store(folder / "assistant.db")
            try:
                from web_tools import WebTools
                result = run_agent(model_client, os.environ["OPENAI_MODEL"], text, store, version=2,
                                   skill=text.startswith("周末计划："),
                                   write_gate=(lambda title: approvals.propose(owner, title)) if approvals is not None else None,
                                   web=WebTools() if args.web else None)
                if result["status"] not in ("completed", "needs_confirmation"):
                    raise RuntimeError("Agent未完成")
                return result["answer"]
            finally:
                store.close()

        def reply(message_id, text):
            response = api.im.v1.message.reply(build_reply_request(message_id, text))
            if not response.success():
                LOG.error("feishu_api_failed id=%s code=%s", message_id, response.code)
                raise RuntimeError("FeishuReplyError:" + str(response.code))

        def worker():
            pause = threading.Event()
            while True:
                try:
                    processed = process_one(inbox, answer, reply)
                    pause.wait(0.3 if processed else 0.5)
                except Exception as exc:
                    # 状态库故障时退出，让进程管理器重启，避免在线但不工作的假象。
                    LOG.critical("worker_failed type=%s", type(exc).__name__)
                    os._exit(1)

        threading.Thread(target=worker, daemon=True, name="agent-worker").start()

        if not args.echo:
            reminders.recover(owner)

            def reminder_worker():
                # 独立客户端和线程：生成出行建议不会占用聊天工作线程。
                sender = lark.Client.builder().app_id(app_id).app_secret(secret).timeout(15).log_level(lark.LogLevel.WARNING).build()

                def send(recipient, text, request_uuid):
                    response = sender.im.v1.message.create(build_send_request(recipient, text, request_uuid))
                    if not response.success():
                        raise RuntimeError("FeishuSendError:" + str(response.code))

                pause = threading.Event()
                while True:
                    try:
                        result = tick(reminders, owner, lambda job, now: compose(folder, job, now), send)
                        if result != "idle":
                            LOG.info("reminder_status=%s", result)
                        pause.wait(15)
                    except Exception as exc:
                        LOG.critical("scheduler_failed type=%s", type(exc).__name__)
                        os._exit(1)

            threading.Thread(target=reminder_worker, daemon=True, name="reminder-worker").start()

    def on_message(data):
        if args.identify:
            if data.event.sender.sender_type == "user" and data.event.message.chat_type == "p2p":
                print("单聊发送者 open_id:", data.event.sender.sender_id.open_id, flush=True)
            return
        if accept_event(data, owner, inbox):
            LOG.info("accepted id=%s", data.event.message.message_id)

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(on_message).build())
    LOG.info("starting mode=%s; 每个应用只启动一个实例", "identify" if args.identify else "echo" if args.echo else "agent")
    LOG.info("confirm_writes=%s", bool(args.confirm_writes and not args.identify and not args.echo))
    lark.ws.Client(app_id, secret, event_handler=handler, log_level=lark.LogLevel.WARNING).start()


if __name__ == "__main__":
    main()
