"""
boto3 client factory.

All AWS clients in the app are created here so retry and timeout settings
are consistent. Region, credentials and endpoint are resolved by boto3 from
the environment (AWS_REGION, AWS_ENDPOINT_URL, credential chain); this module
never reads or logs credentials.
"""

from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)


@lru_cache
def get_client(service_name: str) -> Any:
    """Return a cached low-level client for the given AWS service."""
    return boto3.session.Session().client(service_name, config=_CLIENT_CONFIG)


@lru_cache
def get_resource(service_name: str) -> Any:
    """Return a cached resource object (used for DynamoDB tables)."""
    return boto3.session.Session().resource(service_name, config=_CLIENT_CONFIG)


def clear_client_cache() -> None:
    """Drop cached clients: used by tests that change AWS_* env vars."""
    get_client.cache_clear()
    get_resource.cache_clear()
