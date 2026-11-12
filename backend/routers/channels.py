from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.dependencies import database_dependency, require_admin
from backend.routers.management_helpers import paginated, serialize_channel, valid_object_id
from backend.schemas import ChannelCreate, ChannelUpdate

router = APIRouter(prefix="/api/v1/admin/channels", tags=["Canales"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_channel(data: ChannelCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    document = data.model_dump()
    document.update(is_active=True, created_at=now, updated_at=now)
    try:
        result = database.channels.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A channel with this name already exists.") from exc
    document["_id"] = result.inserted_id
    return serialize_channel(document)


@router.get("")
def list_channels(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=200), active: bool | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    filters = {} if active is None else {"is_active": active}
    result = paginated(database.channels, filters, page, page_size, [("name", ASCENDING)])
    result["items"] = [serialize_channel(item) for item in result["items"]]
    return result


@router.patch("/{channel_id}")
def update_channel(channel_id: str, data: ChannelUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    object_id = valid_object_id(channel_id)
    if object_id is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        document = database.channels.find_one_and_update({"_id": object_id}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A channel with this name already exists.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return serialize_channel(document)
