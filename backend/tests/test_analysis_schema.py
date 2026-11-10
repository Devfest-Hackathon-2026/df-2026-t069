"""発言分析の入力検証とJSON契約のテスト。"""

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalyzeRequest, AnalyzeResponse


def test_analyze_request_json_round_trip():
    payload = {
        "sessionId": "session-001",
        "utterances": [{"id": "u-001", "text": "発言内容", "startSec": 12.5}],
    }
    request = AnalyzeRequest.model_validate(payload)
    assert request.model_dump(mode="json") == payload


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"sessionId": "", "utterances": []},
        {"sessionId": "s", "utterances": []},
        {"sessionId": "s", "utterances": [{"id": "", "text": "発言", "startSec": 0}]},
        {"sessionId": "s", "utterances": [{"id": "u", "text": "", "startSec": 0}]},
        {"sessionId": "s", "utterances": [{"id": "u", "text": "発言"}]},
        {"sessionId": "s", "utterances": [{"id": "u", "text": "発言", "startSec": -1}]},
        {"sessionId": "s", "utterances": [{"id": "u", "text": "発言", "startSec": "abc"}]},
        {"sessionId": "s", "utterances": [{"id": "u", "text": "発言", "startSec": float("inf")}]},
    ],
)
def test_analyze_request_rejects_invalid_input(payload):
    with pytest.raises(ValidationError):
        AnalyzeRequest.model_validate(payload)


@pytest.mark.parametrize("risk_level", ["medium", "high"])
@pytest.mark.parametrize("category", ["C1", "C2", "C3", "C4", "C5"])
def test_suspicious_response_accepts_each_category(risk_level, category):
    payload = {
        "is_suspicious": True, "risk_level": risk_level, "category": category,
        "quote": "確認対象の発言", "reason": "判定理由", "advice": "行動案内",
    }
    assert AnalyzeResponse.model_validate(payload).model_dump(mode="json") == payload


def test_low_response_requires_no_evidence():
    payload = {
        "is_suspicious": False, "risk_level": "low", "category": None,
        "quote": None, "reason": "根拠なし", "advice": "不明点を確認",
    }
    assert AnalyzeResponse.model_validate(payload).model_dump(mode="json") == payload


def test_low_concern_requires_real_evidence():
    payload = {
        "is_suspicious": True, "risk_level": "low", "category": "C1",
        "quote": "手続きの詳しい説明は後で", "reason": "手順の説明がまだありません",
        "advice": "支払う前に手順を確認してください",
    }
    assert AnalyzeResponse.model_validate(payload).model_dump(mode="json") == payload
    for updates in ({"category": None}, {"quote": None}, {"quote": " "}):
        with pytest.raises(ValidationError):
            AnalyzeResponse.model_validate({**payload, **updates})


@pytest.mark.parametrize("risk_level", ["low", "medium", "high"])
def test_response_rejects_mismatched_suspicious_flag(risk_level):
    with pytest.raises(ValidationError):
        AnalyzeResponse(
            is_suspicious=risk_level == "low", risk_level=risk_level,
            category=None if risk_level == "low" else "C1",
            quote=None if risk_level == "low" else "発言",
            reason="理由", advice="確認",
        )


@pytest.mark.parametrize("updates", [
    {"category": "C1"}, {"quote": "発言"},
])
def test_low_response_rejects_evidence(updates):
    payload = {
        "is_suspicious": False, "risk_level": "low", "category": None,
        "quote": None, "reason": "理由", "advice": "確認",
    }
    with pytest.raises(ValidationError):
        AnalyzeResponse.model_validate({**payload, **updates})


@pytest.mark.parametrize("risk_level", ["medium", "high"])
@pytest.mark.parametrize("updates", [
    {"category": None}, {"category": ""}, {"category": "C6"},
    {"category": "金銭の要求・振込の急かし"},
    {"quote": None}, {"quote": ""}, {"quote": " \n "},
    {"reason": ""}, {"reason": " "}, {"advice": ""}, {"advice": " "},
    {"risk_level": "critical"},
])
def test_suspicious_response_rejects_invalid_fields(risk_level, updates):
    payload = {
        "is_suspicious": True, "risk_level": risk_level, "category": "C1",
        "quote": "発言", "reason": "理由", "advice": "確認",
    }
    with pytest.raises(ValidationError):
        AnalyzeResponse.model_validate({**payload, **updates})
