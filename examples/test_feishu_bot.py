import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from feishu_bot import Inbox, accept_event, build_reply_request, process_one


def event(mid="om_test", owner="ou_owner", chat="p2p", text="记下买牛奶", kind="text"):
    return NS(event=NS(sender=NS(sender_type="user", sender_id=NS(open_id=owner)),
        message=NS(message_id=mid, chat_type=chat, message_type=kind,
                   content=json.dumps({"text": text}))))


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "inbox.db"
        self.inbox = Inbox(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_owner_private_text(self):
        for item in [event(owner="ou_other"), event(chat="group"), event(kind="image"), event(text=""), event(text="x" * 2001)]:
            self.assertFalse(accept_event(item, "ou_owner", self.inbox))
        self.assertEqual(self.inbox.rows(), [])
        self.assertTrue(accept_event(event(), "ou_owner", self.inbox))

    def test_duplicates_survive_reopen(self):
        self.assertTrue(accept_event(event(), "ou_owner", self.inbox))
        self.assertFalse(accept_event(event(), "ou_owner", Inbox(self.path)))
        self.assertEqual(len(self.inbox.rows()), 1)

    def test_queue_is_bounded(self):
        for i in range(20):
            self.inbox.enqueue(str(i), "test")
        with self.assertRaises(RuntimeError):
            self.inbox.enqueue("overflow", "test")
        self.assertEqual(len(self.inbox.rows()), 20)

    def test_normal_task_executes_once(self):
        self.inbox.enqueue("one", "hello")
        calls, replies = [], []
        def answer(text):
            calls.append(text)
            return "完成"
        self.assertTrue(process_one(self.inbox, answer, lambda mid, text: replies.append((mid, text))))
        self.assertFalse(process_one(self.inbox, answer, lambda *_: None))
        self.assertEqual(calls, ["hello"])
        self.assertEqual(replies, [("one", "完成")])
        self.assertEqual(self.inbox.rows()[0]["status"], "done")

    def test_reply_failure_does_not_rerun_agent(self):
        self.inbox.enqueue("one", "hello")
        def fail(*_):
            raise TimeoutError()
        process_one(self.inbox, lambda _: "已完成", fail)
        self.assertEqual(self.inbox.rows()[0]["status"], "reply_failed")
        self.assertEqual(self.inbox.rows()[0]["answer"], "已完成")
        self.inbox.recover()
        self.assertIsNone(self.inbox.claim())

    def test_crash_requires_review_but_queued_task_remains(self):
        self.inbox.enqueue("one", "hello")
        self.inbox.enqueue("two", "hello")
        self.inbox.claim()
        self.inbox.recover()
        self.assertEqual(self.inbox.rows()[0]["status"], "needs_review")
        self.assertEqual(self.inbox.claim()["message_id"], "two")

    def test_agent_failure_does_not_requeue(self):
        self.inbox.enqueue("one", "hello")
        def fail(_):
            raise TimeoutError()
        replies = []
        process_one(self.inbox, fail, lambda _, text: replies.append(text))
        self.assertEqual(self.inbox.rows()[0]["status"], "needs_review")
        self.assertIn("部分待办写入", replies[0])
        self.assertIsNone(self.inbox.claim())

    @unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "未安装飞书 SDK")
    def test_sdk_reply_request(self):
        request = build_reply_request("om_test", "中文\n回复")
        other = build_reply_request("om_test", "中文\n回复")
        self.assertEqual(request.paths["message_id"], "om_test")
        self.assertEqual(request.request_body.msg_type, "text")
        self.assertEqual(json.loads(request.request_body.content)["text"], "中文\n回复")
        self.assertEqual(request.request_body.uuid, other.request_body.uuid)
        self.assertLessEqual(len(request.request_body.uuid), 50)


if __name__ == "__main__":
    unittest.main()
