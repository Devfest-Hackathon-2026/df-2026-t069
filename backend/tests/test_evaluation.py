"""評価データの入力契約と、失敗を隠さない集計を確認する。"""

import runpy
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVALUATION = runpy.run_path(str(ROOT / "scripts/evaluate_analysis.py"))


def test_dataset_counts_splits_and_evidence():
    cases = EVALUATION["load_cases"](ROOT / "samples/conversations/analyze-cases.json")
    assert Counter(c["expected"]["risk_level"] for c in cases) == {
        "high": 10, "low": 10, "medium": 5,
    }
    assert Counter(c["split"] for c in cases) == {"tuning": 16, "holdout": 9}
    for case in cases:
        if case["evidence_hint"]:
            assert any(case["evidence_hint"] in u["text"]
                       for u in case["request"]["utterances"][-5:])


def test_metrics_distinguish_misses_false_alerts_and_technical_failures():
    records = []
    for expected, actual in [
        ("high", "medium"), ("high", None), ("low", "high"),
        ("low", "medium"), ("medium", "high"), ("low", "low"),
    ]:
        records.append({
            "http_status": 200 if actual else 504,
            "expected": {"risk_level": expected, "category": None if expected == "low" else "C1"},
            "result": {"risk_level": actual, "is_suspicious": actual != "low",
                       "category": None if actual == "low" else "C1"} if actual else None,
            "usage": [],
        })
    summary = EVALUATION["summarize"](records)
    assert summary["cases"] == 6
    assert summary["valid_responses"] == 5
    assert summary["technical_failures"] == 1
    assert summary["high_warning_misses_including_errors"] == 2
    assert summary["low_false_warnings"] == 1
    assert summary["low_false_suspicious"] == 2
    assert summary["medium_false_warnings"] == 1
    assert summary["risk_matches"] == 1


def test_low_attention_is_not_counted_as_a_false_positive():
    summary = EVALUATION["summarize"]([{
        "http_status": 200,
        "expected": {"risk_level": "low", "is_suspicious": True, "category": "C1"},
        "result": {"risk_level": "low", "is_suspicious": True, "category": "C1"},
        "usage": [],
    }])
    assert summary["low_attention_cases"] == 1
    assert summary["low_cases"] == 0
    assert summary["low_false_suspicious"] == 0
    assert summary["low_attention_misses_including_errors"] == 0
    assert summary["risk_matches"] == 1


def test_low_attention_miss_is_not_hidden_by_matching_risk_level():
    summary = EVALUATION["summarize"]([{
        "http_status": 200,
        "expected": {"risk_level": "low", "is_suspicious": True, "category": "C1"},
        "result": {"risk_level": "low", "is_suspicious": False, "category": None},
        "usage": [],
    }])
    assert summary["low_attention_misses_including_errors"] == 1
