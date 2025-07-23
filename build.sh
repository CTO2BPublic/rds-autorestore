#!/bin/bash
AWS_ACCOUNT_ID=123456789012
AWS_REGION=eu-central-1
VERSION=0.0.5
REPO=rds-autorestore
# login public ECR
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws
# login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
docker build -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:$VERSION --platform linux/amd64 . --push