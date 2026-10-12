"""安装 requirements 后额外检查真实 SDK 序列化；HTTP 使用本地假响应。"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from assistant import Store, run_agent


@unittest.skipUnless(importlib.util.find_spec("openai"), "未安装 SDK；跳过本地 HTTP 契约测试")
class SDKContractTests(unittest.TestCase):
    def test_responses_round_trip(self):
        import httpx
        from openai import OpenAI
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                output = [{"type": "function_call", "id": "fc_test", "call_id": "call_test",
                           "name": "add_task", "arguments": '{"title":"整理书桌"}', "status": "completed"}]
            else:
                self.assertEqual(body["input"][-1]["call_id"], "call_test")
                self.assertTrue(json.loads(body["input"][-1]["output"])["ok"])
                output = [{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": "已记录整理书桌", "annotations": []}]}]
            return httpx.Response(200, json={"id": f"resp_{len(requests)}", "object": "response",
                "created_at": 1, "status": "completed", "model": "test-model", "output": output,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "test.db")
            try:
                with OpenAI(api_key="local-test-placeholder", max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
                    result = run_agent(client, "test-model", "记录整理书桌", store)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["total_tokens"], 30)
                self.assertEqual(store.list_tasks()[0]["title"], "整理书桌")
                self.assertEqual(len(requests), 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
