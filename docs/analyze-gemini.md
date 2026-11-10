# 発言分析のVertex AI接続（#12・第1段階）

## 実装した範囲

`POST /analyze` → Vertex AIのGemini → JSON・引用検証 → HTTP応答。
既存のAPIキー認証と#11の6項目を維持する。`ANALYSIS_BACKEND=mock` は従来の固定モック、`vertexai` は実分析。
件数・重複の待機条件はmockとvertexaiの両方に適用する。
`DETECTOR_BACKEND` / `GEMINI_USE_VERTEXAI` はメディア検知用で、発言分析の切替には使わない。

モデルへの送信内容は配列末尾の直近N件の発言本文のみ。配列は古い順とし、句点で分割せず1つのUtteranceを1件と数える。
セッションID、発言ID、startSec、音声は送信しない。引用は実際に送った本文内にあることを確認する。

## 呼出タイミングと分析済みID

- 初回も以降も、新たな発言がN件そろったら分析する（既定N=5）。
- 以降、末尾N件に分析済み・分析中のIDが1件でも残っていればVertex AIを呼ばない。
- 末尾N件がすべて未分析ならまとめて分析し、正常結果の検証後にN件を分析済みとする。
- 件数待ち・重複待ちはHTTP 204（本文なし）。lowのJSONを代わりに生成しない。
- 失敗・タイムアウトでは予約を解放し、同じIDで再試行できる。実行中予約の期限は分析タイムアウト+30秒。
- 同じ窓に重複するIDがあれば422。IDは同じ発言の再送時も変えない。

```text
u1〜u4   → 204（5件を待つ）
u1〜u5   → u1〜u5を分析
u1〜u6   → 204（u2〜u5が分析済み）
u1〜u10  → u6〜u10を分析
u1〜u15  → u11〜u15を分析
```

初回から累積配列でも直近N件の配列でもよい。サーバーは不足分を蓄積しないため、
毎回新規1件だけを送る方法では分析されない。待機している間の発言は呼出側で保持する。

### 状態の保存先

`ANALYSIS_STATE_BACKEND=auto`（既定）は、実分析vertexaiならFirestore、mockならローカルSQLiteを選ぶ。
`firestore` / `sqlite` を明示することもできる。Firestore障害時は503にし、SQLiteへの自動切替はしない。

Firestoreは同じGCPプロジェクトの `ANALYSIS_FIRESTORE_DATABASE`（既定`(default)`）を使い、
`analysis_states/{SHA256(sessionId)}` に通話ごとに1ドキュメントを作成する。
既存担当者の `sessions` / `detections` は変更しない。
保存するのは分析済みIDのハッシュ一覧と実行中予約（IDハッシュ、所有者、有効期限）、更新時刻のみ。
本文・セッションIDの原文・APIキーは保存しない。1通話につき最大2000件の分析済みIDとし、
上限超過時は古いIDを忘れて再課金しないよう503で止める。

予約と成功・失敗の反映はFirestoreトランザクションで行う。Geminiはその外で呼ぶ。
通話ごとに分析中の予約を1つだけ許し、別インスタンスからの同時実行も待機させる。
古い所有者は、期限切れ後に別リクエストが取得した予約を変更できない。
Firestoreの作成・NativeモードのDB・API有効化・ADC/IAM権限を事前に準備する。
バックエンドのサービスアカウント等にデータ操作権限（例：roles/datastore.user）が必要。
SDKはサーバーからIAM認証で利用し、Web用Security Rulesを認証の代わりにしない。
Firestoreのタイムアウトは `ANALYSIS_STATE_TIMEOUT_SECONDS`（既定10秒）。
タイムアウトした予約操作が後から完了した場合も、予約の期限切れ後に再試行できる。

読み書きの目安（競合・再試行なし）：

| 処理 | 読み取り | 書き込み |
| --- | --- | --- |
| N件未満 | 0 | 0 |
| 件数・重複・実行中により待機 | 1 | 0 |
| 予約して分析し、成功／失敗を反映 | 2 | 2 |

