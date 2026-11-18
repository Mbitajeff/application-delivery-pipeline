"""
AWS integrations (DynamoDB storage, S3 audit trail, SQS item events).

Everything here is opt-in through settings (see app/core/config.py) and is
OFF by default. Clients are built with the standard boto3 credential and
endpoint resolution, so the same code runs against real AWS or against the
local AWS Emulator (AWS_ENDPOINT_URL) without any code changes.
"""
