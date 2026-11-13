from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import BulkWriteError, DuplicateKeyError

from backend.dependencies import database_dependency, require_admin
from backend.routers.management_helpers import channel_identity, paginated, serialize_channel, valid_object_id
from backend.schemas import ChannelCreate, ChannelUpdate
from backend.services.m3u_import import MAX_PLAYLIST_ENTRIES, MAX_PLAYLIST_SIZE, parse_playlist

router = APIRouter(prefix="/api/v1/admin/channels", tags=["Canales"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_channel(data: ChannelCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    document = data.model_dump()
    document["group_title"] = document["group_title"] or document.get("category") or ""
    document["category"] = document["group_title"]
    document["tvg_logo"] = document["tvg_logo"] or document.get("logo_url") or ""
    document["logo_url"] = document["tvg_logo"]
    document["url"] = document["stream_url"]
    identity = channel_identity(document["name"], document["url"])
    if identity:
        document["channel_key"] = identity
    document.update(is_active=True, created_at=now, updated_at=now)
    try:
        result = database.channels.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A channel with this name and URL already exists.") from exc
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
    if "category" in updates and "group_title" not in updates:
        updates["group_title"] = updates["category"] or ""
    if "group_title" in updates:
        updates["category"] = updates["group_title"] or ""
    if "logo_url" in updates and "tvg_logo" not in updates:
        updates["tvg_logo"] = updates["logo_url"] or ""
    if "tvg_logo" in updates:
        updates["logo_url"] = updates["tvg_logo"] or ""
    current = database.channels.find_one({"_id": object_id})
    if current is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    if "stream_url" in updates:
        updates["url"] = updates["stream_url"]
    next_name = updates.get("name", current.get("name", ""))
    next_url = updates.get("url", current.get("url", current.get("stream_url", "")))
    identity = channel_identity(next_name, next_url)
    update_doc = {"$set": updates}
    if identity:
        updates["channel_key"] = identity
    else:
        update_doc["$unset"] = {"channel_key": ""}
    try:
        document = database.channels.find_one_and_update({"_id": object_id}, update_doc, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A channel with this name and URL already exists.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return serialize_channel(document)


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_playlist(
    file: UploadFile = File(...),
    database: Database = Depends(database_dependency),
    _: dict = Depends(require_admin),
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".m3u", ".m3u8")):
        raise HTTPException(status_code=415, detail="Upload a .m3u or .m3u8 playlist file.")
    content = await file.read(MAX_PLAYLIST_SIZE + 1)
    await file.close()
    if len(content) > MAX_PLAYLIST_SIZE:
        raise HTTPException(status_code=413, detail="Playlist exceeds the 10 MB limit.")
    try:
        channels = parse_playlist(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not channels:
        raise HTTPException(status_code=422, detail="The playlist contains no channel entries.")
    if len(channels) > MAX_PLAYLIST_ENTRIES:
        raise HTTPException(status_code=413, detail="Playlist exceeds the 5,000 entry limit.")

    now = datetime.now(timezone.utc)
    documents = []
    for channel in channels:
        channel["url"] = channel["stream_url"]
        channel["category"] = channel["group_title"]
        channel["logo_url"] = channel["tvg_logo"]
        identity = channel_identity(channel["name"], channel["url"])
        if identity:
            channel["channel_key"] = identity
        channel.update(is_active=True, created_at=now, updated_at=now)
        documents.append(channel)

    errors = []
    write_errors = []
    try:
        result = database.channels.insert_many(documents, ordered=False)
        imported_count = len(result.inserted_ids)
    except BulkWriteError as exc:
        details = exc.details or {}
        write_errors = details.get("writeErrors", [])
        imported_count = details.get("nInserted", 0)
        for item in write_errors[:50]:
            errors.append({
                "entry": item.get("index", -1) + 1,
                "error": "duplicate_channel" if item.get("code") == 11000 else "insert_failed",
            })
    return {
        "filename": file.filename,
        "total_entries": len(documents),
        "imported": imported_count,
        "skipped": len(documents) - imported_count,
        "errors": errors,
        "errors_truncated": len(write_errors) > 50,
    }
