from datetime import datetime, timedelta, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.dependencies import database_dependency, require_admin
from backend.routers.management_helpers import paginated, serialize_package, serialize_subscription, valid_object_id
from backend.schemas import PackageCreate, PackageUpdate, SubscriptionCreate

router = APIRouter(prefix="/api/v1/admin", tags=["Paquetes y suscripciones"])


@router.post("/packages", status_code=status.HTTP_201_CREATED)
def create_package(data: PackageCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    channel_ids = [ObjectId(value) for value in data.channel_ids]
    if channel_ids and database.channels.count_documents({"_id": {"$in": channel_ids}, "is_active": True}) != len(set(channel_ids)):
        raise HTTPException(status_code=422, detail="Every package channel must exist and be active.")
    now = datetime.now(timezone.utc)
    document = data.model_dump(exclude={"channel_ids"})
    document.update(channel_ids=channel_ids, is_active=True, created_at=now, updated_at=now)
    try:
        result = database.packages.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A package with this name already exists.") from exc
    document["_id"] = result.inserted_id
    return serialize_package(document)


@router.get("/packages")
def list_packages(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=200), active: bool | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    filters = {} if active is None else {"is_active": active}
    result = paginated(database.packages, filters, page, page_size, [("name", ASCENDING)])
    result["items"] = [serialize_package(item) for item in result["items"]]
    return result


@router.patch("/packages/{package_id}")
def update_package(package_id: str, data: PackageUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    object_id = valid_object_id(package_id)
    if object_id is None:
        raise HTTPException(status_code=404, detail="Package not found.")
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    if "channel_ids" in updates:
        channel_ids = [ObjectId(value) for value in updates.pop("channel_ids")]
        if channel_ids and database.channels.count_documents({"_id": {"$in": channel_ids}, "is_active": True}) != len(set(channel_ids)):
            raise HTTPException(status_code=422, detail="Every package channel must exist and be active.")
        updates["channel_ids"] = channel_ids
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        document = database.packages.find_one_and_update({"_id": object_id}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A package with this name already exists.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Package not found.")
    return serialize_package(document)


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
def create_subscription(data: SubscriptionCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    customer_id, package_id = ObjectId(data.customer_id), ObjectId(data.package_id)
    customer = database.customers.find_one({"_id": customer_id, "is_active": True})
    package = database.packages.find_one({"_id": package_id, "is_active": True})
    if customer is None:
        raise HTTPException(status_code=404, detail="Active customer not found.")
    if package is None:
        raise HTTPException(status_code=404, detail="Active package not found.")
    now = datetime.now(timezone.utc)
    starts_at = data.starts_at or now
    document = {
        "customer_id": customer_id,
        "package_id": package_id,
        "starts_at": starts_at,
        "expires_at": starts_at + timedelta(days=package["duration_days"]),
        "created_at": now,
    }
    result = database.subscriptions.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize_subscription(document)


@router.get("/subscriptions")
def list_subscriptions(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), state: str = Query("all", pattern="^(all|active|expired|expiring)$"), customer_id: str | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    filters = {}
    if state == "active":
        filters["expires_at"] = {"$gt": now}
    elif state == "expired":
        filters["expires_at"] = {"$lte": now}
    elif state == "expiring":
        filters["expires_at"] = {"$gt": now, "$lte": now + timedelta(days=7)}
    if customer_id:
        object_id = valid_object_id(customer_id)
        if object_id is None:
            raise HTTPException(status_code=422, detail="customer_id must be a valid ID.")
        filters["customer_id"] = object_id
    result = paginated(database.subscriptions, filters, page, page_size, [("expires_at", ASCENDING)])
    items = []
    for item in result["items"]:
        customer = database.customers.find_one({"_id": item["customer_id"]}, {"name": 1})
        package = database.packages.find_one({"_id": item["package_id"]}, {"name": 1})
        output = serialize_subscription(item)
        output["customer_name"] = customer.get("name") if customer else None
        output["package_name"] = package.get("name") if package else None
        items.append(output)
    result["items"] = items
    return result
