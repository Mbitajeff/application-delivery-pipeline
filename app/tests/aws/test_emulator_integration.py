"""
Integration tests: the app's real AWS code paths against the local AWS Emulator.

Covers:
  - DynamoDBStore (app/aws/dynamodb_store.py) directly
  - the full items API with STORAGE_BACKEND=dynamodb
  - S3 audit records and SQS events (app/aws/events.py)

NOTE: the emulator does not evaluate IAM or resource policies. Passing
these tests says nothing about whether IAM permissions are correct; that
remains the job of the static gates (Checkov) and review.
"""

import json
import secrets
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.aws.clients import get_client
from app.aws.dynamodb_store import DynamoDBStore
from app.tests.aws.conftest import AwsResources

pytestmark = pytest.mark.aws_emulator

API = "/api/v1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _random_password() -> str:
    """Throwaway password generated per run (letters + digit), never hardcoded."""
    return f"pw{secrets.token_hex(8)}9"


def _register_and_login(client: TestClient, username: str) -> dict[str, str]:
    password = _random_password()
    response = client.post(
        f"{API}/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": password},
    )
    assert response.status_code == 201, response.text
    response = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _receive_events(queue_url: str, expected: int, timeout_s: float = 10.0) -> list[dict]:
    sqs = get_client("sqs")
    messages: list[dict] = []
    deadline = time.monotonic() + timeout_s
    while len(messages) < expected and time.monotonic() < deadline:
        batch = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
            MessageAttributeNames=["All"],
        ).get("Messages", [])
        for message in batch:
            messages.append(message)
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
    return messages


def _audit_objects(bucket: str) -> list[dict]:
    response = get_client("s3").list_objects_v2(Bucket=bucket, Prefix="audit/items/")
    return response.get("Contents", [])


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_sdk_endpoints_resolve_to_local_emulator() -> None:
    for service in ("dynamodb", "s3", "sqs"):
        assert get_client(service).meta.endpoint_url.startswith("https://127.0.0.1:")


def test_dynamodb_store_round_trip(aws_resources: AwsResources) -> None:
    store = DynamoDBStore(aws_resources.users_table, aws_resources.items_table)

    alice = store.create_user("alice", "alice@example.com", "hash-a")
    bob = store.create_user("bob", "bob@example.com", "hash-b")
    assert (alice.id, bob.id) == (1, 2)
    assert store.get_user_by_id(alice.id) == alice
    assert store.get_user_by_username("bob") == bob
    assert store.username_exists("alice") and store.email_exists("bob@example.com")
    assert not store.username_exists("carol")
    assert store.get_user_by_id(999) is None

    now = datetime.now(UTC)
    created = [
        store.create_item({
            "title": f"item {n}", "description": None, "priority": "low",
            "status": "pending", "owner_id": alice.id if n < 3 else bob.id,
            "created_at": now, "updated_at": None,
        })
        for n in range(5)
    ]
    assert [i["id"] for i in created] == [1, 2, 3, 4, 5]
    assert store.get_item(1)["created_at"] == now
    assert store.get_item(0) is None  # counter row is never exposed

    page, total = store.list_items(page=2, page_size=2)
    assert total == 5 and [i["id"] for i in page] == [3, 4]
    _, alice_total = store.list_items(owner_id=alice.id)
    assert alice_total == 3

    updated = store.update_item(2, {"status": "completed", "description": None})
    assert updated["status"] == "completed"
    assert store.get_item(2)["status"] == "completed"
    assert store.update_item(42, {"status": "completed"}) is None

    assert store.delete_item(2) is True
    assert store.delete_item(2) is False
    assert store.get_item(2) is None


