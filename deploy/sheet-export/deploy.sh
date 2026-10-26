#!/bin/sh
# Builds and deploys the tfl-sheets-export Cloud Run Job, which refreshes every tab of the
# project Google Sheet from BigQuery. Run from anywhere: sh deploy/sheet-export/deploy.sh
#
# The build context is a temporary folder holding only what the image needs, so .env, data/
# and the rest of the repository are never uploaded. Cloud Build builds the image and stores it
# in Artifact Registry (repository cloud-run-source-deploy); the job runs as tfl-sheets-export.
# The Sheet ID is read from .env (TFL_SHEET_ID) and set as an environment variable on the job.
set -eu

PROJECT=transport-for-london-511318
REGION=us-central1
JOB=tfl-sheets-export
SERVICE_ACCOUNT=tfl-sheets-export@$PROJECT.iam.gserviceaccount.com
ROOT=$(cd "$(dirname "$0")/../.." && pwd)

SHEET_ID=$(sed -n 's/^TFL_SHEET_ID=//p' "$ROOT/.env")
[ -n "$SHEET_ID" ] || { echo "TFL_SHEET_ID is missing from .env" >&2; exit 1; }

CONTEXT=$(mktemp -d)
trap 'rm -rf "$CONTEXT"' EXIT
cp "$ROOT/deploy/sheet-export/Dockerfile" "$CONTEXT/"
for path in src/exports scripts/export_to_sheet.py analytics/exports config/sheet_exports.json; do
    mkdir -p "$CONTEXT/$(dirname "$path")"
    cp -R "$ROOT/$path" "$CONTEXT/$path"
done
find "$CONTEXT" -name __pycache__ -prune -exec rm -rf {} +

gcloud run jobs deploy "$JOB" \
    --project "$PROJECT" --region "$REGION" --source "$CONTEXT" \
    --service-account "$SERVICE_ACCOUNT" \
    --set-env-vars "TFL_SHEET_ID=$SHEET_ID" \
    --cpu 1 --memory 1Gi --task-timeout 30m --max-retries 1
