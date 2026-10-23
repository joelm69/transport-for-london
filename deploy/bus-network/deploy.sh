#!/bin/sh
# Builds and deploys the tfl-bus-network Cloud Run Job, which polls every bus on the network
# and stores the poll's summaries in GCS. Run from anywhere: sh deploy/bus-network/deploy.sh
#
# The build context is a temporary folder holding only what the image needs, so .env, data/
# and the rest of the repository are never uploaded. The job runs as tfl-bus-network and gets
# the TfL key from Secret Manager (tfl-api-key) as TFL_API_KEY. 1 GiB: a poll peaks at ~600 MB.
set -eu

PROJECT=transport-for-london-511318
REGION=us-central1
JOB=tfl-bus-network
SERVICE_ACCOUNT=tfl-bus-network@$PROJECT.iam.gserviceaccount.com
ROOT=$(cd "$(dirname "$0")/../.." && pwd)

CONTEXT=$(mktemp -d)
trap 'rm -rf "$CONTEXT"' EXIT
cp "$ROOT/deploy/bus-network/Dockerfile" "$CONTEXT/"
for path in src/clients src/collectors/__init__.py src/collectors/bus_network.py scripts/bus_network_snapshot.py; do
    mkdir -p "$CONTEXT/$(dirname "$path")"
    cp -R "$ROOT/$path" "$CONTEXT/$path"
done
find "$CONTEXT" -name __pycache__ -prune -exec rm -rf {} +

gcloud run jobs deploy "$JOB" \
    --project "$PROJECT" --region "$REGION" --source "$CONTEXT" \
    --service-account "$SERVICE_ACCOUNT" \
    --set-secrets "TFL_API_KEY=tfl-api-key:latest" \
    --cpu 1 --memory 1Gi --task-timeout 10m --max-retries 1
