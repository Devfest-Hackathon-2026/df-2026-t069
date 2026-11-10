# API 契約（Role2 ⇔ Role3）

## 発言テキスト分析のスキーマ（#10・#11）

`backend/app/schemas/analysis.py` に定義。`POST /analyze` はmock / Vertex AIを設定で切り替える。
[Vertex AI接続手順](analyze-gemini.md) を参照。
既存のメディア検知用TypeScript型とは別の契約で、フロント側の対応型は未追加。

入力：`sessionId`（空でない文字列）、`utterances`（1件以上）。
各発言は `id`・`text`（空でない文字列）、`startSec`（録音開始から発言開始までの経過秒数。0以上の有限数）を必須とする。

出力は次の6項目をすべて必須とする。

| 項目 | 型 |
| --- | --- |
| `is_suspicious` | boolean |
| `risk_level` | `low` / `medium` / `high` |
| `category` | `C1` / `C2` / `C3` / `C4` / `C5` または null |
| `quote` | string または null |
| `reason` | string |
| `advice` | string |

該当する不審カテゴリ・発言がない場合は `category`・`quote` に null を返す。
カテゴリ・判定基準は [#11 判定基準](detection-criteria.md) を参照する。
根拠のない通常会話は `risk_level=low / is_suspicious=false`、category・quoteはnull。
軽い注意は `risk_level=low / is_suspicious=true` とし、カテゴリと引用を必須とする。
注意対象のlowおよびmedium/highはcategoryがC1〜C5、quoteが空白のみでない文字列。
reason・adviceも空白のみの文字列を許可しない。これらはPydanticで検証する。
即時警告はhighのみ。軽い注意のlowとmediumは通話中に警告せず、終了後レポートへ掲載する。

### 保存・レポートとの対応

`is_suspicious=true` の結果は `/analyze` が保存し、通常会話（false）は保存しない。
保存用スコアはlow=0.25、medium=0.5、high=0.85。確率ではなく危険度区分の固定値。
保存判定はそれぞれlow、suspicious、likely_fakeとなる。
引用元の発言を一意に特定し、録音開始を0秒としたstartSecをtimestamp_secへ転記する。
対象がない・重複している場合は時刻を推測せず502。保存対象のセッションは事前作成が必要。

通話APIのoverall_verdictは `authentic | low | suspicious | likely_fake | unknown`。
最大スコアが0ならauthentic、0より大きく0.4未満ならlow、0.4以上ならsuspicious、
0.7以上ならlikely_fake。lowの表示は「軽い注意があります」とし、安全・本人性を保証しない。
SessionVerdictは通話API専用で、既存のメディア単発判定のVerdict型は変更しない。

初回は発言が5件そろった時点で分析する。以降は末尾5件のIDが
すべて未分析になったときだけ分析する。件数・IDの条件を満たさない場合は
**HTTP 204（本文なし）** を返す。これはlow判定ではない。判定結果がある場合のみ200で6項目を返す。
同じsessionId内で分析済みIDを照合し、成功時のみ記録する。発言IDは再送時も維持する。
件数はANALYSIS_CONTEXT_UTTERANCESで変更可能。
実分析では既定でFirestoreに状態を保存し、複数インスタンスで共有する。
状態ストアへ接続できない場合は503を返し、分析は開始しない。
Vertex AIモードではquoteが実際に送信した発言本文に実在することも検証する。固定モックのquoteは入力からの抽出ではない。

## 既存のメディア検知API

> 実装の正: `backend/app/schemas/detection.py` / `frontend/src/types/detection.ts`
> 変更する場合は **3ファイルを同じPRで更新**し、Role2・Role3 双方の Approve を得ること。
> 起動中の backend では http://localhost:8080/docs で Swagger UI を確認できる。

## `GET /api/v1/health`

```json
{ "status": "ok" }
```

## `POST /api/v1/detect`

`multipart/form-data`

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `media` | file | ✅ | audio/(wav, mpeg, mp4, webm, ogg), video/(mp4, webm), image/(jpeg, png, webp) |
| `context` | string (≤500) | | 状況説明（例: 息子を名乗る電話） |

### 200 OK

```json
{
  "verdict": "likely_fake",
  "risk_score": 0.86,
  "summary": "合成音声の特徴が複数見られます。",
  "evidences": [
    { "category": "voice_prosody", "description": "抑揚が一定で呼吸音がありません", "severity": 0.8 }
  ],
  "recommended_actions": ["一度電話を切り、本人の番号にかけ直してください"],
  "detector": "gemini",
  "model": "gemini-3.5-flash",
  "processing_ms": 3120
}
```

`verdict`: `authentic` | `suspicious` | `likely_fake` | `unknown`

### エラー

| status | 意味 |
| --- | --- |
| 400 | 空ファイル |
| 413 | サイズ上限超過（`MAX_UPLOAD_BYTES`） |
| 415 | 非対応の MIME タイプ |
| 422 | パラメータ不正 |
| 502 | AI 判定失敗（上流エラー） |



### 通話分析の一部未完了

`POST /api/v1/sessions/{id}/end` は任意の `analysis_incomplete: boolean`
（既定値 `false`）を受け付けます。レポート取得レスポンスにも同名の項目を追加しました。
一度 `true` になった値は、終了通知の再送で `false` に戻しません。既存データは
`false` として扱います。検知件数や危険度スコアは保存済みの結果を維持します。

拡張機能は停止時の最終チャンク・分析を最大2秒待ち、その後に未完了通信を中断します。
終了APIは最大750msで待機を打ち切り、失敗してもレポート取得を試みます。
終了通知が届かなかった場合は `?incomplete=1` も付け、表示中の未完了を伝えます。
このURLを失うと通知失敗時のフラグは復元できません。取得API自体が利用できない場合は
従来の取得エラー表示になります。ダウンロード中はOffscreenとBlob URLを保持します。
