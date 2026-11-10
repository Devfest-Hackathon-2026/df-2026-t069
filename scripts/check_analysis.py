"""合成発言でローカルAPI→Vertex AI→レスポンス検証を行う（API利用料金が発生）。"""

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")
# この確認プロセス内だけで利用するキー。ファイルや既存サーバーは変更しない。
os.environ["ANALYSIS_BACKEND"] = "vertexai"
os.environ["BACKEND_API_KEY"] = "local-analysis-smoke-test"
os.environ["ANALYSIS_MAX_RETRIES"] = "0"


class UsageRecorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        if hasattr(record, "analysis_usage"):
            self.records.append(record.analysis_usage)


def save_event(path, event):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["high", "low", "all"], default="all")
    args = parser.parse_args()
    history_dir = ROOT / "output" / "analysis-history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history = history_dir / "requests.jsonl"
    failures = 0
    recorder = UsageRecorder()
    logging.getLogger("app.services.analysis_service").addHandler(recorder)
    with TestClient(create_app()) as client:
        for name, text, expected in [
            ("high", "銀行の暗証番号を教えてください", "high"),
            ("low", "熱はいつからですか。今飲んでいる薬はありますか", "low"),
        ]:
            if args.case not in ("all", name):
                continue
            request_id = str(uuid4())
            payload = {
                "sessionId": f"synthetic-{request_id}",
                "utterances": [
                    {"id": f"{name}-{i}", "text": text, "startSec": i}
                    for i in range(get_settings().analysis_context_utterances)
                ],
            }
            recorder.records.clear()
            save_event(
                history,
                {
                    "event": "started",
                    "request_id": request_id,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "synthetic_data": True,
                    "request": payload,
                },
            )
            try:
                response = client.post(
                    "/analyze",
                    headers={"X-API-Key": "local-analysis-smoke-test"},
                    json=payload,
                )
                body = response.json() if response.content else None
                save_event(
                    history,
                    {
                        "event": "completed",
                        "request_id": request_id,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                        "http_status": response.status_code,
                        "response": body,
                        "usage": recorder.records,
                    },
                )
                print(name, response.status_code, body)
                if response.status_code != 200 or (body or {}).get("risk_level") != expected:
                    failures += 1
            except Exception as exc:  # noqa: BLE001 - 失敗履歴を保存し、終了コードで通知
                save_event(
                    history,
                    {
                        "event": "failed",
                        "request_id": request_id,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                        "error_type": type(exc).__name__,
                        "usage": recorder.records,
                    },
                )
                failures += 1
    print("History saved:", history)
    raise SystemExit(1 if failures else 0)
