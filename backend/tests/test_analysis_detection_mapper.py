"""分析JSONから保存JSONへの変換と、問題発言の開始時刻を検証する。"""

import pytest

from app.schemas.analysis import AnalyzeResponse, Utterance
from app.services.analysis_detection_mapper import to_detection_item
from app.services.session_service import SessionService


def analysis(level):
    return AnalyzeResponse(
        is_suspicious=True,
        risk_level=level,
        category="C1",
        quote="今すぐ振り込んで",
        reason="送金を急がせる要求",
        advice="公式窓口に確認してください",
    )


@pytest.mark.parametrize("level,score,verdict", [
    ("low", 0.25, "low"),
    ("medium", 0.5, "suspicious"),
    ("high", 0.85, "likely_fake"),
])
@pytest.mark.parametrize("start", [0.0, 12.5])
def test_conversion_and_session_storage(level, score, verdict, start):
    utterances = [
        Utterance(id="u1", text="それでは今すぐ振り込んでください", startSec=start),
        Utterance(id="u2", text="確認します", startSec=start + 3),
    ]
    item = to_detection_item(analysis(level), utterances, speaker="remote")
    assert item is not None
    assert item.timestamp_sec == start
    assert item.risk_score == score
    assert item.risk_level == verdict
    assert item.quote == "今すぐ振り込んで"
    assert item.speaker == "remote"

    service = SessionService()
    service.create_session("session")
    saved = service.record_detection("session", item)
    assert saved.overall_verdict == verdict
    assert saved.max_risk_score == score
    assert service.get_session("session").detections[0].timestamp_sec == start
    assert service.end_session("session").overall_verdict == verdict


def test_no_concern_is_not_a_detection():
    result = AnalyzeResponse(
        is_suspicious=False, risk_level="low", category=None, quote=None,
        reason="不審な要求はありません", advice="不明点があれば確認してください",
    )
    assert to_detection_item(result, []) is None


@pytest.mark.parametrize("texts", [[], ["通常の会話"], ["今すぐ振り込んで"] * 2])
def test_unidentifiable_quote_does_not_get_a_guessed_timestamp(texts):
    utterances = [Utterance(id=str(i), text=text, startSec=i * 3) for i, text in enumerate(texts)]
    with pytest.raises(ValueError, match="exactly one"):
        to_detection_item(analysis("high"), utterances)
