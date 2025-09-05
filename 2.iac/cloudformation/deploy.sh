#!/bin/bash

STACK_NAME="rescuebot-infrastructure"
TEMPLATE_FILE="rescuebot-infrastructure.yaml"
REGION="ap-northeast-2"

ENVIRONMENT=${1:-dev}
PROJECT_NAME=${2:-rescuebot}

echo "Deploying Rescue Bot Infrastructure..."
echo "Environment: $ENVIRONMENT"
echo "Project Name: $PROJECT_NAME"
echo "Region: $REGION"

aws cloudformation deploy \
  --template-file $TEMPLATE_FILE \
  --stack-name $STACK_NAME \
  --parameter-overrides \
    Environment=$ENVIRONMENT \
    ProjectName=$PROJECT_NAME \
  --capabilities CAPABILITY_IAM \
  --region $REGION

if [ $? -eq 0 ]; then
  echo "Stack deployment completed successfully!"
  
  echo "Getting stack outputs..."
  aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --region $REGION \
    --query 'Stacks[0].Outputs' \
    --output table
else
  echo "Stack deployment failed!"
  exit 1
fi