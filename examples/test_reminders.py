import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant import DemoClient, Store, run_agent
from reminders import (ReminderStore, build_send_request, check_time, compose,
                       delivery_uuid, fixed_content, handle_command, tick)

OWNER = 'ou_owner'


def at(value='2026-10-16T20:00:00+08:00'):
    return datetime.fromisoformat(value)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.db = ReminderStore(self.folder / 'reminders.db')
        self.sent = []
        self.now = at()

    def tearDown(self):
        self.temp.cleanup()

    def enable(self):
        self.db.configure(OWNER, at('2026-10-16T19:00:00+08:00'), enabled=True)

    def step(self, content=None, sender=None):
        return tick(self.db, OWNER, content or (lambda *_: '周末准备提醒'),
                    sender or (lambda *args: self.sent.append(args)), clock=lambda: self.now)

    def test_disabled_by_default_and_owner_isolation(self):
        self.assertEqual(self.step(), 'idle')
        self.db.configure('ou_other', self.now - timedelta(hours=1), enabled=True)
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(self.sent, [])

    def test_only_shanghai_friday_can_claim(self):
        self.enable()
        for days in range(1, 7):
            self.now = at() + timedelta(days=days)
            self.assertEqual(self.step(), 'idle')
        # UTC仍是周五，但上海已经周六，不能按UTC星期触发。
        self.now = at('2026-10-16T16:10:00+00:00')
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(self.db.history(OWNER), [])
        self.now = at() + timedelta(days=7)
        self.assertEqual(self.step(), 'sent')
        self.assertEqual(len(self.sent), 1)

    def test_due_once_across_reopen_with_utc_clock(self):
        self.enable()
        self.now = at('2026-10-16T11:59:59+00:00')
        self.assertEqual(self.step(), 'idle')
        self.now = at('2026-10-16T12:00:00+00:00')
        self.assertEqual(self.step(), 'sent')
        self.db = ReminderStore(self.folder / 'reminders.db')
        self.db.recover(OWNER)
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], OWNER)
        self.assertEqual(self.db.history(OWNER)[0]['status'], 'sent')

    def test_enable_after_due_waits_until_next_friday(self):
        self.db.configure(OWNER, self.now + timedelta(minutes=1), enabled=True)
        self.now += timedelta(minutes=2)
        self.assertEqual(self.step(), 'idle')
        self.now += timedelta(days=7)
        self.assertEqual(self.step(), 'sent')

    def test_grace_window_and_no_historical_backfill(self):
        self.enable()
        self.now += timedelta(minutes=10)
        self.assertEqual(self.step(), 'sent')
        self.now = at('2026-10-30T20:16:00+08:00')
        self.assertEqual(self.step(), 'idle')
        self.assertEqual([r['status'] for r in self.db.history(OWNER)], ['missed', 'sent'])
        self.assertEqual(len(self.sent), 1)

    def test_time_change_cannot_send_twice_same_day(self):
        self.enable()
        self.assertEqual(self.step(), 'sent')
        self.db.configure(OWNER, self.now + timedelta(minutes=1), hhmm='21:00')
        self.now += timedelta(hours=1)
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(len(self.sent), 1)

    def test_pause_while_composing_cancels_send(self):
        self.enable()
        def content(*_):
            handle_command('/提醒 暂停', self.db, OWNER, self.now)
            return '不应发送'
        self.assertEqual(self.step(content), 'cancelled')
        self.assertEqual(self.sent, [])

    def test_generation_over_grace_or_settings_change_cancels(self):
        self.enable()
        def slow(*_):
            self.now += timedelta(minutes=16)
            return '已过期'
        self.assertEqual(self.step(slow), 'cancelled')
        self.now = at('2026-10-23T20:00:00+08:00')
        def changed(*_):
            self.db.configure(OWNER, self.now, hhmm='21:00')
            return '旧设置'
        self.assertEqual(self.step(changed), 'cancelled')
        self.assertEqual(self.sent, [])

    def test_timeout_is_unknown_and_never_auto_retried(self):
        self.enable()
        def timeout(*args):
            self.sent.append(args)  # 模拟平台已接收，但响应没有回来。
            raise TimeoutError()
        self.assertEqual(self.step(sender=timeout), 'unknown')
        self.db.recover(OWNER)
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.db.history(OWNER)[0]['status'], 'unknown')

    def test_restart_during_run_or_send_requires_review(self):
        self.enable()
        job = self.db.claim(OWNER, self.now)
        self.db.recover(OWNER)
        self.assertFalse(self.db.before_send(job, 'late', self.now))
        self.assertEqual(self.step(), 'idle')
        self.now += timedelta(days=7)
        job = self.db.claim(OWNER, self.now)
        self.assertTrue(self.db.before_send(job, 'test', self.now))
        self.db.recover(OWNER)
        self.assertEqual(self.step(), 'idle')
        self.assertTrue(all(r['status'] == 'unknown' for r in self.db.history(OWNER)))

    def test_failed_content_does_not_send(self):
        self.enable()
        def fail(*_):
            raise OSError()
        self.assertEqual(self.step(fail), 'failed')
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(self.sent, [])

    def test_quiet_hours_and_invalid_times(self):
        for value in ['7:00', '07:59', '23:00', '24:00', '20:60', 'tomorrow']:
            with self.assertRaises(ValueError):
                check_time(value)
        self.assertEqual(check_time('08:00'), '08:00')
        self.db.configure(OWNER, self.now, enabled=True, hhmm='22:59')
        self.now = at('2026-10-16T23:00:00+08:00')
        self.assertEqual(self.step(), 'idle')
        self.assertEqual(self.db.history(OWNER)[0]['status'], 'missed')
        with self.assertRaises(ValueError):
            self.db.claim(OWNER, datetime(2026, 10, 12, 20))

    def test_preview_and_commands_do_not_consume_delivery(self):
        self.enable()
        self.assertIn('目前没有记录待办', fixed_content(self.folder, self.now))
        self.assertIn('开启', handle_command('/提醒 状态', self.db, OWNER, self.now))
        self.assertIn('HH:MM', handle_command('/提醒 时间 invalid', self.db, OWNER, self.now))
        self.assertIsNone(handle_command('周末去哪玩', self.db, OWNER, self.now))
        self.assertEqual(self.db.history(OWNER), [])
        self.assertEqual(self.step(), 'sent')

    def test_ai_failure_falls_back_without_model_for_fixed_mode(self):
        with patch('reminders.make_client', side_effect=TimeoutError()) as client:
            self.assertNotIn('未生成', compose(self.folder, {'mode': 'fixed'}, self.now))
            client.assert_not_called()
            self.assertIn('已使用固定提醒', compose(self.folder, {'mode': 'ai'}, self.now))
            client.assert_called_once()

    def test_read_only_agent_filters_tools_and_rejects_forged_write(self):
        case = self
        class ForgedWrite(DemoClient):
            def create(self, **kwargs):
                case.assertNotIn('add_task', [t['name'] for t in kwargs['tools']])
                return super().create(**kwargs)
        store = Store(self.folder / 'assistant.db')
        try:
            result = run_agent(ForgedWrite(), 'demo', '建议', store, version=2, read_only=True)
            self.assertFalse(result['trace'][0]['result']['ok'])
            self.assertEqual(store.list_tasks(), [])
        finally:
            store.close()

    @unittest.skipUnless(importlib.util.find_spec('lark_oapi'), '未安装飞书 SDK')
    def test_sdk_proactive_message_request(self):
        job = {'owner': OWNER, 'day': '2026-10-16'}
        request = build_send_request(OWNER, '中文提醒', delivery_uuid(job))
        self.assertIn(('receive_id_type', 'open_id'), request.queries)
        self.assertEqual(request.request_body.receive_id, OWNER)
        self.assertEqual(json.loads(request.request_body.content), {'text': '中文提醒'})
        self.assertEqual(request.request_body.uuid, delivery_uuid(job))
        self.assertNotEqual(delivery_uuid(job), delivery_uuid({**job, 'day': '2026-10-23'}))


if __name__ == '__main__':
    unittest.main()
