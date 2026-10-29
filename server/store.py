"""会话与消息的本地存储（SQLite，标准库实现，无额外依赖）。

两部分数据放在同一个数据库文件里：
- conversations / messages：给界面用的会话列表与聊天记录（每轮一条用户消息、一条助手消息，助手消息带行程 plan）；
- agent_sessions / agent_messages：OpenAI Agents SDK 的会话记忆（SQLiteSession 自建），供模型多轮引用上一轮的工具结果。
"""

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Optional

from agents import SQLiteSession

TITLE_MAX_LEN = 8
PREVIEW_MAX_LEN = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    user_id         TEXT,
    title           TEXT NOT NULL,
    preview         TEXT,
    created_at      INTEGER NOT NULL,
    last_message_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id, last_message_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    plan            TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, created_at);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clip(text: str, n: int) -> str:
    text = " ".join(re.sub(r"[*#>`|]+", "", text).split())  # 预览与标题去掉 Markdown 符号
    return text if len(text) <= n else text[:n] + "..."


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            if db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")  # 与 SDK 会话记忆的连接并发读写
            self._conn.executescript(_SCHEMA)

    # ---------- 写入 ----------

    def add_message(self, conversation_id: str, role: str, content: str, *, user_id: Optional[str] = None,
                    plan: Optional[dict] = None, message_id: Optional[str] = None) -> str:
        """追加一条消息；会话不存在时以第一条用户消息为标题创建。"""
        now = _now_ms()
        message_id = message_id or str(uuid.uuid4())
        with self._lock, self._conn:
            exists = self._conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not exists:
                self._conn.execute(
                    "INSERT INTO conversations (id, user_id, title, preview, created_at, last_message_at) "
                    "VALUES (?, ?, ?, NULL, ?, ?)",
                    (conversation_id, user_id, _clip(content, TITLE_MAX_LEN) if role == "user" else "新对话", now, now))
            self._conn.execute(
                "INSERT OR REPLACE INTO messages (id, conversation_id, role, content, plan, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, role, content,
                 json.dumps(plan, ensure_ascii=False) if plan else None, now))
            preview = content or ("（行程）" if plan else "")
            self._conn.execute(
                "UPDATE conversations SET last_message_at = ?, preview = COALESCE(NULLIF(?, ''), preview) WHERE id = ?",
                (now, _clip(preview, PREVIEW_MAX_LEN), conversation_id))
        return message_id

    # ---------- 读取 ----------

    def get_messages(self, conversation_id: str) -> list:
        rows = self._conn.execute(
            "SELECT id, role, content, plan, created_at FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at, rowid", (conversation_id,)).fetchall()
        result = []
        for r in rows:
            msg = {"id": r["id"], "role": r["role"], "content": r["content"], "timestamp": r["created_at"]}
            if r["plan"]:
                msg["plan"] = json.loads(r["plan"])
            result.append(msg)
        return result

    def list_conversations(self, user_id: str, limit: int = 20, offset: int = 0, order: str = "desc") -> tuple:
        """返回 (会话列表, 下一页游标或 None)。游标是偏移量的字符串形式。"""
        direction = "ASC" if order == "asc" else "DESC"
        rows = self._conn.execute(
            f"SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            f"FROM conversations c WHERE c.user_id = ? ORDER BY c.last_message_at {direction} LIMIT ? OFFSET ?",
            (user_id, limit + 1, offset)).fetchall()
        items = [{
            "id": r["id"], "title": r["title"], "preview": r["preview"], "lastMessageAt": r["last_message_at"],
            "createdAt": r["created_at"], "userId": r["user_id"], "messageCount": r["message_count"],
        } for r in rows[:limit]]
        return items, (str(offset + limit) if len(rows) > limit else None)

    # ---------- 删除 ----------

    def clear_messages(self, conversation_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self._clear_agent_memory(conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._clear_agent_memory(conversation_id)

    # ---------- 模型会话记忆 ----------

    def agent_session(self, conversation_id: str) -> SQLiteSession:
        return SQLiteSession(conversation_id, self.db_path)

    def _clear_agent_memory(self, conversation_id: str) -> None:
        with self._lock, self._conn:
            tables = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "agent_messages" in tables:
                self._conn.execute("DELETE FROM agent_messages WHERE session_id = ?", (conversation_id,))
            if "agent_sessions" in tables:
                self._conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (conversation_id,))
