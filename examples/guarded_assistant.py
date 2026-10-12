"""第六篇：需要明确确认才能添加待办的命令行入口。"""
import argparse
import os
from pathlib import Path
import sys

from assistant import DemoClient, ROOT, Store, make_client, run_agent
from approvals import Approvals, handle_approval_command


LOCAL_OWNER = 'local-user'  # 单人本机入口，不是多用户登录系统。


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', nargs='?')
    parser.add_argument('--demo', action='store_true', help='固定脚本生成一条提案，不调用模型')
    parser.add_argument('--web', action='store_true', help='允许联网搜索和读取上海文旅官网资料')
    parser.add_argument('--db', type=Path, default=ROOT / 'data/assistant.db')
    args = parser.parse_args()
    if bool(args.task) == args.demo:
        parser.error('请提供一个任务或管理命令，或单独使用 --demo')
    approvals = Approvals(args.db)
    store = Store(args.db)
    client = None
    try:
        command = handle_approval_command(args.task or '', approvals, LOCAL_OWNER)
        if command is not None:
            print(command)
            return
        client = DemoClient() if args.demo else make_client()
        from web_tools import WebTools
        result = run_agent(client, os.getenv('OPENAI_MODEL', 'demo'),
                           args.task or '记下：周六去公园散步', store, version=2,
                           skill=bool(args.task and args.task.startswith('周末计划：')),
                           write_gate=lambda title: approvals.propose(LOCAL_OWNER, title),
                           web=WebTools() if args.web else None)
        print(result['answer'])
        print('[状态] ' + result['status'])
        if result['status'] not in ('completed', 'needs_confirmation'):
            raise SystemExit(1)
    except Exception as exc:
        print(f'处理失败：{type(exc).__name__}。请用 /待确认 或 /操作记录 核对，不要猜测执行结果。', file=sys.stderr)
        raise SystemExit(1)
    finally:
        store.close()
        if client is not None and hasattr(client, 'close'):
            client.close()


if __name__ == '__main__':
    main()
