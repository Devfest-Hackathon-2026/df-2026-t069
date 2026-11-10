"""通話セッションおよび検知結果の永続化サービス。[Role2]

Issue #17: 通話分析結果の保存（Firestore）。
OOP設計原則に従い、Storeインターフェースを分離してインメモリとFirestoreを切り替え可能にする。
"""

import logging
import secrets
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from google.api_core.exceptions import Aborted

from app.schemas.session import (
    DetectionItem,
    DetectionSaveResponse,
    SessionEndResponse,
    SessionResponse,
    SessionVerdict,
)

logger = logging.getLogger(__name__)

# エディタ警告防止のための try-except インポート
try:
    from google.cloud import firestore  # type: ignore

    HAS_FIRESTORE = True
except ImportError:
    firestore = None  # type: ignore
    HAS_FIRESTORE = False


class BaseSessionStore(ABC):
    """セッション永続化の抽象基底クラス (Repository Pattern)。"""

    @abstractmethod
    def save_session(self, session: SessionResponse) -> None:
        """セッション情報を保存または更新する。"""
        raise NotImplementedError

    @abstractmethod
    def get_session(self, session_id: str) -> SessionResponse | None:
        """指定されたセッションIDの情報を取得する。"""
        raise NotImplementedError

    @abstractmethod
    def add_detection(
        self, session_id: str, detection: DetectionItem
    ) -> DetectionSaveResponse | None:
        """セッションに検知レコードを追加し、軽量保存結果を返す。"""
        raise NotImplementedError

    @abstractmethod
    def end_session(
        self, session_id: str, ended_at: datetime, analysis_incomplete: bool = False
    ) -> SessionEndResponse | None:
        """セッションを終了状態にし、軽量終了結果を返す。"""
        raise NotImplementedError


class InMemorySessionStore(BaseSessionStore):
    """ローカル開発・テスト用のインメモリセッションストア。"""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionResponse] = {}
        self._lock = RLock()

    def save_session(self, session: SessionResponse) -> None:
        self._sessions[session.session_id] = session.model_copy(deep=True)

    def get_session(self, session_id: str) -> SessionResponse | None:
        session = self._sessions.get(session_id)
        return session.model_copy(deep=True) if session else None

    def add_detection(
        self, session_id: str, detection: DetectionItem
    ) -> DetectionSaveResponse | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            if not detection.expire_at:
                detection = detection.model_copy(update={"expire_at": session.expire_at})

            existing = next((d for d in session.detections if d.id == detection.id), None)
            if existing is not None:
                return DetectionSaveResponse(
                    id=existing.id, session_id=session_id, status="saved",
                    detections_count=session.detections_count,
                    max_risk_score=session.max_risk_score,
                    overall_verdict=session.overall_verdict,
                )

            updated_detections = list(session.detections) + [detection]
            new_max_score = max(session.max_risk_score, detection.risk_score)

            new_verdict = session.overall_verdict
            if new_max_score >= 0.7:
                new_verdict = SessionVerdict.LIKELY_FAKE
            elif new_max_score >= 0.4:
                new_verdict = SessionVerdict.SUSPICIOUS
            elif new_max_score > 0:
                new_verdict = SessionVerdict.LOW

            self._sessions[session_id] = session.model_copy(
                update={
                    "detections": updated_detections,
                    "detections_count": len(updated_detections),
                    "max_risk_score": new_max_score,
                    "overall_verdict": new_verdict,
                }
            )
            return DetectionSaveResponse(
                id=detection.id,
                session_id=session_id,
                status="saved",
                detections_count=len(updated_detections),
                max_risk_score=new_max_score,
                overall_verdict=new_verdict,
            )

    def end_session(
        self, session_id: str, ended_at: datetime, analysis_incomplete: bool = False
    ) -> SessionEndResponse | None:
        session = self._sessions.get(session_id)
        if not session:
            return None

        updated_session = session.model_copy(update={
            "ended_at": ended_at,
            "analysis_incomplete": session.analysis_incomplete or analysis_incomplete,
        })
        self._sessions[session_id] = updated_session
        return SessionEndResponse(
            session_id=session_id,
            ended_at=ended_at,
            status="ended",
            detections_count=updated_session.detections_count,
            max_risk_score=updated_session.max_risk_score,
            overall_verdict=updated_session.overall_verdict,
        )


