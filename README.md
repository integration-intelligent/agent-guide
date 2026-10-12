# 使用 Codex 做一个自己的 AI Agent：周末活动搭子开发手记

周五晚上，你想安排一个不太累的周末：公园散步、逛逛书店，下雨就换成看展。收藏夹里有不少资料，却还没有一份适合自己的计划。

这套教程带你做一个叫 **小搭子** 的个人 Agent。它从记录待办开始，逐步学会读取活动笔记、参考出行偏好、提出计划，通过飞书与你聊天，并在周五主动提醒你准备。最后，你会为它加上操作预览和确认机制，知道它准备做什么、实际做了什么。

**Codex 是辅助开发的编程助手，小搭子是你写出来、可以独立运行的 Python 程序。** 小搭子的模型 API 可选择 OpenAI 或 DeepSeek，不需要因此改变 Codex 的开发方式。

## 适合谁，先准备什么

适合写过一点 Python、知道变量、函数、列表和字典的学生或业余开发者。无需先学机器学习、向量数据库或 Agent 框架；遇到陌生代码，可以用每篇给出的提示词请 Codex 解释。

准备 Python 3.10 或以上版本、一个可用的 Codex 开发入口，以及本仓库文件。建议使用已完成本地验证的 Python 3.12。第一步离线演示不需要模型账号；真实自然语言任务需要你所选服务的 API Key 和可用额度。到第四篇才需要飞书账号及自建应用权限，服务器始终是可选项。

第一次阅读，建议**先运行配套代码，再让 Codex 带你理解、修改一小处、重新验收**。想从空目录独立实现，也可以使用每篇的开发提示词，但需按实际生成的文件和参数调整命令。

## 六篇主线

| 文章 | 这一篇解决的问题 | 完成标志 |
| --- | --- | --- |
| [01：从零做出一个能跑的 Agent](01-first-agent.md) | 模型怎样调用自己的 Python 函数？ | 用自然语言记录、查询，重启后数据仍在 |
| [02：工具、技能、记忆与上下文](02-grow-your-agent.md) | 怎么用自己的资料和偏好生成建议？ | 从笔记生成主方案与雨天备选，知道材料来自哪里 |
| [03：评估与稳定性](03-evaluate-your-agent.md) | 怎么知道修改后变好了？ | 跑自己的六条基准任务，读懂一次失败 |
| [04：把 Agent 放进口袋](04-agent-on-your-phone.md) | 怎么从手机使用？ | 飞书单聊可用，分清聊天入口和运行机器 |
| [05：定时任务与主动提醒](05-scheduled-reminders.md) | 怎么在合适的时候主动来找你？ | 能预览、开启、暂停周五提醒，并核对发送记录 |
| [06：操作边界与确认机制](06-boundaries-and-confirmation.md) | 怎么控制它实际能改什么？ | 先看提案再确认，重复确认不重复写入 |

顺序是“跑通 → 扩展 → 评估 → 随手用 → 主动用 → 控制操作”。先在本地形成验证习惯，再接外部平台，能减少同时排查的变量。第六篇的确认模式可提前启用，不必等读完整套才用于个人数据。

只想做本地练习，先读前三篇就能得到一个完整项目。第二篇的联网实战可以回头再做；第四篇完成手机入口后，也可以跳过服务器部署，直接继续第五篇。

- [附录 A：模型 API 配置](APPENDIX-model-api.md)：第一篇已包含最小配置；需要集中查阅、切换服务或了解接口适配边界时再读。
- [番外：让小搭子持续在线——部署到 Linux（附录 B）](APPENDIX-deployment.md)：第四篇之后可选读，也可读完六篇再做；需要电脑关机后仍在线时再部署。
- [验证记录](VALIDATION.md)：当前检查结果、复现命令和仍未完成的真实环境验收。

## 先体验一次

在下载后的仓库根目录打开终端：

```bash
cd examples
python3 assistant.py --demo --db data/demo.db
python3 assistant.py --inspect --db data/demo.db
```

Windows 将 `python3` 换成 `py`。这两条命令使用独立演示数据库：固定脚本添加一条待办，再直接读取，**尚未调用模型，也不能理解自由输入**。随后按第一篇安装依赖、配置 API，完成真实模型验收。请不要把真实密钥交给 Codex 或提交到 Git。

每篇的终端命令默认在 `examples` 中执行，服务器命令会单独标明。终端命令和发给飞书机器人的聊天命令是两个入口，不要互换。

## 文件怎么找，数据在哪里

| 想了解什么 | 主要文件 |
| --- | --- |
| 核心循环、工具和数据库 | [assistant.py](examples/assistant.py) |
| 活动资料与规划流程 | [活动笔记](examples/notes/weekend-spots.md)、[雨天笔记](examples/notes/rainy-day.md)、[技能模板](examples/skills/weekend-plan.md) |
| 可选联网与模型适配 | [web_tools.py](examples/web_tools.py)、[DeepSeek 适配](examples/deepseek_client.py) |
| 评估用例与脚本 | [eval_cases.json](examples/eval_cases.json)、[evaluate.py](examples/evaluate.py) |
| 手机入口与主动提醒 | [feishu_bot.py](examples/feishu_bot.py)、[reminders.py](examples/reminders.py) |
| 提案与确认入口 | [approvals.py](examples/approvals.py)、[guarded_assistant.py](examples/guarded_assistant.py) |

`assistant.py --version 1` 只开放添加与查询；`--version 2` 再开放笔记和偏好，`--skill weekend-plan` 加载规划流程，`--web` 显式开启联网工具。仓库包含六篇完成后的代码，文章按学习顺序逐步启用能力，不需要寻找不同分支。

| 入口 | 默认待办数据库 |
| --- | --- |
| 第一篇离线演示 | `examples/data/demo.db`（命令显式指定） |
| 命令行与本地确认入口 | `examples/data/assistant.db` |
| 飞书与提醒 | `examples/data/feishu/assistant.db` |
| Linux 部署示例 | `/var/lib/xiaodazi/assistant.db` |

这些数据不会自动同步。命令行用 `--db` 指定文件；飞书和提醒用 `AGENT_DATA_DIR` 指定目录。换入口后清单为空，先核对路径。

## 项目做到哪里

本地地点是明确标注的教学虚构资料；联网以上海文旅官网为示例来源。小搭子保存的是候选和待办，不会预约或购票，也没有实时天气、路线、待办完成状态或跨任务聊天历史。每次任务需说完整，不能假定它理解“把刚才那个记下来”。

这是单人、单机的学习项目。六篇会区分程序检查与真实模型评估，解释失败和能力范围。当前代码在 Python 3.12.8 下通过 69 项本地测试；真实模型、飞书账号发送及服务器端到端联调仍待完成，不能据此宣称已经生产可用。具体证据集中在[验证记录](VALIDATION.md)。
