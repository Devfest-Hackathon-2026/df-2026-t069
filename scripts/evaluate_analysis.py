"""架空会話を/analyzeで評価する。既定は検証のみ。--run指定時だけAPIを実行する。"""

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def append_record(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def summarize(records):
    valid = [r for r in records if r["http_status"] == 200 and r["result"] is not None]
    highs = [r for r in records if r["expected"]["risk_level"] == "high"]
    lows = [r for r in records if r["expected"]["risk_level"] == "low"
            and not r["expected"].get("is_suspicious", False)]
    low_attentions = [r for r in records if r["expected"]["risk_level"] == "low"
                      and r["expected"].get("is_suspicious", False)]
    mediums = [r for r in records if r["expected"]["risk_level"] == "medium"]

    def missed(record):
        return not record["result"] or record["result"]["risk_level"] != "high"

    return {
        "cases": len(records),
        "valid_responses": len(valid),
        "technical_failures": len(records) - len(valid),
        "high_cases": len(highs),
        "high_warning_misses_including_errors": sum(map(missed, highs)),
        "low_cases": len(lows),
        "low_attention_cases": len(low_attentions),
        "low_attention_misses_including_errors": sum(
            not r["result"] or not r["result"]["is_suspicious"] for r in low_attentions
        ),
        "low_false_warnings": sum(
            bool(r["result"]) and r["result"]["risk_level"] == "high" for r in lows
        ),
        "low_false_suspicious": sum(
            bool(r["result"]) and r["result"]["is_suspicious"] for r in lows
        ),
        "medium_cases": len(mediums),
        "medium_false_warnings": sum(
            bool(r["result"]) and r["result"]["risk_level"] == "high" for r in mediums
        ),
        "risk_matches": sum(
            r["result"]["risk_level"] == r["expected"]["risk_level"] for r in valid
        ),
        "category_matches": sum(
            r["result"]["category"] == r["expected"]["category"] for r in valid
        ),
        "usage": {
            key: sum(
                (u.get("usage_metadata") or {}).get(key) or 0 for r in records for u in r["usage"]
            )
            for key in (
                "prompt_token_count",
                "candidates_token_count",
                "thoughts_token_count",
                "total_token_count",
            )
        },
    }


class UsageRecorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        if hasattr(record, "analysis_usage"):
            self.records.append(record.analysis_usage)


def load_cases(path):
    from app.schemas.analysis import AnalyzeRequest

    dataset = json.loads(path.read_text())
    cases = dataset["cases"]
    if dataset.get("synthetic_only") is not True:
        raise ValueError("Only synthetic datasets are supported")
    seen = set()
    for case in cases:
        request = AnalyzeRequest.model_validate(case["request"])
        expected = case["expected"]
        if case["case_id"] in seen or len(request.utterances) != 6:
            raise ValueError("Case IDs must be unique and each case must contain six utterances")
        seen.add(case["case_id"])
        if len({u.id for u in request.utterances}) != 6:
            raise ValueError("Utterance IDs must be unique")
        risk = expected["risk_level"]
        if risk not in {"low", "medium", "high"}:
            raise ValueError("Invalid expected risk")
        if risk != "low" and not expected["is_suspicious"]:
            raise ValueError("Inconsistent expected suspicious flag")
        if expected["immediate_alert"] != (risk == "high"):
            raise ValueError("Inconsistent expected warning")
        if (not expected["is_suspicious"] and expected["category"] is not None) or (
            expected["is_suspicious"]
            and expected["category"] not in {"C1", "C2", "C3", "C4", "C5"}
        ):
            raise ValueError("Invalid expected category")
        if case["split"] not in {"tuning", "holdout"}:
            raise ValueError("Invalid evaluation split")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="実行する。vertexaiは課金対象")
    parser.add_argument("--backend", choices=["mock", "vertexai"], default="vertexai")
    parser.add_argument("--split", choices=["tuning", "holdout"], default="tuning")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tag", default="baseline")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    dataset_path = ROOT / "samples/conversations/analyze-cases.json"
    all_cases = load_cases(dataset_path)
    cases = [c for c in all_cases if c["split"] == args.split]
    if args.limit is not None:
        cases = cases[: args.limit]
    print(f"Validated {len(all_cases)} cases; selected {len(cases)} ({args.split}).")
    if not args.run:
        print("Validation only: no Gemini/Firestore calls. Add --run to execute.")
        return 0

    from app.core.config import get_settings
    from app.schemas.analysis import AnalyzeResponse
    from app.services.analysis_service import SYSTEM_INSTRUCTION
    from dotenv import load_dotenv
    from fastapi.testclient import TestClient

    load_dotenv(ROOT / ".env")
    os.environ["ANALYSIS_BACKEND"] = args.backend
    os.environ["ANALYSIS_STATE_BACKEND"] = "sqlite"
    os.environ["ANALYSIS_CONTEXT_UTTERANCES"] = "5"
    os.environ["BACKEND_API_KEY"] = "synthetic-evaluation-key"
    get_settings.cache_clear()
    settings = get_settings()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "output/evaluation" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "prompt.txt").write_text(SYSTEM_INSTRUCTION)
    metadata = {
        "run_id": run_id,
        "tag": args.tag,
        "backend": args.backend,
        "split": args.split,
        "model": settings.gemini_model,
        "location": settings.gcp_location,
        "max_retries": settings.analysis_max_retries,
        "case_count": len(cases),
        "prompt_sha256": hashlib.sha256(SYSTEM_INSTRUCTION.encode()).hexdigest(),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(
            (ROOT / "backend/app/schemas/analysis.py").read_bytes(),
        ).hexdigest(),
        "mock_is_not_accuracy_evaluation": args.backend == "mock",
    }
    (directory / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    recorder = UsageRecorder()
    logging.getLogger("app.services.analysis_service").addHandler(recorder)
    records = []
    with tempfile.TemporaryDirectory(prefix="waiwai-evaluation-") as temporary:
        os.environ["ANALYSIS_STATE_PATH"] = str(Path(temporary) / "state.sqlite3")
        get_settings.cache_clear()
        from app.main import create_app

        with TestClient(create_app()) as client:
            for case in cases:
                payload = {**case["request"], "sessionId": f"evaluation-{uuid4().hex}"}
                base = {"case_id": case["case_id"], "expected": case["expected"]}
                append_record(
                    directory / "results.jsonl",
                    {
                        **base,
                        "event": "started",
                        "request": payload,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                    },
                )
                recorder.records.clear()
                started = perf_counter()
                status, result, error = None, None, None
                try:
                    response = client.post(
                        "/analyze",
                        headers={"X-API-Key": "synthetic-evaluation-key"},
                        json=payload,
                    )
                    status = response.status_code
                    if status == 200:
                        parsed = AnalyzeResponse.model_validate(response.json())
                        if (
                            args.backend == "vertexai"
                            and parsed.quote
                            and not any(
                                parsed.quote in u["text"] for u in payload["utterances"][-5:]
                            )
                        ):
                            raise ValueError("Quote not in submitted input")
                        result = parsed.model_dump()
                    else:
                        error = f"HTTP {status}"
                except Exception as exc:  # noqa: BLE001 - エラーも評価履歴へ保存
                    error = type(exc).__name__
                record = {
                    **base,
                    "event": "completed",
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "http_status": status,
                    "result": result,
                    "error": error,
                    "duration_ms": round((perf_counter() - started) * 1000, 1),
                    "usage": list(recorder.records),
                }
                append_record(directory / "results.jsonl", record)
                records.append(record)
                print(case["case_id"], status, result["risk_level"] if result else error)
    summary = {**metadata, **summarize(records)}
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    lines = [
        f"# Evaluation {run_id}",
        "",
        f"Backend: {args.backend}; split: {args.split}",
        "",
        "| Case | Expected | Actual | HTTP | ms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for record in records:
        actual = record["result"]["risk_level"] if record["result"] else "ERROR"
        lines.append(
            f"| {record['case_id']} | {record['expected']['risk_level']} | "
            f"{actual} | {record['http_status']} | {record['duration_ms']} |"
        )
    (directory / "results.md").write_text("\n".join(lines) + "\n")
    print("Saved:", directory)
    return 1 if summary["technical_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
