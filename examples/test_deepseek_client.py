"""真实 SDK + 假 HTTP；不联网，不使用真实密钥。"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import Store, make_client, run_agent
from deepseek_client import DeepSeekClient


@unittest.skipUnless(importlib.util.find_spec('openai'), '需要安装 requirements.txt')
class DeepSeekTests(unittest.TestCase):
    def test_provider_uses_own_key_and_endpoint(self):
        with patch.dict(os.environ, {'AGENT_PROVIDER': 'deepseek',
                'DEEPSEEK_API_KEY': 'deepseek-test', 'OPENAI_MODEL': 'test-model',
                'OPENAI_API_KEY': 'other-key', 'OPENAI_BASE_URL': 'https://example.invalid'}, clear=True):
            client = make_client()
            try:
                self.assertEqual(client.client.api_key, 'deepseek-test')
                self.assertEqual(str(client.client.base_url), 'https://api.deepseek.com')
            finally:
                client.close()

    def test_missing_key_does_not_fall_back_to_openai(self):
        with patch.dict(os.environ, {'AGENT_PROVIDER': 'deepseek',
                'OPENAI_API_KEY': 'other-key', 'OPENAI_MODEL': 'test-model'}, clear=True):
            with self.assertRaises(ValueError):
                make_client()

    def exercise(self, *, status='completed', **options):
        import httpx
        from openai import OpenAI
        requests = []
        def handler(request):
            self.assertEqual(str(request.url), 'https://api.deepseek.com/responses')
            body = json.loads(request.content)
            requests.append(body)
            for key in ('store', 'include', 'parallel_tool_calls'):
                self.assertNotIn(key, body)
            self.assertTrue(all('strict' not in tool for tool in body['tools']))
            if len(requests) == 1:
                output = [
                    {'type': 'reasoning', 'id': 'reason_1', 'summary': [],
                     'content': [{'type': 'reasoning_text', 'text': 'mock reasoning'}]},
                    *[{'type': 'function_call', 'id': f'fc_{i}', 'call_id': f'call_{i}',
                       'name': 'add_task', 'arguments': json.dumps({'title': title}),
                       'status': 'completed'} for i, title in enumerate(('公园散步', '雨天看展'))]]
            else:
                self.assertTrue(any(item.get('content') == [{'type': 'reasoning_text', 'text': 'mock reasoning'}]
                                    for item in body['input']))
                results = [item for item in body['input'] if item.get('type') == 'function_call_output']
                self.assertEqual([item['call_id'] for item in results], ['call_0', 'call_1'])
                output = [{'type': 'message', 'id': 'msg_1', 'role': 'assistant', 'status': 'completed',
                           'content': [{'type': 'output_text', 'text': '处理完毕', 'annotations': []}]}]
            return httpx.Response(200, json={'object': 'response', 'id': 'resp_test', 'created_at': 1,
                'status': status, 'model': 'test-model', 'output': output,
                'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}})
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'test.db')
            client = DeepSeekClient(OpenAI(api_key='local-test', base_url='https://api.deepseek.com',
                max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
            try:
                result = run_agent(client, 'test-model', '记下两项活动', store, **options)
                return result, store.list_tasks(), requests
            finally:
                client.close()
                store.close()

    def test_multi_tool_round_trip_preserves_reasoning(self):
        result, tasks, requests = self.exercise()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len(requests), 2)
        self.assertEqual(result['total_tokens'], 30)

    def test_read_only_rejects_unsolicited_writes(self):
        result, tasks, _ = self.exercise(read_only=True)
        self.assertEqual(tasks, [])
        self.assertTrue(all(not event['result']['ok'] for event in result['trace']))

    def test_confirmation_stops_after_first_proposal(self):
        proposals = []
        def gate(title):
            proposals.append(title)
            return {'preview': '待确认，尚未写入'}
        result, tasks, requests = self.exercise(write_gate=gate)
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertEqual(tasks, [])
        self.assertEqual(proposals, ['公园散步'])
        self.assertEqual(len(requests), 1)

    def test_tool_limit_applies_within_one_response(self):
        result, tasks, _ = self.exercise(max_calls=1)
        self.assertEqual(result['status'], 'limit')
        self.assertEqual(len(tasks), 1)

    def test_incomplete_response_never_executes_tools(self):
        result, tasks, _ = self.exercise(status='incomplete')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(tasks, [])


if __name__ == '__main__':
    unittest.main()
