# ADR-0001: 初期技術スタック

- 日付: 2026-11-05
- 状態: **Proposed**（チームで議論して Accepted にする）
- 決定者: TODO

## 背景（Context）
24時間・4人で「AIなりすまし検知」を、デモ可能な完成度まで作る必要がある。
音声と映像の両方を扱い、判定理由をユーザーに説明できることが要件。

## 選択肢（Options）
| 領域 | 案 | 主な代替案 |
| --- | --- | --- |
| フロント | Next.js (App Router) + TypeScript + Zustand | Vite + React / Flutter Web |
| バックエンド | FastAPI (Python) | Next.js API Routes のみ / Node(Express) |
| AI | Gemini API（google-genai SDK） | 専用検知モデル / 他社LLM |
| インフラ | Cloud Run ×2 + Secret Manager | Firebase Hosting + Functions |

## 決定（Decision）
上記「案」列を初期構成として採用する（ボイラープレートはこの前提で作成済み）。

## 理由（Why）— 自分たちの言葉で
TODO: チームで議論して記入。たたき台:
- FastAPI を分離する理由: 検知ロジック（Python の音声処理ライブラリ等）を将来追加しやすい / APIキーをブラウザから完全に隔離できる / Role2 と Role3 が独立して開発できる
- Zustand: Redux より記述量が少なく24時間向き
- Cloud Run: 両サービスを同じ方法でデプロイでき、運用知識が1つで済む

## 影響（Consequences）
- 2サービス構成のため CORS 設定とデプロイ手順が増える
- 見直し条件: TODO

