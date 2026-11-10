# バックエンドAPI基盤（#10）

Python + FastAPI を使用する。ローカル起動手順は [README](../README.md#クイックスタート) を参照。
起動後の OpenAPI 仕様は `/openapi.json`、Swagger UI は `/docs` で確認できる。

## `GET /healthz`

APIサーバーがHTTPリクエストに応答できることを確認する。リクエスト本文・認証は不要。
Geminiへの通信や認証、データ保存先の接続状態は確認しない。

### レスポンス：`200 OK`

Content-Type: `application/json`

```json
{"status": "ok"}
```

```bash
curl -i http://localhost:8080/healthz
```

## `POST /analyze`（mock / Vertex AI）

Content-Type: `application/json`。`utterances` は1件以上の可変件数を受け取る。
`ANALYSIS_BACKEND=mock`（既定）は固定モック、`vertexai` は実分析。
設定・エラー・手動検証は [Vertex AI接続手順](analyze-gemini.md) を参照。
初回は既定5件以上が必要。以降も末尾5件すべてが未分析の場合のみ分析し、
件数待ち・分析済みIDとの重複・実行中予約がある場合は204（本文なし）を返す。
mockモードにも同じ待機条件を適用する。
以下の1〜2件の例は入力形式の例であり、現在の件数条件では204となる。
各項目の型・検証条件は [API契約](api-contract.md) を参照。

### APIキー認証

サーバーの `.env` または環境変数に `BACKEND_API_KEY` を設定して再起動する。
これはGemini接続用の `GEMINI_API_KEY` とは別の共有キー。
例えば `python -c 'import secrets; print(secrets.token_urlsafe(32))'` で値を生成できる。
実際の値をGitへコミットしないこと。

呼び出し時は `X-API-Key` ヘッダーに同じ値を指定する。
Swagger UIでは「Authorize」からキーを入力して実行できる。

```bash
curl -i http://localhost:8080/analyze \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: 実際に設定したキー' \
  -d '{"sessionId":"s-001","utterances":[{"id":"u-001","text":"発言内容","startSec":0}]}'
```

| 条件 | ステータス |
| --- | --- |
| 正しいキー・正常な入力 | 200 |
| キー未指定・空・不一致 | 401 |
| サーバー側のキー未設定・空白のみ | 503 |
| 正しいキー・不正な入力 | 422 |

`/healthz` は認証不要。認証は `/analyze` のみに適用し、既存APIは変更しない。
共有キーはMVP向けの簡易認証であり、ユーザーごとの認証ではない。

```json
{
  "sessionId": "session-001",
  "utterances": [
    {"id": "u-001", "text": "発言内容", "startSec": 0},
    {"id": "u-002", "text": "次の発言", "startSec": 12.5}
  ]
}
```

### レスポンス：`200 OK`

```json
{
  "is_suspicious": true,
  "risk_level": "high",
  "category": "金銭の要求・振込の急かし",
  "quote": "今すぐこの口座に振り込んでください",
  "reason": "【モック】送金を急がせる発言を検出した場合の判定例です。",
  "advice": "その場で振り込まず、公式窓口に確認してください。"
}
```

mockモードでは実際の分析・Gemini通信・セッション保存は行わず、入力内容や件数によらず
上記の固定結果1件を返す。`quote` も入力からの抽出ではない。
必須項目不足、空の発言一覧、空文字のID・発言、負の開始秒数などは `422` を返す。

## 拡張機能からの通信とCORS

通信経路は `content script → chrome.runtime.sendMessage → service worker → /analyze`。
content scriptからAPIを直接呼ばず、service workerでAPIキーを付与する。
現在は送信口のみ実装しており、録音・文字起こしからの自動送信は未接続。

- `extension/manifest.json` の `host_permissions` にローカルAPIのホストを追加済み。
  Chromeのホスト権限はポート単位に制限できないが、実際の送信先は
  `analysis-api.js` 内の `http://localhost:8080/analyze` に固定している。
- host permissionsを持つservice workerからの通信は通常のWebページのCORSとは異なる。
  この経路のためにMeetや任意の拡張機能Originをバックエンドで一律許可する必要はない。
- 通常のWeb画面には既存の `CORS_ALLOW_ORIGINS` を使用する。
  許可メソッドはGET・POST、許可ヘッダーはContent-Type・X-API-Key。
  許可OriginのOPTIONSは認証なしで応答し、実際のPOSTではAPIキーを検証する。
- CORSは認証ではない。未許可Originでもサーバーへの到達自体を防ぐものではない。

仕様の根拠：[Chrome公式：Cross-origin network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)。

### 拡張機能での手動確認

1. `.env` に `BACKEND_API_KEY` を設定し、バックエンドを再起動する。
2. `chrome://extensions` で `extension/` を読み込み済みなら再読み込みする。
3. 拡張機能の「Service Worker」の検証リンクを開き、Consoleで設定する。
   値はローカル開発用キーを使い、画面共有・ログへの貼り付けを避ける。

   ```javascript
   await chrome.storage.session.set({ backendApiKey: "サーバーと同じキー" });
   ```

4. 同じConsoleから送信する（service workerの関数を直接呼んで通信を確認）。

   ```javascript
   await analyzeUtterances({
     sessionId: "session-001",
     utterances: [{ id: "u-001", text: "接続確認", startSec: 0 }]
   });
   ```

   `{ok: true, result: ...}` と固定モックが返ることを確認する。
   キーを不正な値に変えると `{ok: false, status: 401, ...}`、
   正しいキーで `utterances: []` にするとstatus 422となる。
   キーを削除すると送信前に `api-key-not-configured` となる。

   ```javascript
   await chrome.storage.session.remove("backendApiKey");
   ```

5. 将来のcontent script側は、次のメッセージで同じ送信口を使用する。

   ```javascript
   const response = await chrome.runtime.sendMessage({
     target: "service-worker",
     type: "analyze",
     payload: {
       sessionId: "session-001",
       utterances: [{ id: "u-001", text: "発言内容", startSec: 0 }]
     }
   });
   ```

キーは `chrome.storage.session` に保存するため、ブラウザ終了や拡張機能再読み込み時は再設定が必要。
content scriptへのストレージ公開は行わない。キー入力UIや文字起こしとの接続は今後実装する。
Cloud Runへ切り替える際は、送信先URLとmanifestのhost permissionsを実際のHTTPSホストに更新し、
拡張機能を再読み込みして確認する。Cloud Runの接続先は未確定のため未追加。

### 検証結果（2026-11-08）

Chromeにローカルの `extension/` を読み込み、service workerのDevTools Consoleから
`analyzeUtterances()` を呼び出して、実際のHTTP通信を確認した。

| ケース | 確認結果 |
| --- | --- |
| 正しいテストキー・正常な入力 | HTTP 200、`ok: true` と固定モックの6項目を取得 |
| 正しいテストキー・空の発言一覧 | HTTP 422、`ok: false`・`status: 422` |
| 不正なキー | HTTP 401、`ok: false`・`status: 401` |
| 拡張機能側のキー未設定 | HTTP送信前に `api-key-not-configured` |

8080番は既存サーバーが使用中だったため、正常系と上記異常系の比較には
専用ダミーキーを設定した一時サーバー（`http://localhost:18081`）を使用した。
DevTools内で一時的にfetchの送信先ポートのみを差し替えて検証し、ファイル内の
`http://localhost:8080/analyze` は変更していない。
既存の8080番サーバーについては、ダミーキーで401が返ることを確認した。

検証後はfetchを元に戻し、拡張機能のテストキーを削除し、一時サーバーを停止した。
既存サーバーと実際のキーは変更していない。録音・マイク許可は行わず、拡張機能は読み込み済みで残した。

自動検証はPythonテスト38件、Ruff、JavaScript構文チェックが成功。
JavaScriptの模擬環境では送信ヘッダー・本文、正常応答、401、ネットワークエラー、
キー未設定、送信元制限、非同期メッセージ応答も確認した。
別途、実HTTPで許可Originのプリフライト200・未許可Originの400も確認済み。

今回Chromeで確認したのはservice workerからAPIへの通信であり、
content scriptからのメッセージ経由、録音・文字起こしからの自動送信、
Cloud Runへの接続は未確認。

## 既存APIと今後の範囲

既存の `GET /api/v1/health` と `POST /api/v1/detect` は維持する。
仕様は [既存のAPI契約](api-contract.md) を参照。

#10 のセッション開始・終了、レポート取得、
本文を残さないリクエストログの整備、Cloud Runへのデプロイ確認は今回の実装範囲に含めない。
