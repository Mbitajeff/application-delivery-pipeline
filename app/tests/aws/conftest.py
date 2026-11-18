"""
Fixtures for the AWS Emulator integration suite (marker: aws_emulator).

Every test gets its own freshly created DynamoDB tables, S3 bucket and SQS
queue (unique names), which are deleted again in teardown.

Safety: before any test runs, the session fixture below verifies that the
SDK can only reach a loopback endpoint and that no real-credential
variables are present. If not, every test in this folder FAILS.

Unimplemented emulator operations (HTTP 501 / NotImplemented) are not
worked around: the test fails and the operation is listed in a dedicated
section of the terminal summary so it is easy to report.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.api import dependencies
from app.aws.clients import clear_client_cache, get_client
from app.core.config import get_settings
from app.main import create_app
from app.tests.aws.safety import (
    UnsafeEmulatorConfigError,
    assert_loopback_endpoint,
    assert_safe_environment,
)

SERVICES = ("dynamodb", "s3", "sqs")
_not_implemented: list[str] = []


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def emulator_safety_gate() -> None:
    try:
        assert_safe_environment()
        clear_client_cache()
        for service in SERVICES:
            assert_loopback_endpoint(
                get_client(service).meta.endpoint_url,
                source=f"resolved {service} endpoint",
            )
    except UnsafeEmulatorConfigError as exc:
        pytest.fail(f"Unsafe AWS Emulator test environment: {exc}", pytrace=False)


# ---------------------------------------------------------------------------
# 501 NotImplemented reporting
# ---------------------------------------------------------------------------

def _not_implemented_operation(excinfo: pytest.ExceptionInfo | None) -> str | None:
    if excinfo is None or not isinstance(excinfo.value, ClientError):
        return None
    error = excinfo.value
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = error.response.get("Error", {}).get("Code")
    if status == 501 or code == "NotImplemented":
        return error.operation_name
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[None]:
    outcome = yield
    operation = _not_implemented_operation(call.excinfo)
    if operation:
        report = outcome.get_result()
        message = (
            f"AWS Emulator returned 501 NotImplemented for {operation} "
            f"({item.nodeid}, {call.when}). Not worked around: report upstream."
        )
        report.sections.append(("AWS Emulator NotImplemented", message))
        _not_implemented.append(message)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if _not_implemented:
        terminalreporter.section("AWS Emulator unimplemented operations (HTTP 501)")
        for line in _not_implemented:
            terminalreporter.write_line(line)


# ---------------------------------------------------------------------------
# Per-test AWS resources
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AwsResources:
    users_table: str
    items_table: str
    bucket: str
    queue_url: str


def _create_table(name: str) -> None:
    ddb = get_client("dynamodb")
    ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(
        TableName=name, WaiterConfig={"Delay": 1, "MaxAttempts": 30}
    )


def _empty_and_delete_bucket(bucket: str) -> None:
    s3 = get_client("s3")
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
    s3.delete_bucket(Bucket=bucket)


@pytest.fixture
def aws_resources() -> Iterator[AwsResources]:
    suffix = uuid.uuid4().hex[:12]
    names = {
        "users_table": f"it-users-{suffix}",
        "items_table": f"it-items-{suffix}",
        "bucket": f"it-audit-{suffix}",
        "queue": f"it-events-{suffix}",
    }
    cleanups = []
    try:
        _create_table(names["users_table"])
        cleanups.append(
            lambda: get_client("dynamodb").delete_table(TableName=names["users_table"])
        )
        _create_table(names["items_table"])
        cleanups.append(
            lambda: get_client("dynamodb").delete_table(TableName=names["items_table"])
        )
        get_client("s3").create_bucket(Bucket=names["bucket"])
        cleanups.append(lambda: _empty_and_delete_bucket(names["bucket"]))
        queue_url = get_client("sqs").create_queue(QueueName=names["queue"])["QueueUrl"]
        cleanups.append(lambda: get_client("sqs").delete_queue(QueueUrl=queue_url))

        yield AwsResources(
            users_table=names["users_table"],
            items_table=names["items_table"],
            bucket=names["bucket"],
            queue_url=queue_url,
        )
    finally:
        # Run every cleanup, then surface the first failure (if any).
        errors = []
        for cleanup in reversed(cleanups):
            try:
                cleanup()
            except Exception as exc:  # re-raised below
                errors.append(exc)
        if errors:
            raise errors[0]


@pytest.fixture
def aws_app_client(
    aws_resources: AwsResources,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """TestClient for the real app wired to DynamoDB, S3 and SQS (no overrides)."""
    env = {
        "ENVIRONMENT": "testing",
        "SECRET_KEY": "test-secret-key-32-bytes-minimum!",
        "STORAGE_BACKEND": "dynamodb",
        "DYNAMODB_USERS_TABLE": aws_resources.users_table,
        "DYNAMODB_ITEMS_TABLE": aws_resources.items_table,
        "AUDIT_BUCKET": aws_resources.bucket,
        "EVENTS_QUEUE_URL": aws_resources.queue_url,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    dependencies._get_dynamodb_store.cache_clear()

    with TestClient(create_app()) as client:
        yield client

    get_settings.cache_clear()
    dependencies._get_dynamodb_store.cache_clear()
