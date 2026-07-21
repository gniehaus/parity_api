import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


AWS_REGION = os.environ["AWS_REGION"]
AWS_S3_BUCKET = os.environ["AWS_S3_BUCKET"]

s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    config=Config(
        signature_version="s3v4",
        s3={
            "addressing_style": "virtual",
        },
    ),
)


def generate_document_url(
    object_key: str,
    expires_in: int = 3600,
) -> str:
    try:
        return s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": AWS_S3_BUCKET,
                "Key": object_key,
            },
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(
            f"Unable to generate S3 document URL for {object_key}"
        ) from exc