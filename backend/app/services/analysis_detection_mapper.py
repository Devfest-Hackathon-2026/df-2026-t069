"""発言分析結果をセッション保存用レコードに変換する。"""

from app.schemas.analysis import AnalyzeResponse, Utterance
from app.schemas.session import DetectionItem, SessionVerdict

# 確率ではなく危険度区分を表す固定値。保存側の0.4/0.7の境界に対応する。
RISK_MAPPING = {
    "low": (0.25, SessionVerdict.LOW),
    "medium": (0.5, SessionVerdict.SUSPICIOUS),
    "high": (0.85, SessionVerdict.LIKELY_FAKE),
}


def to_detection_item(
    result: AnalyzeResponse,
    utterances: list[Utterance],
    *,
    speaker: str | None = None,
) -> DetectionItem | None:
    """注意の根拠がある結果を保存用に変換し、根拠なしは保存しない。

    startSecは録音開始を0秒とする発言開始位置。引用が発言の途中でも
    その発言の開始位置を使用し、文字数から時刻を推測しない。
    引用元が存在しない・複数ある場合は誤った時刻を保存せずエラーにする。
    """
    if not result.is_suspicious:
        return None

    matches = [u for u in utterances if result.quote is not None and result.quote in u.text]
    if len(matches) != 1:
        raise ValueError("Analysis quote must identify exactly one source utterance")

    score, verdict = RISK_MAPPING[result.risk_level]
    return DetectionItem(
        timestamp_sec=matches[0].startSec,
        speaker=speaker,
        quote=result.quote,
        category=result.category,
        risk_score=score,
        risk_level=verdict.value,
        reason=result.reason,
        advice=result.advice,
    )
