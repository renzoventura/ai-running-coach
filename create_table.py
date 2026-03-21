"""
One-time setup script to create the DynamoDB table for the AI Running Coach.

Usage:
    DYNAMODB_TABLE=ai-running-coach AWS_REGION=ap-southeast-2 python create_table.py
"""
import os
import sys

import boto3
from botocore.exceptions import ClientError

table_name = os.environ.get("DYNAMODB_TABLE")
region = os.environ.get("AWS_REGION")

if not table_name:
    print("Error: DYNAMODB_TABLE environment variable is not set.")
    sys.exit(1)

if not region:
    print("Error: AWS_REGION environment variable is not set.")
    sys.exit(1)

dynamodb = boto3.resource("dynamodb", region_name=region)

print(f"Creating table '{table_name}' in region '{region}'...")

try:
    table = dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    print("Waiting for table to become ACTIVE...")
    table.wait_until_exists()
    table.reload()

    print(f"Table ARN:    {table.table_arn}")
    print(f"Table status: {table.table_status}")

except ClientError as e:
    if e.response["Error"]["Code"] == "ResourceInUseException":
        print(f"Table '{table_name}' already exists.")
    else:
        print(f"Failed to create table: {e}")
        sys.exit(1)
