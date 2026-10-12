"""第六篇：单人待办提案。确认入口属于程序，不作为模型工具开放。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time

from assistant import Store

TTL_SECONDS = 600
ID_PATTERN = r'[0-9a-f]{16}'


def quoted(title):
    # 将换行等控制字符转义，避免标题伪装成预览中的另一条指令。
    return json.dumps(title, ensure_ascii=False)


def preview(row):
    deadline = datetime.fromtimestamp(row['expires_at'], timezone.utc).isoformat()
    return (f"待确认提案 {row['id']}（尚未写入待办）\n"
            f"操作：新增1条本地待办；同名待办合并\n内容：{quoted(row['title'])}\n"
            f"有效期至：{deadline}（UTC，创建后10分钟）\n"
            f"确认：/确认 {row['id']}\n取消：/取消 {row['id']}\n"
            "本次只处理这一条；其他事项尚未执行。修改内容请取消后重新提出。")


class Approvals:
    def __init__(self, path):
        self.path = Path(path)
        # 待办和提案必须在同一个 SQLite 文件，才能一起提交。
        store = Store(self.path)
        store.close()
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                decided_at REAL, task_id INTEGER, inserted INTEGER)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def propose(self, owner, title, now=None):
        now = time.time() if now is None else now
        if (not owner or not isinstance(title, str) or not title.strip()
                or len(title.strip()) > 200 or not title.isprintable()):
            raise ValueError('需要固定用户身份和1–200字符的单行待办标题，不能包含控制字符')
        row = dict(id=secrets.token_hex(8), owner=owner, title=title.strip(),
                   status='pending', created_at=now, expires_at=now + TTL_SECONDS)
        with self.connect() as db:
            db.execute('INSERT INTO proposals(id,owner,title,status,created_at,expires_at) '
                       'VALUES (:id,:owner,:title,:status,:created_at,:expires_at)', row)
        return {**row, 'preview': preview(row)}

    def decide(self, owner, proposal_id, action, now=None):
        if action not in ('confirm', 'cancel'):
            raise ValueError('只支持 confirm / cancel')
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM proposals WHERE id=? AND owner=?',
                             (proposal_id, owner)).fetchone()
            if not row:
                return {'status': 'not_found'}
            if row['status'] != 'pending':
                return dict(row)
            if now >= row['expires_at']:
                db.execute("UPDATE proposals SET status='expired', decided_at=? WHERE id=?", (now, proposal_id))
            elif action == 'cancel':
                db.execute("UPDATE proposals SET status='cancelled', decided_at=? WHERE id=?", (now, proposal_id))
            else:
                # 只执行数据库中保存的标题，不接受确认请求另带的新参数。
                cursor = db.execute('INSERT OR IGNORE INTO tasks(title) VALUES (?)', (row['title'],))
                inserted = int(cursor.rowcount == 1)
                task_id = db.execute('SELECT id FROM tasks WHERE title=?', (row['title'],)).fetchone()['id']
                db.execute("UPDATE proposals SET status='applied', decided_at=?, task_id=?, inserted=? WHERE id=?",
                           (now, task_id, inserted, proposal_id))
            return dict(db.execute('SELECT * FROM proposals WHERE id=?', (proposal_id,)).fetchone())

    def records(self, owner, pending=False, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("UPDATE proposals SET status='expired', decided_at=? WHERE owner=? AND status='pending' AND expires_at<=?",
                       (now, owner, now))
            query = 'SELECT * FROM proposals WHERE owner=?'
            if pending:
                query += " AND status='pending'"
            # 控制飞书回复长度，最多展示最近3条；旧记录仍保留在本地库中。
            return [dict(row) for row in db.execute(query + ' ORDER BY created_at DESC, rowid DESC LIMIT 3', (owner,))]


def receipt(row):
    status = row['status']
    if status == 'not_found':
        return '未找到属于当前用户的提案，请核对完整ID。'
    detail = f"提案 {row['id']}；内容：{quoted(row['title'])}"
    if status == 'applied':
        outcome = '已新增' if row['inserted'] else '同名待办已存在，未重复新增'
        return f"{detail}\n状态：已执行；{outcome}，待办编号 {row['task_id']}。再次确认不会重复执行。"
    if status == 'cancelled':
        return f'{detail}\n状态：已取消，不能再确认。'
    if status == 'expired':
        return f'{detail}\n状态：已过期，请重新提出操作并检查新预览。'
    return preview(row)


HELP = '用法：/待确认、/操作记录、/确认 完整ID、/取消 完整ID。不会将“好的”或“都同意”当作确认。'


def handle_approval_command(text, approvals, owner, now=None):
    """只在已验证用户身份的输入入口调用；永远不要传入模型输出或笔记。"""
    if not text.startswith(('/确认', '/取消', '/待确认', '/操作记录')):
        return None
    if text == '/待确认':
        rows = approvals.records(owner, pending=True, now=now)
        return '\n\n'.join(preview(row) for row in rows) if rows else '没有待确认提案。'
    if text == '/操作记录':
        rows = approvals.records(owner, now=now)
        return '\n\n'.join(receipt(row) for row in rows) if rows else '没有操作记录。'
    match = re.fullmatch(r'/(' + '确认|取消' + r') (' + ID_PATTERN + r')', text)
    if not match:
        return HELP
    action = 'confirm' if match.group(1) == '确认' else 'cancel'
    return receipt(approvals.decide(owner, match.group(2), action, now=now))
