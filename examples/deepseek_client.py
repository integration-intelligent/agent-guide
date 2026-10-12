"""DeepSeek 官方 Responses 接口适配；不影响开发时使用的 Codex。"""
import os


class DeepSeekClient:
    def __init__(self, client):
        self.client = client
        self.responses = self

    def create(self, **kwargs):
        request = dict(kwargs)
        # 官方接口不支持这些选项；并行工具请求仍由本地循环逐个检查。
        for key in ("include", "store", "parallel_tool_calls"):
            request.pop(key, None)
        # 不依赖服务端严格模式，execute() 始终检查工具名和参数。
        request["tools"] = [
            {key: value for key, value in tool.items() if key != "strict"}
            for tool in request.get("tools", [])
        ]
        return self.client.responses.create(**request)

    def close(self):
        self.client.close()


def make_deepseek_client():
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key or not os.getenv("OPENAI_MODEL"):
        raise ValueError("请设置 DEEPSEEK_API_KEY 和 OPENAI_MODEL")
    from openai import OpenAI
    return DeepSeekClient(OpenAI(
        api_key=key, base_url="https://api.deepseek.com",
        timeout=30.0, max_retries=0,
    ))
