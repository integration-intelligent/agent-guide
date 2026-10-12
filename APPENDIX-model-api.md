# 附录 A：配置小搭子的模型 API

第一篇已包含 OpenAI、DeepSeek 的最小配置步骤，首次跟做不必跳转到本页。需要集中查阅配置、切换服务或了解接口适配边界时，再使用本附录。开始前应已安装 `examples/requirements.txt`；这里仅配置你开发的 Python 程序，Codex 的开发方式保持不变。选择一套即可，不需要把两个服务都注册一遍。

## 1. 只选择一套配置

Codex 继续负责辅助开发。运行小搭子时，可以单独选择 LLM API；安装 `openai` 这个 Python 库，并不意味着必须使用 OpenAI 的模型。这里提供两个明确的配置入口：

| `AGENT_PROVIDER` | 密钥变量 | 配置位置 |
| --- | --- | --- |
| `deepseek` | `DEEPSEEK_API_KEY` | 下方示例 |
| `openai`（默认） | `OPENAI_API_KEY` | 下方示例，先核对服务支持地区 |

两者共用 `OPENAI_MODEL` 保存**所选服务商的模型 ID**，这个变量名只是沿用初版代码。密钥、服务商、模型 ID 必须配套，切换服务时一起改。模型 API 的账号和费用与开发时使用的 Codex 分开。

## 2. 选项 A：DeepSeek

截至本次核对，DeepSeek 官方已提供 Responses API，配套程序使用其官方地址。以下模型 ID 来自官方示例，实际可用性以你的账号为准。[参考：DeepSeek Responses API](https://api-docs.deepseek.com/guides/responses_api/)

macOS 默认 zsh：

```zsh
read -s "DEEPSEEK_API_KEY?粘贴 DeepSeek API Key 后回车："
export DEEPSEEK_API_KEY
export AGENT_PROVIDER=deepseek
export OPENAI_MODEL=deepseek-flash
```

Linux Bash：

```bash
read -s -p "粘贴 DeepSeek API Key 后回车：" DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
export AGENT_PROVIDER=deepseek
export OPENAI_MODEL=deepseek-flash
```

Windows PowerShell：

```powershell
$key = Read-Host "粘贴 DeepSeek API Key" -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
Remove-Variable key
$env:AGENT_PROVIDER = "deepseek"
$env:OPENAI_MODEL = "deepseek-flash"
```

配置完成后，返回[第一篇：使用所选服务运行](01-first-agent.md#run-model)，不用再配置 OpenAI Key。此路径通过 [deepseek_client.py](examples/deepseek_client.py) 适配请求，共用待办、笔记、飞书和确认机制；不是修改 Codex 的模型配置。

## 3. 选项 B：OpenAI

使用前核对[官方支持地区](https://developers.openai.com/api/docs/supported-countries)。

准备可用的 OpenAI API Key，以及账号有权限调用、支持 Responses API 与 function calling 的模型 ID。不要填界面里的展示名称；以 [API 模型目录](https://developers.openai.com/api/docs/models)中的标识和账号实际权限为准。本文不固定一个型号，代码从 `OPENAI_MODEL` 读取。

在 macOS 默认的 zsh 终端，可隐藏输入密钥：

```zsh
read -s "OPENAI_API_KEY?粘贴 API Key 后回车："
export OPENAI_API_KEY
export AGENT_PROVIDER=openai
export OPENAI_MODEL="替换为你可用的模型ID"
```

Linux Bash 使用：

```bash
read -s -p "粘贴 API Key 后回车：" OPENAI_API_KEY
export OPENAI_API_KEY
export AGENT_PROVIDER=openai
export OPENAI_MODEL="替换为你可用的模型ID"
```

PowerShell 使用：

```powershell
$key = Read-Host "粘贴 API Key" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
Remove-Variable key
$env:AGENT_PROVIDER = "openai"
$env:OPENAI_MODEL = "替换为你可用的模型ID"
```

## 4. 以后接入其他模型服务

待办、笔记、技能文件、偏好和本轮上下文由 Python 管理，不依赖某一家模型。第一篇切换 `AGENT_PROVIDER` 后仍使用它们。网页搜索也是自己的 Python 工具，切换 LLM 不会自动获得服务商内置搜索，`BOCHA_API_KEY` 仍需单独配置。

但“OpenAI 兼容”不代表所有接口相同。本教程循环使用 **Responses API**；只有 `chat.completions` 的服务不能仅改 `base_url` 就接入。适配层需要转换工具定义、模型消息、工具调用 ID 与结果，保留继续对话所需的内容，再通过第三篇评估。先用已有的 OpenAI 或 DeepSeek 入口跑通，其他服务按其官方协议逐个接入。

DeepSeek 适配去掉不适用的请求选项，并保留模型返回的全部输出供下一轮使用。它不会替程序保证只返回一个工具请求；循环仍逐个校验、计数，达到上限就停止。具体参数差异见[官方兼容说明](https://api-docs.deepseek.com/guides/responses_api/)。

可以这样让 Codex 帮你扩展：

```text
我想增加另一家 LLM API，保留当前 Codex 开发方式和 Agent 业务逻辑。
先核对它的官方接口是否支持 Responses 和 function calling，列出差异。
参照 deepseek_client.py 增加独立适配，不把供应商判断散落到工具代码。
用假 HTTP 检查工具往返、错误响应和多工具调用，再告诉我如何用真实密钥验收。
不要声称只配置 base_url 就兼容所有服务，不要自动执行付费评估。
```

## 配置后检查

配置只对当前终端及其子进程生效；本项目不自动读取 `.env` 文件。第一篇的真实模型验收、飞书进程和 `evaluate.py` 都读取同一套变量。在另一个终端运行时需要重新设置；服务器则使用部署附录的凭据文件。

请求会使用所选服务商的 API 额度。密钥不放入提示词、源代码或 Git；模型 ID 以官方文档和账号权限为准。SDK 模拟测试通过不等于真实服务联调通过，见[验证记录](VALIDATION.md)。

[返回第一篇运行模型](01-first-agent.md#run-model) · [返回第四篇](04-agent-on-your-phone.md) · [返回目录](README.md)
