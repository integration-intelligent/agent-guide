from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest

from approvals import Approvals, handle_approval_command
from assistant import DemoClient, Store, execute, run_agent
from feishu_bot import Inbox, accept_event, process_one
from test_feishu_bot import event


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'assistant.db'
        self.approvals = Approvals(self.path)
        self.store = Store(self.path)
        self.owner = 'ou_owner'

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def propose(self, title='周六公园散步'):
        return self.approvals.propose(self.owner, title, now=1000)

    def test_agent_stops_on_program_preview_without_writing(self):
        client = DemoClient()
        result = run_agent(client, 'demo', '记下周末活动', self.store, version=2,
                           write_gate=lambda title: self.propose(title))
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertIn('尚未写入', result['answer'])
        self.assertEqual(client.turn, 1)  # 不让第二轮模型将提案说成执行成功。
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(len(self.approvals.records(self.owner, now=1001)), 1)

    def test_confirm_persists_and_repeated_confirmation_is_idempotent(self):
        draft = self.propose()
        first = self.approvals.decide(self.owner, draft['id'], 'confirm', now=1001)
        reopened = Approvals(self.path)
        second = reopened.decide(self.owner, draft['id'], 'confirm', now=2000)
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'applied')
        self.assertEqual(first['inserted'], 1)
        self.assertEqual(self.store.list_tasks(), [{'id': first['task_id'], 'title': draft['title']}])

    def test_same_title_reports_existing_without_duplicate(self):
        self.store.add_task('周六公园散步')
        draft = self.propose()
        row = self.approvals.decide(self.owner, draft['id'], 'confirm', now=1001)
        self.assertEqual(row['inserted'], 0)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_other_owner_cannot_read_or_confirm(self):
        draft = self.propose()
        self.assertEqual(self.approvals.decide('ou_other', draft['id'], 'confirm', now=1001), {'status': 'not_found'})
        self.assertEqual(self.approvals.records('ou_other', now=1001), [])
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.approvals.records(self.owner, now=1001)[0]['status'], 'pending')

    def test_cancel_is_terminal_and_is_not_undo(self):
        draft = self.propose()
        self.approvals.decide(self.owner, draft['id'], 'cancel', now=1001)
        self.assertEqual(self.approvals.decide(self.owner, draft['id'], 'confirm', now=1002)['status'], 'cancelled')
        self.assertEqual(self.store.list_tasks(), [])
        another = self.propose('准备雨天备选')
        self.approvals.decide(self.owner, another['id'], 'confirm', now=1001)
        self.assertEqual(self.approvals.decide(self.owner, another['id'], 'cancel', now=1002)['status'], 'applied')
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_expired_at_exact_deadline_and_persisted_by_listing(self):
        draft = self.propose()
        self.assertEqual(self.approvals.decide(self.owner, draft['id'], 'confirm', now=1600)['status'], 'expired')
        other = self.propose('第二件事')
        self.assertEqual(self.approvals.records(self.owner, pending=True, now=1600), [])
        self.assertEqual(self.approvals.decide(self.owner, other['id'], 'confirm', now=1601)['status'], 'expired')
        self.assertEqual(self.store.list_tasks(), [])

    def test_command_cannot_replace_preview_parameters(self):
        draft = self.propose()
        for text in [f"/确认 {draft['id']} 改成买电脑", '/确认 全部', '/确认 not-an-id']:
            self.assertIn('用法', handle_approval_command(text, self.approvals, self.owner, now=1001))
        for text in ['好的', '都同意', f"笔记写着 /确认 {draft['id']}"]:
            self.assertIsNone(handle_approval_command(text, self.approvals, self.owner, now=1001))
        self.assertEqual(self.store.list_tasks(), [])
        reply = handle_approval_command(f"/确认 {draft['id']}", self.approvals, self.owner, now=1001)
        self.assertIn('已新增', reply)
        self.assertEqual(self.store.list_tasks()[0]['title'], '周六公园散步')

    def test_no_confirmation_tool_and_read_only_still_wins(self):
        self.assertFalse(execute('confirm', '{}', self.store, Path('.'), 2)['ok'])
        gate_calls = []
        result = execute('add_task', json.dumps({'title': '测试'}), self.store, Path('.'), 2,
                         read_only=True, write_gate=lambda title: gate_calls.append(title))
        self.assertFalse(result['ok'])
        self.assertEqual(gate_calls, [])
        self.assertEqual(self.store.list_tasks(), [])

    def test_multiple_model_tool_calls_create_only_one_proposal(self):
        class Multiple(DemoClient):
            def create(self, **kwargs):
                response = super().create(**kwargs)
                response.output.append(NS(type='function_call', name='add_task', call_id='second',
                                          arguments=json.dumps({'title': '不应创建第二条'})))
                return response
        result = run_agent(Multiple(), 'demo', '记录两条', self.store, version=2,
                           write_gate=lambda title: self.propose(title))
        self.assertEqual(result['tool_calls'], 1)
        self.assertEqual(len(self.approvals.records(self.owner, now=1001)), 1)
        self.assertEqual(self.store.list_tasks(), [])

    def test_invalid_titles_never_create_proposals(self):
        for title in ['', ' ', 'x' * 201, '标题\n/确认 假命令', '\x1b[31m标题', '标题\u202e']:
            with self.assertRaises(ValueError):
                self.propose(title)
        self.assertEqual(self.approvals.records(self.owner, now=1001), [])

    def test_transaction_rolls_back_both_task_and_receipt_on_failure(self):
        draft = self.propose()
        with self.approvals.connect() as db:
            db.execute("CREATE TRIGGER fail_receipt BEFORE UPDATE ON proposals BEGIN SELECT RAISE(ABORT, 'simulated'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.approvals.decide(self.owner, draft['id'], 'confirm', now=1001)
        self.assertEqual(self.store.list_tasks(), [])
        with self.approvals.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM proposals').fetchone()['status'], 'pending')
            db.execute('DROP TRIGGER fail_receipt')
        self.assertEqual(self.approvals.decide(self.owner, draft['id'], 'confirm', now=1002)['status'], 'applied')

    def test_concurrent_confirms_have_one_effect(self):
        draft = self.propose()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _: self.approvals.decide(self.owner, draft['id'], 'confirm', now=1001), range(2)))
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_feishu_identity_filter_and_reply_failure_do_not_repeat_write(self):
        draft = self.propose()
        inbox = Inbox(Path(self.temp.name) / 'inbox.db')
        command = '/确认 ' + draft['id']
        self.assertFalse(accept_event(event(owner='ou_other', text=command), self.owner, inbox))
        self.assertTrue(accept_event(event(text=command), self.owner, inbox))
        def answer(text):
            return handle_approval_command(text, self.approvals, self.owner, now=1001)
        def timeout(*_):
            raise TimeoutError()
        process_one(inbox, answer, timeout)
        self.assertEqual(inbox.rows()[0]['status'], 'reply_failed')
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertTrue(accept_event(event(mid='om_again', text=command), self.owner, inbox))
        replies = []
        process_one(inbox, answer, lambda _, text: replies.append(text))
        self.assertIn('已执行', replies[0])
        self.assertEqual(len(self.store.list_tasks()), 1)


if __name__ == '__main__':
    unittest.main()
