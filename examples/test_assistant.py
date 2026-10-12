import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from assistant import Store, DemoClient, execute, run_agent
from evaluate import grade


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder / "test.db")
        self.notes = self.folder / "notes"
        self.notes.mkdir()
        (self.notes / "test.md").write_text("周末公园散步", encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def call(self, name, args, version=2):
        return execute(name, json.dumps(args), self.store, self.notes, version)

    def test_demo_persists_and_passes_tool_result_back(self):
        class CheckingClient(DemoClient):
            def create(inner, **kwargs):
                if inner.turn == 1:
                    last = kwargs["input"][-1]
                    self.assertEqual(last["call_id"], "demo-call")
                    self.assertTrue(json.loads(last["output"])["ok"])
                return super().create(**kwargs)
        result = run_agent(CheckingClient(), "demo", "test", self.store)
        self.assertEqual(result["status"], "completed")
        other = Store(self.folder / "test.db")
        try:
            self.assertEqual(len(other.list_tasks()), 1)
        finally:
            other.close()

    def test_same_title_deduplicated(self):
        self.store.add_task("买牛奶")
        self.store.add_task(" 买牛奶 ")
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_invalid_arguments(self):
        for args in [{"title": " "}, {"title": 1}, {"title": "a", "extra": "b"}, []]:
            self.assertFalse(self.call("add_task", args)["ok"])
        self.assertFalse(execute("add_task", "{", self.store, self.notes, 1)["ok"])
        self.assertEqual(self.store.list_tasks(), [])

    def test_unknown_tool_and_v1_boundary(self):
        self.assertFalse(self.call("shell", {})["ok"])
        self.assertFalse(self.call("read_note", {"name": "test.md"}, 1)["ok"])

    def test_path_escape(self):
        (self.folder / "secret.md").write_text("secret", encoding="utf-8")
        self.assertFalse(self.call("read_note", {"name": "../secret.md"})["ok"])
        self.assertFalse(self.call("read_note", {"name": str(self.folder / "secret.md")})["ok"])

    def test_search_and_read(self):
        self.assertEqual(self.call("search_notes", {"query": "公园"})["data"][0]["name"], "test.md")
        self.assertIn("散步", self.call("read_note", {"name": "test.md"})["data"]["content"])
        self.assertEqual(self.call("search_notes", {"query": "不存在"})["data"], [])

    def test_memory_lifecycle(self):
        self.store.remember("喜欢轻松步行")
        other = Store(self.folder / "test.db")
        try:
            self.assertEqual(other.preference(), "喜欢轻松步行")
        finally:
            other.close()
        self.store.forget()
        self.assertEqual(self.store.preference(), "尚未保存偏好")

    def test_tool_budget(self):
        result = run_agent(DemoClient(), "demo", "test", self.store, max_calls=0)
        self.assertEqual(result["status"], "limit")
        self.assertEqual(self.store.list_tasks(), [])

    def test_round_budget_preserves_partial_write(self):
        result = run_agent(DemoClient(), "demo", "test", self.store, max_rounds=1)
        self.assertEqual(result["status"], "limit")
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_incomplete_response_never_executes_tool(self):
        class Incomplete(DemoClient):
            def create(self, **kwargs):
                response = super().create(**kwargs)
                response.status = "incomplete"
                return response
        result = run_agent(Incomplete(), "demo", "test", self.store)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(self.store.list_tasks(), [])

    def test_grader_detects_unwanted_write(self):
        case = {"expected_titles": [], "required_tools": [], "contains": []}
        result = {"status": "completed", "trace": [], "answer": "建议如下"}
        self.assertFalse(grade(case, result, [{"title": "意外写入"}])["state"])


if __name__ == "__main__":
    unittest.main()
