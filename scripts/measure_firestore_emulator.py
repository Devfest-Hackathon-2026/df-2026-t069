"""40発言の分析状態管理の文書読み書きを実Emulatorで計測する。外部AIは使わない。"""

import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def save(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class Counts:
    """実SDKのRPCを置き換えずに委譲し、取得文書とコミット成功文書数を数える。"""

    def __init__(self, api):
        self.api = api
        self.lock = Lock()
        self.reads = 0
        self.writes = 0
        self.read_rpcs = 0
        self.commit_rpcs = 0
        self.original_get = api.batch_get_documents
        self.original_commit = api.commit

    def snapshot(self):
        with self.lock:
            return {
                "document_reads": self.reads,
                "document_writes": self.writes,
                "read_rpcs": self.read_rpcs,
                "commit_rpcs": self.commit_rpcs,
            }

    def read(self, *args, **kwargs):
        with self.lock:
            self.read_rpcs += 1
        for response in self.original_get(*args, **kwargs):
            if response.found.name or response.missing:
                with self.lock:
                    self.reads += 1
            yield response

    def commit(self, *args, **kwargs):
        request = kwargs.get("request", args[0] if args else {})
        writes = request.get("writes", []) if isinstance(request, dict) else request.writes
        with self.lock:
            self.commit_rpcs += 1
        response = self.original_commit(*args, **kwargs)
        with self.lock:
            self.writes += len(writes)
        return response


def is_running(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1:8787")
    parser.add_argument("--start-emulator", action="store_true")
    args = parser.parse_args()
    host = urlsplit(f"//{args.host}")
    if host.hostname != "127.0.0.1" or not host.port:
        parser.error("Only 127.0.0.1:<port> is allowed")
    os.environ.update(
        {
            "FIRESTORE_EMULATOR_HOST": args.host,
            "GCP_PROJECT_ID": "demo-waiwai-measurement",
            "GOOGLE_CLOUD_PROJECT": "demo-waiwai-measurement",
            "ANALYSIS_BACKEND": "mock",
            "ANALYSIS_STATE_BACKEND": "firestore",
            "ANALYSIS_FIRESTORE_DATABASE": "(default)",
            "ANALYSIS_CONTEXT_UTTERANCES": "5",
            "BACKEND_API_KEY": "emulator-measurement-key",
        }
    )
    directory = (
        ROOT / "output/emulator-measurements" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    directory.mkdir(parents=True, exist_ok=False)
    process = None
    output = None
    save(
        directory / "events.jsonl",
        {"event": "started", "host": args.host, "synthetic_data": True, "utterance_count": 40},
    )
    try:
        if args.start_emulator:
            if is_running(host.hostname, host.port):
                raise RuntimeError("Port is already in use; existing process will not be stopped")
            environment = dict(os.environ)
            java = Path("/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home")
            if java.exists():
                environment["JAVA_HOME"] = str(java)
                environment["PATH"] = str(java / "bin") + os.pathsep + environment["PATH"]
            output = (directory / "emulator.log").open("w", encoding="utf-8")
            gcloud = shutil.which("gcloud")
            if not gcloud:
                raise RuntimeError("gcloud is not installed")
            process = subprocess.Popen(  # noqa: S603 - 検証済みローカルhost、shellなし
                [
                    str(Path(gcloud).resolve()),
                    "emulators",
                    "firestore",
                    "start",
                    f"--host-port={args.host}",
                    "--project=demo-waiwai-measurement",
                    "--quiet",
                ],
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 60
            while not is_running(host.hostname, host.port):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Emulator failed to start; see emulator.log")
                time.sleep(0.1)
        elif not is_running(host.hostname, host.port):
            raise RuntimeError("Start Emulator first or use --start-emulator")

        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import create_app
        from app.services.analysis_window_store import _digest
        from app.services.firestore_analysis_store import (
            COLLECTION,
            FirestoreAnalysisWindowStore,
        )

        get_settings.cache_clear()
        store = FirestoreAnalysisWindowStore("demo-waiwai-measurement")
        counts = Counts(store.client._firestore_api)
        summaries = []
        with (
            patch.object(store.client._firestore_api, "batch_get_documents", counts.read),
            patch.object(store.client._firestore_api, "commit", counts.commit),
            patch(
                "app.services.analysis_service.genai.Client",
                Mock(
                    side_effect=AssertionError("Gemini must not be called"),
                ),
            ),
            TestClient(create_app(), headers={"X-API-Key": "emulator-measurement-key"}) as client,
        ):
            for scenario in ("five_at_a_time", "one_new_utterance_at_a_time"):
                session = f"synthetic-{uuid4().hex}"
                prior = counts.snapshot()
                requests = successes = waiting = 0
                end_points = range(5, 41, 5) if scenario == "five_at_a_time" else range(1, 41)
                for end in end_points:
                    start = end - 4 if scenario == "five_at_a_time" else 1
                    payload = {
                        "sessionId": session,
                        "utterances": [
                            {"id": f"u{i}", "text": f"合成の発言{i}", "startSec": i}
                            for i in range(start, end + 1)
                        ],
                    }
                    before = counts.snapshot()
                    response = client.post("/analyze", json=payload)
                    after = counts.snapshot()
                    requests += 1
                    successes += response.status_code == 200
                    waiting += response.status_code == 204
                    record = {
                        "event": "request_completed",
                        "scenario": scenario,
                        "last_utterance": end,
                        "http_status": response.status_code,
                        **{k: after[k] - before[k] for k in before},
                    }
                    save(directory / "events.jsonl", record)
                    if response.status_code not in (200, 204):
                        raise RuntimeError(f"Unexpected HTTP {response.status_code}")
                after = counts.snapshot()
                summary = {
                    "scenario": scenario,
                    "utterances": 40,
                    "api_requests": requests,
                    "analyses_mock": successes,
                    "waiting": waiting,
                    **{k: after[k] - prior[k] for k in prior},
                }
                # この検算の1読み取りは分析処理のカウントから除外する。
                snapshot = store.client.collection(COLLECTION).document(_digest(session)).get()
                state = snapshot.to_dict()
                summary["completed_ids_verified"] = len(state["completed_ids"])
                summary["verification_reads_excluded"] = 1
                assert summary["completed_ids_verified"] == 40
                assert successes == 8
                assert summary["document_writes"] == 16
                assert summary["document_reads"] == (16 if scenario == "five_at_a_time" else 44)
                summaries.append(summary)
                print(json.dumps(summary, ensure_ascii=False))
        result = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "emulator_host": args.host,
            "production_firestore_calls": 0,
            "gemini_calls": 0,
            "measurement": "successful document read responses and committed writes via SDK RPC",
            "not_a_billing_report": True,
            "scenarios": summaries,
        }
        (directory / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        save(directory / "events.jsonl", {"event": "completed", "summary": result})
        print("Saved:", directory)
        return 0
    except Exception as exc:
        save(directory / "events.jsonl", {"event": "failed", "error_type": type(exc).__name__})
        raise
    finally:
        if process is not None:
            try:
                connection = http.client.HTTPConnection(host.hostname, host.port, timeout=5)
                try:
                    connection.request("POST", "/shutdown", body=b"")
                    response = connection.getresponse()
                    response.read()
                finally:
                    connection.close()
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
        if output:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())
