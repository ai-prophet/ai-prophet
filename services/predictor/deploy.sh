#!/bin/bash
set -e

PROJECT_ID="anri-trading-491320"
SERVICE_NAME="predictor"
IMAGE_NAME="predictor"
REGION="us-west1"

echo "Deploying predictor service to project: $PROJECT_ID"

# Build and push using Cloud Build
gcloud builds submit \
  --config=cloudbuild.yaml \
  --project=$PROJECT_ID

echo "Deployment complete!"
echo ""
echo "Service URL:"
gcloud run services describe $SERVICE_NAME \
  --region=$REGION \
  --project=$PROJECT_ID \
  --format="value(status.url)"