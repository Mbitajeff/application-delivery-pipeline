"""
Item change events: S3 audit trail and SQS notifications.

On every item create/update/delete the item routes call
ItemEventPublisher.publish(). Depending on settings it:

  - writes a JSON audit record to S3 (AUDIT_BUCKET), server-side encrypted
  - sends the same JSON as an SQS message (EVENTS_QUEUE_URL)

Data minimisation: events carry ids, the event type, a timestamp and the
NAMES of changed fields only. Item titles/descriptions are free text that
may contain sensitive data, so they are never copied into S3 or SQS.

Failure mode: fail closed. If the audit write or the event send fails the
exception propagates and the request returns 500, so a change is never
silently left without an audit record.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from app.aws.clients import get_client
from app.core.logging import get_logger

logger = get_logger(__name__)

EventType = Literal["item.created", "item.updated", "item.deleted"]


class ItemEventPublisher:
    """Publishes item change events to S3 and/or SQS."""

    def __init__(self, audit_bucket: str | None, queue_url: str | None) -> None:
        self.audit_bucket = audit_bucket
        self.queue_url = queue_url

    @property
    def enabled(self) -> bool:
        return bool(self.audit_bucket or self.queue_url)

    @staticmethod
    def build_event(
        event_type: EventType,
        item_id: int,
        actor_id: int,
        changed_fields: list[str] | None = None,
    ) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "item_id": item_id,
            "actor_id": actor_id,
            "changed_fields": sorted(changed_fields or []),
            "occurred_at": datetime.now(UTC).isoformat(),
        }

    def publish(
        self,
        event_type: EventType,
        item_id: int,
        actor_id: int,
        changed_fields: list[str] | None = None,
    ) -> dict | None:
        """Publish one event. Returns the event, or None if publishing is disabled."""
        if not self.enabled:
            return None

        event = self.build_event(event_type, item_id, actor_id, changed_fields)
        body = json.dumps(event, separators=(",", ":"))

        if self.audit_bucket:
            key = f"audit/items/{item_id}/{event['occurred_at']}-{event['event_id']}.json"
            get_client("s3").put_object(
                Bucket=self.audit_bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )

        if self.queue_url:
            get_client("sqs").send_message(
                QueueUrl=self.queue_url,
                MessageBody=body,
                MessageAttributes={
                    "event_type": {"DataType": "String", "StringValue": event_type},
                },
            )

        logger.info(
            "item_event_published",
            event_type=event_type,
            item_id=item_id,
            audit=bool(self.audit_bucket),
            queue=bool(self.queue_url),
        )
        return event
