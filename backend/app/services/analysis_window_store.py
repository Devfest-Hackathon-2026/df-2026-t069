"""ローカルの分析済みIDと実行中予約をSQLiteで管理する。発言本文は保存しない。"""
# これは今後活用しない予定。

import hashlib
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AnalysisWindowStore:
    """同じDBを使うローカルプロセス間で予約を排他する。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS analysis_ids (
                    session TEXT NOT NULL,
                    utterance TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    expires_at REAL,
                    PRIMARY KEY (session, utterance)
                )
            """)
            yield connection

    def claim(
        self,
        session_id: str,
        ids: list[str],
        lease_seconds: float,
        *,
        initial_ready: bool = True,
    ) -> str | None:
        """1件でも分析済み・実行中のIDがあれば予約せず、全件新規の場合だけ予約する。"""
        if not ids or len(set(ids)) != len(ids):
            return None
        session = _digest(session_id)
        hashed_ids = [_digest(value) for value in ids]
        owner = uuid4().hex
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                not initial_ready
                and connection.execute(
                    "SELECT 1 FROM analysis_ids WHERE session=? AND expires_at IS NULL LIMIT 1",
                    (session,),
                ).fetchone()
                is None
            ):
                connection.rollback()
                return None
            for utterance in hashed_ids:
                row = connection.execute(
                    "SELECT expires_at FROM analysis_ids WHERE session=? AND utterance=?",
                    (session, utterance),
                ).fetchone()
                if row is not None and (row[0] is None or row[0] > now):
                    connection.rollback()
                    return None
            for utterance in hashed_ids:
                connection.execute(
                    "INSERT OR REPLACE INTO analysis_ids VALUES (?, ?, ?, ?)",
                    (session, utterance, owner, now + lease_seconds),
                )
            connection.commit()
        return owner

    def finish(self, owner: str, success: bool) -> None:
        with self._connection() as connection:
            if success:
                connection.execute(
                    "UPDATE analysis_ids SET expires_at=NULL WHERE owner=?",
                    (owner,),
                )
            else:
                connection.execute("DELETE FROM analysis_ids WHERE owner=?", (owner,))
            connection.commit()
