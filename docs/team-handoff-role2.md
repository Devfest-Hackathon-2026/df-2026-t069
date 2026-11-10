# Role 2（なさたな）開発成果物・連携仕様書（ハンドオフ資料）

本ドキュメントは、Role 2（なさたな: 通信・状態管理・拡張機能・文字起こし・Firestore）が
実装した Issue #7, #8, #9, #17 のインターフェース仕様および
チームメンバー（Role 1, Role 3, Role 4）との連携方法をまとめたものです。

---

## 1. To: poco（Role 1: UI/UX・警告UI・レポート画面担当）

### ① リアルタイム警告UI連携 [Issue #15 向け]
Service Worker が不審な発言を検知した際、Meet 画面の Content Script に以下のメッセージを送信します。

- **メッセージ形式 (`SHOW_ALERT`)**:
  ```typescript
  {
    type: "SHOW_ALERT",
    payload: {
      text: string;         // 検知された不審な発言引用（例: "至急口座に振り込んでください"）
      reason: string;       // 検知理由（例: "金銭の至急振り込み要求が検出されました"）
      risk_score: number;   // 0.0 〜 1.0 の危険度
      advice?: string;      // 推奨アクション（例: "一度電話を切って公式窓口に確認してください"）
    }
  }
  ```

- **Content Script での受信・表示方法**:
  `extension/src/content/index.tsx` の `OverlayManager` で受信し、
  `WarningBanner.tsx` を画面最前面（`z-index: 999999`）に描画しています。
  UIデザインやアニメーションのブラッシュアップは
  [`extension/src/content/WarningBanner.tsx`](file:///Users/kitanokeita/DevFest-2026/df-2026-waiwai-dev/extension/src/content/WarningBanner.tsx)
  を直接編集してください。

---

### ② 通話後レポートAPI連携 [Issue #18 / #19 向け]
通話終了後、レポート画面で以下のエンドポイントを呼び出すことで、
セッションの統計および不審箇所のタイムラインを取得できます。

- **エンドポイント**: `GET /api/v1/sessions/{session_id}`
- **レスポンススキーマ (`SessionResponse`)**:
  ```json
  {
    "session_id": "meet-12345",
    "started_at": "2026-11-08T08:00:00Z",
    "ended_at": "2026-11-08T08:15:30Z",
    "max_risk_score": 0.92,
    "overall_verdict": "likely_fake",
    "detections_count": 2,
    "expire_at": "2026-11-15T08:00:00Z",
    "detections": [
      {
        "id": "c1f7b8e2-...",
        "timestamp_sec": 12.5,
        "speaker": "remote",
        "quote": "至急、指定の口座にデポジットを振り込んでください。",
        "category": "emergency_request",
        "risk_score": 0.55,
        "risk_level": "suspicious",
        "reason": "金銭の至急振り込み要求が検出されました。",
        "advice": "電話を切って公式窓口に確認してください。",
        "created_at": "2026-11-08T08:02:15Z",
        "expire_at": "2026-11-15T08:00:00Z"
      }
    ]
  }
  ```
- **判定ラベル (`overall_verdict`) の表示目安**:
  - `authentic`: 緑色バッジ（「安全・正常」）
  - `suspicious`: 黄色バッジ（「要注意・追加確認推奨」）
  - `likely_fake`: 赤色バッジ（「危険・なりすまし疑い高」）

---

## 2. To: ふら（Role 3: AI検知コア・Gemini連携担当）

### 文字起こしバッファ連携 [Issue #9 / #11 向け]
Service Worker から送られた 3秒ごとの音声チャンクを左右チャンネル（L=自分, R=相手）で分離認識し、
確定した発言をセッションごとに独立して文単位でバッファリングしています。

- **エンドポイント**: `POST /api/v1/transcribe`
- **リクエストパラメータ**:
  - `audio`: 音声チャンクファイル (WebM/Opus または WAV)
  - `session_id`: 通話セッションID (セッションごとに発言文脈を完全分離)
  - `start_sec`: 通話開始からの経過秒数
- **レスポンススキーマ (`TranscriptionResponse`)**:
  ```json
  {
    "utterances": [
      {
        "id": "utt_1",
        "text": "もしもし、聞こえますでしょうか。",
        "start_sec": 3.0,
        "speaker": "自分",
        "is_final": true
      }
    ],
    "recent_context": "もしもし、聞こえますでしょうか。 はい、聞こえますよ。",
    "has_opponent_speech": true,
    "processing_ms": 12
  }
  ```
- **Gemini分析への渡し方**:
  `recent_context` に直近5文程度の会話文脈がまとめられています。
  これを `POST /api/v1/detect` の `context` 引数にそのまま渡すことで、
  文脈を踏まえた高精度ななりすまし分析が可能です。

---

## 3. To: Link（Role 4: GCPインフラ・デプロイ担当）

### Firestore ルールおよび TTL ポリシーの適用 [Issue #6 / #17 向け]
`infra/firestore/` にセキュリティルールと設計書を配置しています。

1. **セキュリティルールのデプロイ**:
   クライアント直接の読み書きは全面拒否（`allow read, write: if false;`）とし、
   Cloud Run バックエンド（サービスアカウント権限）からのみアクセスする堅牢な構成です。
   ```bash
   firebase deploy --only firestore:rules
   ```

2. **TTL（自動消去）ポリシーの有効化**:
   通話終了から7日後に自動消去し、プライバシー保護とストレージコストゼロ運用を実現します。
   （※Firestoreの仕様上、サブコレクションにも個別にTTL設定が必要です）
   ```bash
   # 1. ルートドキュメント（sessions）の TTL
   gcloud firestore fields ttls update expire_at \
       --collection-group=sessions \
       --project=<GCP_PROJECT_ID>

   # 2. サブコレクション（detections）の TTL
   gcloud firestore fields ttls update expire_at \
       --collection-group=detections \
       --project=<GCP_PROJECT_ID>
   ```

---

## 4. テスト検証完了状況サマリー

| 機能 / Issue | テスト内容 | 結果 |
| :--- | :--- | :--- |
| **#7 拡張機能雛形** | `npm run build` による TypeScript 型検査 & バンドル | **Passed** (`dist/` 生成確認) |
| **#8 Meet音声取得** | `chrome.tabCapture` + `getUserMedia` ステレオ合成 & 3秒チャンク | **Passed** (実機検証・ビルド確認) |
| **#9 文字起こしAPI** | 文単位分割・左右話者分離・セッション分離バッファリング | **Passed** (`tests/test_transcription.py`) |
| **#17 Firestore保存** | セッション開始・不審検知蓄積・サブコレクションTTL・レポート取得 | **Passed** (`tests/test_sessions.py`) |
| **バックエンド全体** | pytest 自動テスト一括実行（全 31 件） | **Passed** (31 passed in 0.44s) |
