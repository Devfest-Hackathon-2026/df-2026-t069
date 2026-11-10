#!/usr/bin/env bash
# frontend を Cloud Run にデプロイ [Role4]
# NEXT_PUBLIC_* はビルド時に埋め込まれるため、Cloud Build の build-arg として渡す
set -euo pipefail
: "${PROJECT_ID:?PROJECT_ID を設定してください}"
: "${BACKEND_URL:?BACKEND_URL (backend の Cloud Run URL) を設定してください}"
REGION="${REGION:-asia-northeast1}"
EXTENSION_ID="${EXTENSION_ID:?EXTENSION_ID を設定してください}"
FRONTEND_SA="detector-frontend@${PROJECT_ID}.iam.gserviceaccount.com"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/detector-frontend"

gcloud builds submit "$ROOT/frontend" --project "$PROJECT_ID" \
  --config /dev/stdin <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "--build-arg", "NEXT_PUBLIC_API_BASE_URL=${BACKEND_URL}", "-t", "${IMAGE}", "."]
images: ["${IMAGE}"]
EOF

gcloud run deploy detector-frontend \
  --project "$PROJECT_ID" --region "$REGION" \
  --image "$IMAGE" --port 3000 \
  --allow-unauthenticated --max-instances 3 \
  --service-account "$FRONTEND_SA" \
  --update-env-vars "BACKEND_API_URL=${BACKEND_URL},EXTENSION_ORIGIN=chrome-extension://${EXTENSION_ID}" \
  --update-secrets "BACKEND_API_KEY=BACKEND_API_KEY:latest"

