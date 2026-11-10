#!/usr/bin/env bash

# Backend を Cloud Run にデプロイ
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID を設定してください}"

REGION="${REGION:-asia-northeast1}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN:?FRONTEND_ORIGIN を設定してください}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"

SA_EMAIL="detector-backend@${PROJECT_ID}.iam.gserviceaccount.com"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

gcloud run deploy detector-backend \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source "$ROOT/backend" \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --max-instances 3 \
  --memory 1Gi \
  --timeout 60 \
  --update-env-vars "DETECTOR_BACKEND=gemini,GEMINI_MODEL=${GEMINI_MODEL},GEMINI_USE_VERTEXAI=true,ANALYSIS_BACKEND=vertexai,STT_BACKEND=gcp,ANALYSIS_STATE_BACKEND=firestore,ANALYSIS_FIRESTORE_DATABASE=(default),GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=${REGION},CORS_ALLOW_ORIGINS=${FRONTEND_ORIGIN}" \
  --update-secrets "BACKEND_API_KEY=BACKEND_API_KEY:latest"
