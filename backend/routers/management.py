from datetime import datetime, timedelta, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.dependencies import database_dependency, require_admin
from backend.schemas import (
    ChannelCreate,
    ChannelUpdate,
    CustomerCreate,
    CustomerUpdate,
    PackageCreate,
    PackageUpdate,
    SubscriptionCreate,
)

router = APIRouter(prefix="/api/v1/admin", tags=["Administración"])


def _id(value: ObjectId) -> str:
    return str(value)


def _customer(document: dict) -> dict:
    return {
        "id": _id(document["_id"]),
        "name": document["name"],
        "email": document.get("email"),
        "phone": document.get("phone"),
        "notes": document.get("notes"),
        "is_active": document["is_active"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def _channel(document: dict) -> dict:
    return {
        "id": _id(document["_id"]),
        "name": document["name"],
        "stream_url": document["stream_url"],
        "category": document.get("category"),
        "logo_url": document.get("logo_url"),
        "is_active": document["is_active"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def _package(document: dict) -> dict:
    return {
        "id": _id(document["_id"]),
        "name": document["name"],
        "description": document.get("description"),
        "price": document["price"],
        "duration_days": document["duration_days"],
        "channel_ids": [str(item) for item in document["channel_ids"]],
        "is_active": document["is_active"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def _subscription(document: dict) -> dict:
    return {
        "id": _id(document["_id"]),
        "customer_id": str(document["customer_id"]),
        "package_id": str(document["package_id"]),
        "starts_at": document["starts_at"],
        "expires_at": document["expires_at"],
        "status": "active" if document["expires_at"] > datetime.now(timezone.utc) else "expired",
        "created_at": document["created_at"],
    }


def _page(collection, filters: dict, page: int, page_size: int, sort: list) -> dict:
    total = collection.count_documents(filters)
    cursor = collection.find(filters).sort(sort).skip((page - 1) * page_size).limit(page_size)
    return {
        "items": list(cursor),
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/customers", status_code=status.HTTP_201_CREATED)
def create_customer(data: CustomerCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    document = data.model_dump()
    document.update(is_active=True, created_at=now, updated_at=now)
    try:
        result = database.customers.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email already belongs to a customer.") from exc
    document["_id"] = result.inserted_id
    return _customer(document)


@router.get("/customers")
def list_customers(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), search: str | None = None, active: bool | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    filters = {}
    if active is not None:
        filters["is_active"] = active
    if search and search.strip():
        escaped = search.strip()[:100].replace("\\", "\\\\").replace(".", "\\.").replace("*", "\\*")
        filters["$or"] = [{field: {"$regex": escaped, "$options": "i"}} for field in ("name", "email", "phone")]
    result = _page(database.customers, filters, page, page_size, [("created_at", DESCENDING)])
    result["items"] = [_customer(item) for item in result["items"]]
    return result


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: str, data: CustomerUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    if not ObjectId.is_valid(customer_id):
        raise HTTPException(status_code=404, detail="Customer not found.")
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        document = database.customers.find_one_and_update({"_id": ObjectId(customer_id)}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email already belongs to a customer.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return _customer(document)


@router.post("/channels", status_code=status.HTTP_201_CREATED)
def create_channel(data: ChannelCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    document = data.model_dump()
    document.update(is_active=True, created_at=now, updated_at=now)
    try:
        result = database.channels.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A channel with this name already exists.") from exc
    document["_id"] = result.inserted_id
    return _channel(document)


@router.get("/channels")
def list_channels(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=200), active: bool | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    filters = {} if active is None else {"is_active": active}
    result = _page(database.channels, filters, page, page_size, [("name", ASCENDING)])
    result["items"] = [_channel(item) for item in result["items"]]
    return result


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: str, data: ChannelUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    if not ObjectId.is_valid(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found.")
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        document = database.channels.find_one_and_update({"_id": ObjectId(channel_id)}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A channel with this name already exists.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return _channel(document)


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
    return _package(document)


@router.get("/packages")
def list_packages(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=200), active: bool | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    filters = {} if active is None else {"is_active": active}
    result = _page(database.packages, filters, page, page_size, [("name", ASCENDING)])
    result["items"] = [_package(item) for item in result["items"]]
    return result


@router.patch("/packages/{package_id}")
def update_package(package_id: str, data: PackageUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    if not ObjectId.is_valid(package_id):
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
        document = database.packages.find_one_and_update({"_id": ObjectId(package_id)}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="A package with this name already exists.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Package not found.")
    return _package(document)


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
    return _subscription(document)


@router.get("/subscriptions")
def list_subscriptions(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), state: str = Query("all", pattern="^(all|active|expired|expiring)$"), database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    filters = {}
    if state == "active":
        filters["expires_at"] = {"$gt": now}
    elif state == "expired":
        filters["expires_at"] = {"$lte": now}
    elif state == "expiring":
        filters["expires_at"] = {"$gt": now, "$lte": now + timedelta(days=7)}
    result = _page(database.subscriptions, filters, page, page_size, [("expires_at", ASCENDING)])
    items = []
    for item in result["items"]:
        customer = database.customers.find_one({"_id": item["customer_id"]}, {"name": 1})
        package = database.packages.find_one({"_id": item["package_id"]}, {"name": 1})
        item["customer_name"] = customer.get("name") if customer else None
        item["package_name"] = package.get("name") if package else None
        output = _subscription(item)
        output["customer_name"] = item["customer_name"]
        output["package_name"] = item["package_name"]
        items.append(output)
    result["items"] = items
    return result


@router.get("/dashboard")
def dashboard(database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=7)
    total_customers = database.customers.count_documents({})
    active_customers = database.customers.count_documents({"is_active": True})
    active_subscriptions = database.subscriptions.count_documents({"expires_at": {"$gt": now}})
    expired_subscriptions = database.subscriptions.count_documents({"expires_at": {"$lte": now}})
    expiring_subscriptions = database.subscriptions.count_documents({"expires_at": {"$gt": now, "$lte": soon}})
    active_channels = database.channels.count_documents({"is_active": True})
    active_packages = database.packages.count_documents({"is_active": True})
    expiring = list(database.subscriptions.find({"expires_at": {"$gt": now, "$lte": soon}}).sort("expires_at", ASCENDING).limit(10))
    expiring_items = []
    for item in expiring:
        customer = database.customers.find_one({"_id": item["customer_id"]}, {"name": 1})
        package = database.packages.find_one({"_id": item["package_id"]}, {"name": 1})
        summary = _subscription(item)
        summary["customer_name"] = customer.get("name") if customer else None
        summary["package_name"] = package.get("name") if package else None
        summary["customer_name"] = customer.get("name") if customer else None
        summary["package_name"] = package.get("name") if package else None
        expiring_items.append(summary)
    return {
        "generated_at": now,
        "customers": {"total": total_customers, "active": active_customers, "inactive": total_customers - active_customers},
        "subscriptions": {"active": active_subscriptions, "expired": expired_subscriptions, "expiring_in_7_days": expiring_subscriptions},
        "catalog": {"active_channels": active_channels, "active_packages": active_packages},
        "expiring_soon": expiring_items,
    }
