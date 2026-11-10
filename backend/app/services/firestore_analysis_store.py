"""Firestoreによる通話単位の分析予約。本文は保存せず、IDのハッシュだけを保持する。"""

import os
import secrets
import time
from functools import lru_cache
from uuid import uuid4

from google.api_core.exceptions import Aborted
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore

from app.services.analysis_window_store import _digest

# 通話ごとに保存するID数を制限し、1MiBのドキュメント上限に余裕を残す。
MAX_COMPLETED_IDS = 2000
COLLECTION = "analysis_states"


class AnalysisStateError(Exception):
    pass


@lru_cache(maxsize=8)
def _client(project: str, database: str, emulator_host: str = ""):
    credentials = AnonymousCredentials() if emulator_host else None
    return firestore.Client(project=project, database=database, credentials=credentials)


class FirestoreAnalysisWindowStore:
    def __init__(self, project: str, database: str = "(default)") -> None:
        if not project.strip():
            raise AnalysisStateError("Firestore project is required")
        self.client = _client(project, database, os.environ.get("FIRESTORE_EMULATOR_HOST", ""))

    def _run_transaction(self, operation):
        # 競合したトランザクションを毎回rollbackし、ロックを解放してから再試行する。
        for attempt in range(3):
            try:
                return operation(self.client.transaction(max_attempts=1))
            except ValueError as exc:
                if not isinstance(exc.__cause__, Aborted) or attempt == 2:
                    raise
                time.sleep(0.05 * (2 ** attempt) + secrets.randbelow(50) / 1000)

    def claim(
        self, session_id: str, ids: list[str], lease_seconds: float,
        *, initial_ready: bool = True,
    ) -> str | None:
        if not ids or len(set(ids)) != len(ids):
            return None
        session = _digest(session_id)
        hashed_ids = [_digest(value) for value in ids]
        owner = uuid4().hex
        ref = self.client.collection(COLLECTION).document(session)

        @firestore.transactional
        def reserve(transaction):
            snapshot = ref.get(transaction=transaction, timeout=3, retry=None)
            data = snapshot.to_dict() or {}
            completed = set(data.get("completed_ids", []))
            if not initial_ready and not completed:
                return None
            if completed.intersection(hashed_ids):
                return None
            pending = data.get("pending")
            if pending and pending["expires_at"] > time.time():
                return None
            if len(completed) + len(hashed_ids) > MAX_COMPLETED_IDS:
                # 古いIDを忘れて再課金しないよう、上限では503にして分析を止める。
                raise AnalysisStateError("Analysis state capacity exceeded")
            transaction.set(ref, {
                "completed_ids": sorted(completed),
                "pending": {"owner": owner, "ids": hashed_ids,
                            "expires_at": time.time() + lease_seconds},
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            return f"{session}:{owner}"

        return self._run_transaction(reserve)

    def finish(self, token: str, success: bool) -> None:
        session, owner = token.split(":", 1)
        ref = self.client.collection(COLLECTION).document(session)

        @firestore.transactional
        def complete(transaction):
            snapshot = ref.get(transaction=transaction, timeout=3, retry=None)
            data = snapshot.to_dict() or {}
            pending = data.get("pending")
            if not pending or pending["owner"] != owner:
                # 期限切れ後に別リクエストが取得した予約を変更しない。
                raise AnalysisStateError("Analysis reservation is no longer owned")
            completed = set(data.get("completed_ids", []))
            if success:
                completed.update(pending["ids"])
            transaction.set(ref, {
                "completed_ids": sorted(completed), "pending": None,
                "updated_at": firestore.SERVER_TIMESTAMP,
            })

        self._run_transaction(complete)
