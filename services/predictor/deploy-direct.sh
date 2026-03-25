#!/bin/bash
set -e

PROJECT_ID="anri-trading-491320"
SERVICE_NAME="predictor"
REGION="us-west1"

echo "Direct deployment to Cloud Run (no Cloud Build)..."

# Deploy directly from source
gcloud run deploy $SERVICE_NAME \
  --source . \
  --project=$PROJECT_ID \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi \
  --timeout=300s \
  --concurrency=80 \
  --min-instances=0 \
  --max-instances=10 \
  --port=8080

echo ""
echo "Deployment complete!"
echo "Service URL:"
gcloud run services describe $SERVICE_NAME \
  --region=$REGION \
  --project=$PROJECT_ID \
  --format="value(status.url)"