15分300発言・発言ごとにAPI呼出・約60回分析の場合、概算約360読み取り・120書き込み。
これはID管理のみで、レポート保存等は別。トランザクション競合やリトライで増える。
2000件の上限と保存期限は別の制限。現時点で自動削除は設定せず、保存期限・削除方法はチームで決める。
TTL削除には無料枠がないため、expire_atの追加だけで無料の自動削除を保証しない。

SQLiteを明示した場合は `output/analysis-state/state.sqlite3` に保存し、ANALYSIS_STATE_PATHで変更可能。
Cloud Runではsqliteを使用せず、全インスタンスで同じプロジェクト・DBを指定する。
既存SQLiteの履歴は自動移行しない。切替後の確認では新しいsessionIdを使う。
プロセス停止や上流応答直後の障害による二重課金まで完全に防ぐものではない。

## 設定

既存の `.env` に必要な変数を設定する。実際のキーやプロジェクト値はGitに追加しない。

```dotenv
ANALYSIS_BACKEND=vertexai
BACKEND_API_KEY=ローカルで生成した共有キー
GOOGLE_CLOUD_PROJECT=自分のプロジェクトID
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.5-flash
ANALYSIS_STATE_BACKEND=auto
ANALYSIS_FIRESTORE_DATABASE=(default)
ANALYSIS_CONTEXT_UTTERANCES=5
ANALYSIS_TIMEOUT_SECONDS=20
ANALYSIS_MAX_RETRIES=1
```

- モデル・ロケーションはプロジェクトで利用可能な組合せを指定する。自動で別リージョンに切り替えない。
- `GCP_PROJECT_ID` / `GCP_LOCATION` も利用可能。同時指定時はこれらが `GOOGLE_CLOUD_*` より優先する。
- `GEMINI_MODEL` はメディア検知と共通。コード既定値を使うより、利用可能なモデル名を明示する。
- タイムアウトは再試行を含む分析全体で20秒（設定可能）。再試行は既定1回、最大2回。
- 不正なJSON・スキーマ不整合・入力にない引用、一時的な429/5xxを再試行する。権限不足等は再試行しない。
- 成功してもAI判定は安全・本人性の保証ではない。highのみ即時警告、mediumは終了後レポートという表示はUI側で実装する。

モデルと提供場所の確認先：
[Google公式モデル情報](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash)。

## ローカルからの確認

Google Cloud側で課金・Vertex AI API・呼出権限を準備する。
ローカルの認証は `gcloud auth application-default login`。
Cloud Runでは権限を付与したサービスアカウントのADCを使用し、秘密鍵ファイルを作成しない。

リポジトリのルートで、合成発言2件の確認を実行できる（API利用料金が発生）。

```bash
.venv/bin/python scripts/check_analysis.py
```

スクリプトは `.env` を読み込み、同じプロセス内のFastAPIへ認証付きリクエストを送る。
そのプロセス内だけでバックエンドをvertexai、APIキーを検証用の値に設定し、再試行を無効化する。
実際のVertex AIへ接続するが、ファイルや起動中のサーバー設定は変更しない。
各ケースは固有のsessionIdとN件の合成発言を生成する。
暗証番号の要求がhigh、症状・服薬確認がlowでHTTP 200なら終了コード0。
これは疎通確認であり、カテゴリ全体の検知精度評価ではない。

Swaggerから確認する場合：

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 8080 --env-file ../.env
```

`http://localhost:8080/docs` のAuthorizeに `BACKEND_API_KEY` を入力し、次を送る。

```json
{
  "sessionId": "synthetic-test",
  "utterances": [
    {"id": "u-1", "text": "こんにちは", "startSec": 0},
    {"id": "u-2", "text": "本日の手続きをご案内します", "startSec": 3},
    {"id": "u-3", "text": "銀行の暗証番号を教えてください", "startSec": 6},
    {"id": "u-4", "text": "確認のために必要です", "startSec": 9},
    {"id": "u-5", "text": "今からお伺いします", "startSec": 12},
    {"id": "u-6", "text": "暗証番号を読み上げてください", "startSec": 15}
  ]
}
```