class FirestoreSessionStore(BaseSessionStore):
    """Google Cloud Firestore を利用した本番用永続化ストア。"""

    def __init__(self, project_id: str | None = None) -> None:
        if not HAS_FIRESTORE:
            raise RuntimeError("google-cloud-firestore がインストールされていません。")
        self._db = firestore.Client(project=project_id)  # type: ignore
        self._collection_name = "sessions"

    def _doc_ref(self, session_id: str) -> Any:
        return self._db.collection(self._collection_name).document(session_id)

    def save_session(self, session: SessionResponse) -> None:
        doc = self._doc_ref(session.session_id)
        doc.set(
            {
                "session_id": session.session_id,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "max_risk_score": session.max_risk_score,
                "overall_verdict": session.overall_verdict.value,
                "detections_count": session.detections_count,
                "expire_at": session.expire_at,
            }
        )
        # サブコレクションに detections を追加
        detections_col = doc.collection("detections")
        for item in session.detections:
            detections_col.document(item.id).set(item.model_dump())

    def get_session(self, session_id: str) -> SessionResponse | None:
        doc = self._doc_ref(session_id)
        snapshot = doc.get()
        if not snapshot.exists:
            return None

        data = snapshot.to_dict()
        detections: list[DetectionItem] = []
        for det_doc in doc.collection("detections").stream():
            detections.append(DetectionItem(**det_doc.to_dict()))

        detections.sort(key=lambda d: d.timestamp_sec)

        return SessionResponse(
            session_id=data["session_id"],
            started_at=data["started_at"],
            ended_at=data.get("ended_at"),
            analysis_incomplete=data.get("analysis_incomplete", False),
            max_risk_score=data.get("max_risk_score", 0.0),
            overall_verdict=SessionVerdict(data.get("overall_verdict", "authentic")),
            detections_count=data.get("detections_count", len(detections)),
            expire_at=data["expire_at"],
            detections=detections,
        )

    def add_detection(
        self, session_id: str, detection: DetectionItem
    ) -> DetectionSaveResponse | None:
        doc = self._doc_ref(session_id)
        detection_ref = doc.collection("detections").document(detection.id)

        @firestore.transactional
        def save(transaction):
            snapshot = doc.get(transaction=transaction, timeout=3, retry=None)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            existing = detection_ref.get(transaction=transaction, timeout=3, retry=None)
            new_max = data.get("max_risk_score", 0.0)
            new_verdict = SessionVerdict(data.get("overall_verdict", "authentic"))
            count = data.get("detections_count", 0)
            if not existing.exists:
                new_max = max(new_max, detection.risk_score)
                if new_max >= 0.7:
                    new_verdict = SessionVerdict.LIKELY_FAKE
                elif new_max >= 0.4:
                    new_verdict = SessionVerdict.SUSPICIOUS
                elif new_max > 0:
                    new_verdict = SessionVerdict.LOW
                count += 1
                stored = detection.model_copy(update={"expire_at": data["expire_at"]})
                transaction.update(doc, {
                    "max_risk_score": new_max,
                    "overall_verdict": new_verdict.value,
                    "detections_count": count,
                })
                transaction.set(detection_ref, stored.model_dump())
            return DetectionSaveResponse(
                id=detection.id, session_id=session_id, status="saved",
                detections_count=count, max_risk_score=new_max,
                overall_verdict=new_verdict,
            )

        # 競合時はrollback済みの新しいトランザクションで再試行する。
        for attempt in range(3):
            try:
                return save(self._db.transaction(max_attempts=1))
            except ValueError as exc:
                if not isinstance(exc.__cause__, Aborted) or attempt == 2:
                    raise
                time.sleep(0.05 * (2 ** attempt) + secrets.randbelow(50) / 1000)

    def end_session(
        self, session_id: str, ended_at: datetime, analysis_incomplete: bool = False
    ) -> SessionEndResponse | None:
        doc = self._doc_ref(session_id)
        snapshot = doc.get()
        if not snapshot.exists:
            return None

        data = snapshot.to_dict()
        doc.update({
            "ended_at": ended_at,
            "analysis_incomplete": data.get("analysis_incomplete", False) or analysis_incomplete,
        })

        # 全件ストリーム読み取り(get_session)を廃止し、O(1)軽量レスポンスを返す
        return SessionEndResponse(
            session_id=session_id,
            ended_at=ended_at,
            status="ended",
            detections_count=data.get("detections_count", 0),
            max_risk_score=data.get("max_risk_score", 0.0),
            overall_verdict=SessionVerdict(
                data.get("overall_verdict", SessionVerdict.AUTHENTIC.value),
            ),
        )


class SessionService:
    """通話セッションおよび検知結果のビジネスロジックを統括するサービスクラス。"""

    DEFAULT_TTL_DAYS = 7

    def __init__(self, store: BaseSessionStore | None = None) -> None:
        self._store = store or InMemorySessionStore()

    def create_session(
        self, session_id: str, started_at: datetime | None = None
    ) -> SessionResponse:
        """新規セッションを作成する（TTLを7日後に設定）。"""
        now = started_at or datetime.now(timezone.utc)
        expire_at = now + timedelta(days=self.DEFAULT_TTL_DAYS)

        session = SessionResponse(
            session_id=session_id,
            started_at=now,
            expire_at=expire_at,
        )
        self._store.save_session(session)
        logger.info("Session %s created (expire_at: %s)", session_id, expire_at)
        return session

    def record_detection(
        self, session_id: str, detection: DetectionItem
    ) -> DetectionSaveResponse:
        """セッションに検知結果を記録する（O(1)軽量保存・事前/事後の全件再取得ゼロ）。"""
        # AI出力長文ガード（Pydantic文字数バリデーション上限に安全に収める）
        if len(detection.quote) > 1000:
            detection = detection.model_copy(update={"quote": detection.quote[:1000]})
        if len(detection.reason) > 1000:
            detection = detection.model_copy(update={"reason": detection.reason[:1000]})
        if detection.advice and len(detection.advice) > 500:
            detection = detection.model_copy(update={"advice": detection.advice[:500]})

        # 事前の全件読み取りを廃止し、add_detection 内で存在確認とTTL付与を実施
        updated = self._store.add_detection(session_id, detection)
        if not updated:
            raise KeyError(f"Session '{session_id}' not found.")
        return updated

    def end_session(
        self, session_id: str, ended_at: datetime | None = None,
        analysis_incomplete: bool = False,
    ) -> SessionEndResponse:
        """セッションを終了する（O(1)軽量終了・全件再取得ゼロ）。"""
        now = ended_at or datetime.now(timezone.utc)
        updated = self._store.end_session(session_id, now, analysis_incomplete=analysis_incomplete)
        if not updated:
            raise KeyError(f"Session '{session_id}' not found.")
        logger.info("Session %s ended at %s", session_id, now)
        return updated

    def get_session(self, session_id: str) -> SessionResponse:
        """セッション情報を取得する（通話後レポート用）。"""
        session = self._store.get_session(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found.")
        return session

