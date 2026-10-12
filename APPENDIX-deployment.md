# 番外：让小搭子持续在线——部署到 Linux（附录 B）

这是一篇可选的部署实战。前六篇已经让小搭子学会记待办、读资料、接受飞书消息和按时提醒；如果你希望个人电脑关机后仍能使用它，再将程序搬到常开主机。

不部署也能继续跟做主线，只要使用时本机程序保持运行。本篇使用 Codex 辅助准备和检查部署配置，不要求你先学习容器或购买新的服务器。[完整文集与配套代码](README.md)。

## 1. 先确认本机已经跑通

下面以一台**已准备好、你有管理权限的 Debian 或 Ubuntu 主机**为例。这些命令应在服务器执行；本机 macOS、Windows 不使用 systemd。教程只提供配置，不会替你购买机器或操作云账号。

开始前先完成第四篇的本机回声、真实模型与手机查询验收。若这些还没通过，搬到服务器只会增加排查变量。

## 2. 让 Codex 适配部署环境

先让 Codex 帮你适配已有主机：

```text
我已经有一台 Debian/Ubuntu 主机，准备部署现有的飞书长连接 Agent。
请根据 examples/deploy/xiaodazi.service 准备部署步骤。
代码放 /opt/xiaodazi，数据放 /var/lib/xiaodazi，凭据放 /etc/xiaodazi.env。
使用独立的 xiaodazi 用户运行；异常退出后重启；日志进入 journal。
程序只向外建立连接，不新增 HTTP 监听端口。
先检查 Python 与目录条件，给我可审阅的命令和停用方式。
不要购买资源，不要把本地虚拟环境或密钥提交到 Git。
```

通过你惯用的 SSH 文件传输方式，把本项目的 `examples` 目录上传到服务器的 `/opt/xiaodazi/examples`。不要复制本机 `.venv`；macOS 上的虚拟环境不能直接当作 Linux 环境使用。首次部署也先不上传本机 `data/`，保持测试数据分离。

## 3. 安装依赖并配置凭据

完成上传后，服务器上可执行以下准备步骤。假设 `xiaodazi` 用户尚不存在；若已存在，跳过创建用户一行：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv
sudo useradd --system --home-dir /var/lib/xiaodazi --shell /usr/sbin/nologin xiaodazi
sudo python3 -m venv /opt/xiaodazi/examples/.venv
sudo /opt/xiaodazi/examples/.venv/bin/python -m pip install -r /opt/xiaodazi/examples/requirements-feishu.txt
sudo install -d -o xiaodazi -g xiaodazi -m 700 /var/lib/xiaodazi
sudo install -o root -g root -m 600 /opt/xiaodazi/examples/deploy/xiaodazi.env.example /etc/xiaodazi.env
sudo nano /etc/xiaodazi.env
```

在编辑器里填入真实的飞书凭据、自己的 `open_id` 和[附录 A](APPENDIX-model-api.md)中已跑通的一套模型配置。模板中的业务空间、密钥都是占位符，直接启动不能完成真实任务。如果是在更新已有部署，不要再次用模板覆盖现有凭据文件。

## 4. 启动服务，从手机验收

再安装并启动服务：

```bash
sudo install -m 644 /opt/xiaodazi/examples/deploy/xiaodazi.service /etc/systemd/system/xiaodazi.service
sudo systemctl daemon-reload
sudo systemctl enable --now xiaodazi
sudo systemctl status xiaodazi --no-pager
sudo journalctl -u xiaodazi -n 50 --no-pager
```

服务文件指定了运行用户、虚拟环境解释器、凭据文件和数据目录，并在异常退出后重启。日志能帮助定位启动失败，但不要把整份凭据文件贴进日志或聊天。[参考：Ubuntu 的 systemd 服务配置手册](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html)

**服务器启动前，停止个人电脑上的同一机器人实例。** 然后从手机重新发送一条查询待办的消息。服务器版使用自己的数据库，初次清单为空属于正常现象。

需要查看服务器数据时，要使用服务中的目录和运行用户。例如：

```bash
sudo -u xiaodazi env AGENT_DATA_DIR=/var/lib/xiaodazi /opt/xiaodazi/examples/.venv/bin/python /opt/xiaodazi/examples/feishu_bot.py --jobs
sudo -u xiaodazi /opt/xiaodazi/examples/.venv/bin/python /opt/xiaodazi/examples/assistant.py --inspect --db /var/lib/xiaodazi/assistant.db
```

## 5. 停用、排查与数据迁移

暂停服务与取消开机启动：

```bash
sudo systemctl disable --now xiaodazi
```

`Restart=on-failure` 负责异常退出，不能保证发现所有“进程还在但连接异常”的情况。是否真正可用，最终还要从手机发送测试消息确认。

想迁移旧待办时，先停止本机和服务器的写入，再备份并复制数据；不要让两个实例同时写同一个 SQLite 文件。服务器上的 `assistant.db` 保存待办、偏好和第六篇的提案，`inbox.db` 保存接收任务与答案；第五篇的 `reminders.db` 保存提醒设置与发送记录。它们都包含个人内容，备份与保留周期需要自己管理。

## 6. 已有提醒和确认模式，怎样接着用

第五篇的提醒调度就在同一个飞书进程中，不需要再启动一套定时服务。服务器默认使用自己的数据；首次部署后，请按第五篇步骤从手机查看并开启提醒，再做一次真实发送验收。

如果希望写入待办前先确认，还需要将服务启动命令改为带 `--confirm-writes` 的形式，并从手机测试提案与确认。默认部署不会自动开启这一模式；覆盖配置和验收步骤见[第六篇：接入飞书确认模式](06-boundaries-and-confirmation.md)。同一机器人只运行一个实例。

## 本篇完成标准

- 服务正常启动，手机查询能收到回答。
- 关闭个人电脑后，服务器上的机器人仍能响应。
- 知道如何查看日志、停用服务，以及数据保存在什么位置。
- 如开启提醒或确认模式，分别完成对应的手机端验收。

本文提供部署步骤与配置示例，尚未在真实服务器上完成端到端联调；服务状态显示运行中，也不能代替手机消息验收。[验证范围与记录](VALIDATION.md)。

[第四篇：飞书入口](04-agent-on-your-phone.md) · [第五篇：定时提醒](05-scheduled-reminders.md) · [完整文集与配套代码](README.md)