def test_item_lifecycle_via_api_uses_dynamodb_s3_and_sqs(
    aws_app_client: TestClient, aws_resources: AwsResources
) -> None:
    headers = _register_and_login(aws_app_client, "owner")

    # create
    response = aws_app_client.post(
        f"{API}/items", json={"title": "Rotate keys", "priority": "high"}, headers=headers
    )
    assert response.status_code == 201, response.text
    item_id = response.json()["id"]

    # the item really lives in DynamoDB
    record = get_client("dynamodb").get_item(
        TableName=aws_resources.items_table, Key={"id": {"N": str(item_id)}}
    )["Item"]
    assert record["title"]["S"] == "Rotate keys"

    # read + list through the API
    assert aws_app_client.get(f"{API}/items/{item_id}").json()["priority"] == "high"
    listing = aws_app_client.get(f"{API}/items").json()
    assert listing["total"] == 1

    # update
    response = aws_app_client.patch(
        f"{API}/items/{item_id}", json={"status": "in_progress"}, headers=headers
    )
    assert response.status_code == 200 and response.json()["status"] == "in_progress"
    assert response.json()["updated_at"] is not None

    # delete
    assert aws_app_client.delete(f"{API}/items/{item_id}", headers=headers).status_code == 204
    assert aws_app_client.get(f"{API}/items/{item_id}").status_code == 404

    # one SQS event per change, in order
    events = _receive_events(aws_resources.queue_url, expected=3)
    bodies = [json.loads(m["Body"]) for m in events]
    assert [b["event_type"] for b in bodies] == ["item.created", "item.updated", "item.deleted"]
    assert all(b["item_id"] == item_id for b in bodies)
    assert bodies[1]["changed_fields"] == ["status", "updated_at"]
    assert [m["MessageAttributes"]["event_type"]["StringValue"] for m in events] == [
        b["event_type"] for b in bodies
    ]

    # one S3 audit record per change
    assert len(_audit_objects(aws_resources.bucket)) == 3


def test_audit_records_are_encrypted_and_contain_no_item_content(
    aws_app_client: TestClient, aws_resources: AwsResources
) -> None:
    headers = _register_and_login(aws_app_client, "auditor")
    response = aws_app_client.post(
        f"{API}/items",
        json={"title": "SENSITIVE-TITLE", "description": "SENSITIVE-DESCRIPTION"},
        headers=headers,
    )
    assert response.status_code == 201

    (obj,) = _audit_objects(aws_resources.bucket)
    s3 = get_client("s3")
    head = s3.head_object(Bucket=aws_resources.bucket, Key=obj["Key"])
    assert head["ServerSideEncryption"] == "AES256"

    body = s3.get_object(Bucket=aws_resources.bucket, Key=obj["Key"])["Body"].read().decode()
    assert "SENSITIVE" not in body
    assert json.loads(body)["event_type"] == "item.created"


def test_ownership_enforced_and_forbidden_changes_emit_no_events(
    aws_app_client: TestClient, aws_resources: AwsResources
) -> None:
    owner = _register_and_login(aws_app_client, "owner2")
    intruder = _register_and_login(aws_app_client, "intruder")
    item_id = aws_app_client.post(
        f"{API}/items", json={"title": "mine"}, headers=owner
    ).json()["id"]

    assert aws_app_client.delete(f"{API}/items/{item_id}", headers=intruder).status_code == 403
    assert aws_app_client.patch(
        f"{API}/items/{item_id}", json={"title": "pwned"}, headers=intruder
    ).status_code == 403

    assert aws_app_client.get(f"{API}/items/{item_id}").json()["title"] == "mine"
    events = _receive_events(aws_resources.queue_url, expected=2, timeout_s=3)
    assert [json.loads(m["Body"])["event_type"] for m in events] == ["item.created"]


def test_duplicate_registration_rejected_with_dynamodb_backend(
    aws_app_client: TestClient,
) -> None:
    _register_and_login(aws_app_client, "dupe")
    response = aws_app_client.post(
        f"{API}/auth/register",
        json={"username": "dupe", "email": "other@example.com", "password": _random_password()},
    )
    assert response.status_code == 409