high / C2 / 入力内の引用が返ることを確認する。通常の症状確認へ変更するとlowとなりcategory・quoteはnull。
同じリクエストの再送は204となる。別のケースを試す際は新しいsessionIdを使用する。
Gemini応答は確率的なので、期待と異なる場合は基準とプロンプトを評価する。実際の患者情報を疎通テストに使用しない。

## エラーとログ

| 状態 | HTTP |
| --- | --- |
| Project・ロケーション・モデルの空設定 | 503 |
| AI上流エラー・ADC認証エラー | 502 |
| 再試行後も不正なJSON・スキーマ・引用 | 502 |
| 分析全体のタイムアウト | 504 |

分析失敗をlowに変換しない。既存形式の `{"detail": "..."}` で汎用エラーのみを返す。
アプリの分析ログには成否・処理時間・送信件数と、各AI応答のusage_metadataを構造化レコードとして記録する。本文・生のAI応答・キー・上流エラー本文は記録しない。
SDKのHTTPデバッグログやリクエスト本文の記録は有効にしない。

## 第1段階に含めない範囲

- 確定発言の収集、N件または一定秒数での自動送信。
- 警告・保存側の重複防止（分析予約の共有はFirestore版で対応）。
- Firestoreへの保存、終了後レポート、警告UI。
- 発言確定から警告表示までの総遅延と費用の実測。現段階のログはバックエンド分析時間のみ。

同じFirestoreの状態を参照するインスタンス間では分析済みIDの再分析を抑制する。
状態の削除や、成功直後の保存障害等では再課金の可能性がある。
複数カテゴリ時の優先順や逐次送信仕様は連携時に確定する。

## 実接続の確認結果（2026-11-09）

ローカルのADC認証・既存プロジェクト、`gemini-3.5-flash` / `asia-northeast1` で
`scripts/check_analysis.py` を実行し、FastAPI経由の実HTTP上流通信を確認した。

| 合成入力 | 応答 | 分析時間（ネットワーク等を含む） |
| --- | --- | --- |
| 銀行の暗証番号を教えてください | 200 / high / C2 / 入力に一致する引用 | 約3.7秒 |
| 熱はいつからですか。今飲んでいる薬はありますか | 200 / low / category・quoteはnull | 約2.8秒 |

2件のみの疎通確認であり、精度・平均時間・P95・警告までの総遅延の測定ではない。

## 手動接続確認の履歴保存

`scripts/check_analysis.py` は実行前に開始履歴を作成し、実行後のHTTP応答と各AI呼出の
`usage_metadata` を `output/analysis-history/requests.jsonl` に追記する。
日時はUTC、リクエストIDで開始・完了・失敗を対応付ける。書込みごとにflush/fsyncする。
スクリプトに定義した合成発言のみを入力・出力として保存し、認証キーは保存しない。
履歴はGit管理外のローカルファイルであり、クラウドのバックアップではない。

```bash
.venv/bin/python scripts/check_analysis.py --case high
```

`prompt_token_count`が入力、`candidates_token_count`が回答、
`thoughts_token_count`が推論のトークン数。費用はモデル・ロケーション・料金体系に応じて
入力と回答・推論を別々に計算し、total_token_countをそのまま出力料金に掛けない。
失敗時はusage_metadataが取得できない場合があり、履歴だけで実請求額を保証しない。

参考：[Firestoreのトランザクション](https://firebase.google.com/docs/firestore/manage-data/transactions)、
[料金と無料枠](https://firebase.google.com/docs/firestore/pricing)。

## 本番DB作成なしでのFirestore検証

[Firestore Emulatorでの検証手順](firestore-emulator.md) を参照。
Geminiはmock、状態管理はEmulatorを使用して予約・重複防止を検証できる。
