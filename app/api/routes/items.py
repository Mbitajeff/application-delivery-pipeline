"""
Items resource routes.

GET    /items          — list items (public: anyone can read)
GET    /items/{id}     — get a single item (public)
POST   /items          — create an item (requires auth)
PATCH  /items/{id}     — update an item (requires auth + ownership)
DELETE /items/{id}     — delete an item (requires auth + ownership)

Security design:
- Read endpoints are intentionally public. This gives DAST surface to
  scan without needing auth, while the write endpoints test auth gates.
- Ownership is enforced on mutations: a user can only modify their own
  items. This prevents Insecure Direct Object Reference (IDOR).
- Pagination is enforced with hard limits to prevent scraping or
  accidental large queries.
"""

import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import InMemoryStore, get_current_user, get_db, get_event_publisher
from app.aws.events import ItemEventPublisher
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.item import ItemCreate, ItemListResponse, ItemResponse, ItemUpdate
from app.models.user import UserInDB

router = APIRouter(prefix="/items", tags=["items"])
logger = get_logger(__name__)


def _item_dict_to_response(data: dict) -> ItemResponse:
    """Convert raw store dict to a typed ItemResponse."""
    return ItemResponse(**data)


@router.get(
    "",
    response_model=ItemListResponse,
    summary="List items",
    description="Returns a paginated list of all items. No authentication required.",
)
async def list_items(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    db: InMemoryStore = Depends(get_db),
) -> ItemListResponse:
    """List all items with pagination."""
    settings = get_settings()

    # Enforce server-side page size limit regardless of query param
    page_size = min(page_size, settings.max_page_size)

    items_data, total = db.list_items(page=page, page_size=page_size)
    pages = math.ceil(total / page_size) if total > 0 else 1

    return ItemListResponse(
        items=[_item_dict_to_response(i) for i in items_data],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{item_id}",
    response_model=ItemResponse,
    summary="Get a single item",
    description="Returns a single item by ID. No authentication required.",
)
async def get_item(
    item_id: int,
    db: InMemoryStore = Depends(get_db),
) -> ItemResponse:
    """Get a single item by ID."""
    item = db.get_item(item_id)

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found",
        )

    return _item_dict_to_response(item)


@router.post(
    "",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new item",
    description="Creates a new item owned by the authenticated user.",
)
async def create_item(
    item_data: ItemCreate,
    current_user: UserInDB = Depends(get_current_user),
    db: InMemoryStore = Depends(get_db),
    events: ItemEventPublisher = Depends(get_event_publisher),
) -> ItemResponse:
    """
    Create a new item.

    The owner_id is set from the authenticated user's identity —
    clients cannot set it directly (prevents IDOR at creation).
    """
    now = datetime.now(UTC)

    item = db.create_item({
        "title": item_data.title,
        "description": item_data.description,
        # Store enum values consistently as strings — not enum objects.
        # update_item also normalises to .value, so both paths are uniform.
        "priority": item_data.priority.value,
        "status": "pending",
        "owner_id": current_user.id,
        "created_at": now,
        "updated_at": None,
    })

    logger.info(
        "item_created",
        item_id=item["id"],
        owner_id=current_user.id,
        title=item_data.title,
    )

    events.publish("item.created", item_id=item["id"], actor_id=current_user.id)

    return _item_dict_to_response(item)


@router.patch(
    "/{item_id}",
    response_model=ItemResponse,
    summary="Update an item",
    description="Updates an item. Requires authentication and ownership.",
)
async def update_item(
    item_id: int,
    updates: ItemUpdate,
    current_user: UserInDB = Depends(get_current_user),
    db: InMemoryStore = Depends(get_db),
    events: ItemEventPublisher = Depends(get_event_publisher),
) -> ItemResponse:
    """
    Update an item (PATCH semantics — only provided fields are changed).

    Returns 404 if the item doesn't exist.
    Returns 403 if the authenticated user doesn't own the item.
    """
    existing = db.get_item(item_id)

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found",
        )

    # Ownership check — prevents IDOR
    if existing["owner_id"] != current_user.id:
        logger.warning(
            "item_update_forbidden",
            item_id=item_id,
            requesting_user=current_user.id,
            owner=existing["owner_id"],
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this item",
        )

    update_data = updates.model_dump(exclude_none=True)
    update_data["updated_at"] = datetime.now(UTC)

    # Convert enums to their values for storage
    if "priority" in update_data:
        update_data["priority"] = update_data["priority"].value
    if "status" in update_data:
        update_data["status"] = update_data["status"].value

    updated = db.update_item(item_id, update_data)

    logger.info("item_updated", item_id=item_id, fields=list(update_data.keys()))

    events.publish(
        "item.updated",
        item_id=item_id,
        actor_id=current_user.id,
        changed_fields=list(update_data.keys()),
    )

    return _item_dict_to_response(updated)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an item",
    description="Deletes an item. Requires authentication and ownership.",
)
async def delete_item(
    item_id: int,
    current_user: UserInDB = Depends(get_current_user),
    db: InMemoryStore = Depends(get_db),
    events: ItemEventPublisher = Depends(get_event_publisher),
) -> None:
    """
    Delete an item.

    Returns 404 if the item doesn't exist.
    Returns 403 if the authenticated user doesn't own the item.
    Returns 204 (no content) on success.
    """
    existing = db.get_item(item_id)

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found",
        )

    # Ownership check
    if existing["owner_id"] != current_user.id:
        logger.warning(
            "item_delete_forbidden",
            item_id=item_id,
            requesting_user=current_user.id,
            owner=existing["owner_id"],
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to delete this item",
        )

    db.delete_item(item_id)

    logger.info("item_deleted", item_id=item_id, owner_id=current_user.id)

    events.publish("item.deleted", item_id=item_id, actor_id=current_user.id)
