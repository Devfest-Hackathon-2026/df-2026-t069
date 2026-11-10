#!/usr/bin/env bash
# 初回のみ実行: API有効化・Secret作成・backend用サービスアカウント作成 [Role4]
set -euo pipefail
: "${PROJECT_ID:?PROJECT_ID を設定してください}"
REGION="${REGION:-asia-northeast1}"
SA_NAME="detector-backend"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com

gcloud iam service-accounts create "$SA_NAME" --display-name="Detector backend" || true

# APIキーは標準入力から受け取り、シェル履歴に残さない
if ! gcloud secrets describe GEMINI_API_KEY >/dev/null 2>&1; then
  read -r -s -p "GEMINI_API_KEY: " KEY; echo
  printf '%s' "$KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
  unset KEY
fi
gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor"

echo "Done. region=${REGION} sa=${SA_EMAIL}"

