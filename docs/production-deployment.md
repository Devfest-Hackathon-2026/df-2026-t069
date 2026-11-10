# Mimamori 本番デプロイ設定

## 前提

- GCPプロジェクト: `waiwaiwaiwai`
- リージョン: `asia-northeast1`
- Cloud Run: `detector-backend` / `detector-frontend`
- Secret Manager: `BACKEND_API_KEY`

## Chrome拡張機能ID

1. Chromeで `chrome://extensions` を開く
2. デベロッパーモードを有効にする
3. Mimamoriの拡張機能IDを確認する
4. デプロイ時に `EXTENSION_ID` として指定する

`EXTENSION_ORIGIN` は `chrome-extension://<拡張ID>` の形式です。

ローカル版と本番版で拡張機能IDが異なる場合は、
それぞれの環境に対応するIDを使用してください。

## Secret Manager

`BACKEND_API_KEY` はSecret Managerで管理します。
APIキーの値をリポジトリやシェル履歴に記録しないでください。

Backend・Frontendのサービスアカウントには、
Secret `BACKEND_API_KEY` に対する
`roles/secretmanager.secretAccessor` が必要です。

既存Secretを使用する場合、キーの再生成は不要です。

## Backend設定

必要な設定:

- `CORS_ALLOW_ORIGINS`: 許可するFrontend Origin
- `BACKEND_API_KEY`: Secret Manager参照

デプロイ例:

    export PROJECT_ID=waiwaiwaiwai
    export REGION=asia-northeast1
    export FRONTEND_ORIGIN=https://waiwaiwaiwai.web.app
    bash infra/cloudrun/deploy-backend.sh

`--update-env-vars` で既存環境変数を保持し、
`--update-secrets` でSecret参照を設定します。

## Frontend設定

必要な設定:

- `BACKEND_API_URL`: Backend Cloud Run URL
- `EXTENSION_ORIGIN`: 許可するChrome拡張機能Origin
- `BACKEND_API_KEY`: Secret Manager参照

デプロイ例:

    export PROJECT_ID=waiwaiwaiwai
    export REGION=asia-northeast1
    export BACKEND_URL=https://detector-backend-vaggox657a-an.a.run.app
    export EXTENSION_ID="実際のChrome拡張機能ID"
    bash infra/cloudrun/deploy-frontend.sh

Next.jsの分析APIは `EXTENSION_ORIGIN` を厳密に照合します。
ワイルドカードによる許可は行いません。

## デプロイ前チェック

- Backend・Frontendのサービスアカウントが存在する
- 両サービスアカウントにSecret Accessor権限がある
- `BACKEND_API_KEY` のSecretと有効なバージョンが存在する
- `FRONTEND_ORIGIN` が正しい
- `BACKEND_URL` が正しい
- `EXTENSION_ID` が実際の拡張機能IDと一致する

## マージ前の未実施確認

Google Meet通話から文字起こし、Gemini分析、
Firestore保存、レポート表示までのE2E確認は未実施です。
PR #39のマージ前確認事項として残します。
