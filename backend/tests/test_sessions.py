"""セッション管理・分析結果保存 API のテスト。[Role2]

Issue #17: 通話分析結果の保存とレポート取得エンドポイントの検証。
"""

from datetime import datetime, timezone

try:
    from fastapi.testclient import TestClient  # type: ignore
except ImportError:
    TestClient = None  # type: ignore

from app.main import create_app

app = create_app()
client = TestClient(app) if TestClient else None


def test_session_lifecycle() -> None:
    if not client:
        return  # ライブラリ未インストール環境ではスキップ

    session_id = f"test-session-{datetime.now(timezone.utc).timestamp()}"

    # 1. セッション開始
    create_res = client.post(
        "/api/v1/sessions",
        json={"session_id": session_id},
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert created_data["session_id"] == session_id
    assert created_data["max_risk_score"] == 0.0
    assert created_data["overall_verdict"] == "authentic"
    assert created_data["detections_count"] == 0
    assert "expire_at" in created_data

    # 2. 検知結果の追加 (1件目: 低〜中リスク -> O(1) 軽量レスポンス)
    det1 = {
        "timestamp_sec": 12.5,
        "speaker": "remote",
        "quote": "至急、指定の口座にデポジットを振り込んでください。",
        "category": "emergency_request",
        "risk_score": 0.55,
        "risk_level": "suspicious",
        "reason": "金銭の至急振り込み要求が検出されました。",
        "advice": "電話を切って公式窓口に確認してください。",
    }
    det1_res = client.post(f"/api/v1/sessions/{session_id}/detections", json=det1)
    assert det1_res.status_code == 200
    det1_data = det1_res.json()
    assert det1_data["session_id"] == session_id
    assert det1_data["status"] == "saved"
    assert "id" in det1_data
    assert det1_data["detections_count"] == 1
    assert det1_data["max_risk_score"] == 0.55
    assert det1_data["overall_verdict"] == "suspicious"
    # 全件リスト detections は含まれず O(1) に軽量化されていること
    assert "detections" not in det1_data

    # 3. 検知結果の追加 (2件目: 高リスク -> overall_verdict が likely_fake に昇格)
    det2 = {
        "timestamp_sec": 25.0,
        "speaker": "remote",
        "quote": "院長の代理です。今すぐ暗証番号を教えてください。",
        "category": "impersonation",
        "risk_score": 0.92,
        "risk_level": "likely_fake",
        "reason": "暗証番号要求および医療関係者へのなりすまし疑い。",
        "advice": "絶対に応答せず通話を切断してください。",
    }
    det2_res = client.post(f"/api/v1/sessions/{session_id}/detections", json=det2)
    assert det2_res.status_code == 200
    det2_data = det2_res.json()
    assert det2_data["session_id"] == session_id
    assert det2_data["status"] == "saved"
    assert det2_data["detections_count"] == 2
    assert det2_data["max_risk_score"] == 0.92
    assert det2_data["overall_verdict"] == "likely_fake"
    assert "detections" not in det2_data

    # 4. セッション終了 (O(1) 軽量レスポンス)
    end_res = client.post(f"/api/v1/sessions/{session_id}/end", json={})
    assert end_res.status_code == 200
    end_data = end_res.json()
    assert end_data["session_id"] == session_id
    assert end_data["status"] == "ended"
    assert end_data["ended_at"] is not None
    assert end_data["detections_count"] == 2
    assert "detections" not in end_data

    # 5. 通話後レポート取得（ここで初めて全件 detections を取得！）
    report_res = client.get(f"/api/v1/sessions/{session_id}")
    assert report_res.status_code == 200
    report_data = report_res.json()
    assert report_data["session_id"] == session_id
    assert report_data["max_risk_score"] == 0.92
    assert report_data["overall_verdict"] == "likely_fake"
    assert report_data["detections_count"] == 2
    assert report_data["ended_at"] is not None
    assert len(report_data["detections"]) == 2
    assert report_data["detections"][0]["quote"] == det1["quote"]
    assert report_data["detections"][1]["quote"] == det2["quote"]


def test_session_not_found() -> None:
    if not client:
        return  # ライブラリ未インストール環境ではスキップ

    dummy_id = "non-existent-session-id"

    res = client.get(f"/api/v1/sessions/{dummy_id}")
    assert res.status_code == 404

    det = {
        "timestamp_sec": 1.0,
        "quote": "テスト",
        "category": "test",
        "risk_score": 0.1,
        "reason": "テスト",
    }
    res_det = client.post(f"/api/v1/sessions/{dummy_id}/detections", json=det)
    assert res_det.status_code == 404

    res_end = client.post(f"/api/v1/sessions/{dummy_id}/end", json={})
    assert res_end.status_code == 404


def test_add_detection_idempotent_on_retry() -> None:
    """同じ検知IDが再送されても、検知件数が重複増加しないことを検証する。"""
    if not client:
        return

    session_id = f"test-idempotent-{datetime.now(timezone.utc).timestamp()}"
    create_res = client.post(
        "/api/v1/sessions",
        json={"session_id": session_id},
    )
    assert create_res.status_code == 201

    det = {
        "id": f"{session_id}:utt-001",
        "timestamp_sec": 9.0,
        "speaker": "remote",
        "quote": "至急、手数料を振り込んでください。",
        "category": "C1",
        "risk_score": 0.85,
        "risk_level": "likely_fake",
        "reason": "金銭要求が検出されました。",
        "advice": "通話を終了して公式窓口へ確認してください。",
    }

    # 1回目の保存
    res1 = client.post(f"/api/v1/sessions/{session_id}/detections", json=det)
    assert res1.status_code == 200
    assert res1.json()["detections_count"] == 1
    assert res1.json()["id"] == det["id"]

    # 2回目の再送（ネットワーク応答消失後のリトライ等を想定）
    res2 = client.post(f"/api/v1/sessions/{session_id}/detections", json=det)
    assert res2.status_code == 200
    assert res2.json()["detections_count"] == 1
    assert res2.json()["id"] == det["id"]

    # レポート取得時も1件のみであること
    report_res = client.get(f"/api/v1/sessions/{session_id}")
    assert report_res.status_code == 200
    report = report_res.json()
    assert report["detections_count"] == 1
    assert len(report["detections"]) == 1
    assert report["detections"][0]["id"] == det["id"]


def test_incomplete_analysis_remains_visible_after_reopening_report() -> None:
    assert client is not None
    session_id = f"partial-{datetime.now(timezone.utc).timestamp()}"
    assert client.post("/api/v1/sessions", json={"session_id": session_id}).status_code == 201
    url = f"/api/v1/sessions/{session_id}"
    assert client.get(url).json()["analysis_incomplete"] is False
    assert client.post(url + "/end", json={"analysis_incomplete": True}).status_code == 200
    assert client.get(url).json()["analysis_incomplete"] is True
    # An older client ending the same session must not erase the warning.
    assert client.post(url + "/end", json={}).status_code == 200
    assert client.get(url).json()["analysis_incomplete"] is True
