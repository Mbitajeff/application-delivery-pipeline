"""
DynamoDB-backed data store.

Drop-in replacement for InMemoryStore (same method names and return
shapes), selected with STORAGE_BACKEND=dynamodb. Tables are provisioned
outside the application (IaC in real environments, pytest fixtures against
the AWS Emulator) and are never created here.

Table layout (both tables: partition key "id", type N):
    users : one item per user
    items : one item per item
Each table also holds a counter row with id = 0 that issues new ids via an
atomic UpdateItem ADD. Scans filter it out with id > 0.

Unlike InMemoryStore, this store does NOT seed a default admin user: demo
credentials must never be written to a persistent database.

Username/email lookups use filtered scans, which is fine for a demo-sized
table. A production design would add GSIs or uniqueness rows.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr

from app.aws.clients import get_resource
from app.models.user import UserInDB

_COUNTER_ID = 0
_DATETIME_FIELDS = ("created_at", "updated_at")


def _to_python(value: Any) -> Any:
    """Convert DynamoDB Decimal numbers back to int."""
    if isinstance(value, Decimal):
        return int(value)
    return value


class DynamoDBStore:
    """Data store backed by two DynamoDB tables."""

    def __init__(self, users_table: str, items_table: str) -> None:
        dynamodb = get_resource("dynamodb")
        self._users = dynamodb.Table(users_table)
        self._items = dynamodb.Table(items_table)

    # --- helpers ---

    @staticmethod
    def _next_id(table: Any) -> int:
        response = table.update_item(
            Key={"id": _COUNTER_ID},
            UpdateExpression="ADD next_id :one",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(response["Attributes"]["next_id"])

    @staticmethod
    def _scan(table: Any, filter_expression: Any) -> list[dict]:
        """Scan the whole table (all pages) with a filter, excluding the counter row."""
        expression = Attr("id").gt(_COUNTER_ID) & filter_expression
        kwargs: dict[str, Any] = {"FilterExpression": expression}
        results: list[dict] = []
        while True:
            page = table.scan(**kwargs)
            results.extend(page.get("Items", []))
            last_key = page.get("LastEvaluatedKey")
            if not last_key:
                return results
            kwargs["ExclusiveStartKey"] = last_key

    @staticmethod
    def _user_from_record(record: dict) -> UserInDB:
        return UserInDB(**{k: _to_python(v) for k, v in record.items()})

    @staticmethod
    def _item_to_record(item: dict) -> dict:
        record = {}
        for key, value in item.items():
            if value is None:
                continue  # DynamoDB: omit absent attributes rather than store NULL
            if isinstance(value, datetime):
                value = value.isoformat()
            record[key] = value
        return record

    @staticmethod
    def _item_from_record(record: dict) -> dict:
        item = {k: _to_python(v) for k, v in record.items()}
        for field in _DATETIME_FIELDS:
            raw = item.get(field)
            item[field] = datetime.fromisoformat(raw) if raw else None
        item.setdefault("description", None)
        return item

    # --- User operations ---

    def get_user_by_id(self, user_id: int) -> UserInDB | None:
        record = self._users.get_item(Key={"id": user_id}, ConsistentRead=True).get("Item")
        return self._user_from_record(record) if record else None

    def get_user_by_username(self, username: str) -> UserInDB | None:
        matches = self._scan(self._users, Attr("username").eq(username))
        return self._user_from_record(matches[0]) if matches else None

    def create_user(self, username: str, email: str, hashed_password: str) -> UserInDB:
        user = UserInDB(
            id=self._next_id(self._users),
            username=username,
            email=email,
            hashed_password=hashed_password,
        )
        self._users.put_item(
            Item=user.model_dump(),
            ConditionExpression=Attr("id").not_exists(),
        )
        return user

    def username_exists(self, username: str) -> bool:
        return bool(self._scan(self._users, Attr("username").eq(username)))

    def email_exists(self, email: str) -> bool:
        return bool(self._scan(self._users, Attr("email").eq(email)))

    # --- Item operations ---

    def get_item(self, item_id: int) -> dict | None:
        if item_id <= _COUNTER_ID:
            return None
        record = self._items.get_item(Key={"id": item_id}, ConsistentRead=True).get("Item")
        return self._item_from_record(record) if record else None

    def list_items(
        self,
        owner_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        condition = Attr("owner_id").eq(owner_id) if owner_id is not None else Attr("id").exists()
        records = sorted(self._scan(self._items, condition), key=lambda r: int(r["id"]))
        total = len(records)
        start = (page - 1) * page_size
        return [self._item_from_record(r) for r in records[start:start + page_size]], total

    def create_item(self, data: dict) -> dict:
        item = {**data, "id": self._next_id(self._items)}
        self._items.put_item(
            Item=self._item_to_record(item),
            ConditionExpression=Attr("id").not_exists(),
        )
        return self._item_from_record(self._item_to_record(item))

    def update_item(self, item_id: int, updates: dict) -> dict | None:
        existing = self.get_item(item_id)
        if existing is None:
            return None
        existing.update({k: v for k, v in updates.items() if v is not None})
        self._items.put_item(
            Item=self._item_to_record(existing),
            ConditionExpression=Attr("id").exists(),
        )
        return existing

    def delete_item(self, item_id: int) -> bool:
        if item_id <= _COUNTER_ID:
            return False
        response = self._items.delete_item(Key={"id": item_id}, ReturnValues="ALL_OLD")
        return "Attributes" in response
