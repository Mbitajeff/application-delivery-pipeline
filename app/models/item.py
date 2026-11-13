"""
Item data models.

Items are the core resource of this API — a simple task/item tracker
with enough fields to give DAST meaningful endpoints to probe.

Priority levels and status enums are explicitly defined to prevent
injection of arbitrary values.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Priority(str, Enum):
    """Item priority levels. Explicit enum prevents arbitrary string injection."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ItemStatus(str, Enum):
    """Item lifecycle states."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ItemBase(BaseModel):
    """Fields shared across item representations."""

    title: str = Field(
        min_length=1,
        max_length=200,
        description="Item title",
        examples=["Fix login bug", "Add rate limiting"],
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional detailed description",
    )
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Item priority",
    )


class ItemCreate(ItemBase):
    """Input model for creating a new item."""

    # owner_id is set server-side from the authenticated user,
    # not accepted from the client — prevents IDOR at creation
    pass


class ItemUpdate(BaseModel):
    """
    Input model for updating an item.

    All fields optional — PATCH semantics. Only provided fields
    are updated, preventing accidental null overwrites.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority | None = None
    status: ItemStatus | None = None


class ItemResponse(ItemBase):
    """Public-facing item representation."""

    id: int = Field(description="Unique item identifier")
    status: ItemStatus = Field(default=ItemStatus.PENDING)
    owner_id: int = Field(description="ID of the user who created this item")
    created_at: datetime = Field(description="Creation timestamp (UTC)")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp (UTC)")

    model_config = {"from_attributes": True}


class ItemListResponse(BaseModel):
    """Paginated list of items."""

    items: list[ItemResponse]
    total: int = Field(description="Total number of items matching the query")
    page: int = Field(description="Current page number (1-indexed)")
    page_size: int = Field(description="Number of items per page")
    pages: int = Field(description="Total number of pages")
