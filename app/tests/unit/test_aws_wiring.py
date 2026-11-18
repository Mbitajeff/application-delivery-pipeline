"""
Unit tests for the AWS wiring that do NOT need the emulator.

- emulator safety guard (localhost-only endpoint, no real credentials)
- storage backend / event publisher selection from settings
- ItemEventPublisher request shape (botocore Stubber, no network)
- item routes call the publisher with the right event types
"""

import json

import pytest
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient

from app.api import dependencies
from app.api.dependencies import InMemoryStore, get_db, get_event_publisher
from app.aws import clients
from app.aws.events import ItemEventPublisher
from app.tests.aws.safety import (
    UnsafeEmulatorConfigError,
    assert_loopback_endpoint,
    assert_no_real_credentials,
    assert_safe_environment,
)

# ---------------------------------------------------------------------------
# Safety guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1:9297", "https://localhost:9297", "https://[::1]:9297"],
)
def test_loopback_endpoints_accepted(url: str) -> None:
    assert_loopback_endpoint(url, source="test")


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://127.0.0.1:9297",
        "https://s3.us-east-1.amazonaws.com",
        "https://dynamodb.us-east-1.amazonaws.com",
        "https://127.0.0.1.evil.example.com",
        "https://10.0.0.5:9297",
    ],
)
def test_non_local_endpoints_rejected(url: str | None) -> None:
    with pytest.raises(UnsafeEmulatorConfigError):
        assert_loopback_endpoint(url, source="test")


@pytest.mark.parametrize(
    "name", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"]
)
def test_real_credential_variables_rejected_without_leaking_value(name: str) -> None:
    with pytest.raises(UnsafeEmulatorConfigError) as excinfo:
        assert_no_real_credentials({name: "VALUE-MUST-NOT-APPEAR"})
    assert name in str(excinfo.value)
    assert "VALUE-MUST-NOT-APPEAR" not in str(excinfo.value)


def test_safe_environment_passes_for_emulator_settings() -> None:
    assert_safe_environment({
        "AWS_ENDPOINT_URL": "https://127.0.0.1:9297",
        "AWS_SHARED_CREDENTIALS_FILE": "/home/runner/.aws/emulator/9297/emulator_credentials",
    })


# ---------------------------------------------------------------------------
# Settings-driven selection
# ---------------------------------------------------------------------------

def test_default_backend_is_in_memory_and_publisher_disabled() -> None:
    assert isinstance(get_db(), InMemoryStore)
    assert get_event_publisher().enabled is False
    assert get_event_publisher().publish("item.created", item_id=1, actor_id=1) is None


def test_dynamodb_backend_selected_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {}

    class FakeStore:
        def __init__(self, users_table: str, items_table: str) -> None:
            created.update(users=users_table, items=items_table)

    monkeypatch.setattr("app.aws.dynamodb_store.DynamoDBStore", FakeStore)
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_USERS_TABLE", "u-table")
    monkeypatch.setenv("DYNAMODB_ITEMS_TABLE", "i-table")
    dependencies._get_dynamodb_store.cache_clear()
    try:
        assert isinstance(get_db(), FakeStore)
        assert created == {"users": "u-table", "items": "i-table"}
    finally:
        dependencies._get_dynamodb_store.cache_clear()


# ---------------------------------------------------------------------------
# ItemEventPublisher (Stubber: no network, no credentials used)
# ---------------------------------------------------------------------------

@pytest.fixture
def stubbed_clients(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "stub")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "stub")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    clients.clear_client_cache()
    s3, sqs = clients.get_client("s3"), clients.get_client("sqs")
    with Stubber(s3) as s3_stub, Stubber(sqs) as sqs_stub:
        yield s3_stub, sqs_stub
        s3_stub.assert_no_pending_responses()
        sqs_stub.assert_no_pending_responses()
    clients.clear_client_cache()


def test_publisher_writes_encrypted_audit_and_sends_event(stubbed_clients) -> None:
    s3_stub, sqs_stub = stubbed_clients
    s3_stub.add_response(
        "put_object",
        {},
        {
            "Bucket": "audit-bucket",
            "Key": ANY,
            "Body": ANY,
            "ContentType": "application/json",
            "ServerSideEncryption": "AES256",
        },
    )
    sqs_stub.add_response(
        "send_message",
        {"MessageId": "m-1", "MD5OfMessageBody": "x"},
        {
            "QueueUrl": "https://127.0.0.1:9297/000000000000/q",
            "MessageBody": ANY,
            "MessageAttributes": {
                "event_type": {"DataType": "String", "StringValue": "item.updated"},
            },
        },
    )
    publisher = ItemEventPublisher("audit-bucket", "https://127.0.0.1:9297/000000000000/q")
    event = publisher.publish("item.updated", item_id=7, actor_id=3, changed_fields=["status"])

    assert event["item_id"] == 7 and event["actor_id"] == 3
    assert event["changed_fields"] == ["status"]
    assert set(event) == {
        "event_id", "event_type", "item_id", "actor_id", "changed_fields", "occurred_at"
    }


def test_publisher_failure_propagates(stubbed_clients) -> None:
    s3_stub, _ = stubbed_clients
    s3_stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
    publisher = ItemEventPublisher("audit-bucket", None)
    with pytest.raises(Exception, match="AccessDenied"):
        publisher.publish("item.created", item_id=1, actor_id=1)


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------

class RecordingPublisher(ItemEventPublisher):
    def __init__(self) -> None:
        super().__init__(None, None)
        self.calls: list[tuple] = []

    def publish(self, event_type, item_id, actor_id, changed_fields=None):  # type: ignore[override]
        self.calls.append((event_type, item_id, actor_id, sorted(changed_fields or [])))
        return None


def test_item_routes_publish_events(client: TestClient, auth_headers: dict[str, str]) -> None:
    recorder = RecordingPublisher()
    client.app.dependency_overrides[get_event_publisher] = lambda: recorder

    item_id = client.post(
        "/api/v1/items", json={"title": "t"}, headers=auth_headers
    ).json()["id"]
    client.patch(f"/api/v1/items/{item_id}", json={"status": "completed"}, headers=auth_headers)
    client.delete(f"/api/v1/items/{item_id}", headers=auth_headers)

    assert recorder.calls == [
        ("item.created", item_id, 1, []),
        ("item.updated", item_id, 1, ["status", "updated_at"]),
        ("item.deleted", item_id, 1, []),
    ]
    json.dumps(recorder.calls)  # events are JSON-serialisable
