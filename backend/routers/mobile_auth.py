import hashlib
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.dependencies import database_dependency, get_current_customer
from backend.routers.management_helpers import serialize_channel, serialize_package, serialize_subscription
from backend.schemas import MobileLoginRequest, MobileTokenResponse
from backend.security import create_access_token, verify_password

router = APIRouter(prefix="/api/v1/mobile", tags=["Aplicación móvil de clientes"])


def _register_or_touch_device(
    database: Database, customer: dict, device_uuid: str, device_name: str | None
) -> int:
    now = datetime.now(timezone.utc)
    devices = customer.get("devices", [])
    existing = next((device for device in devices if device.get("device_uuid") == device_uuid), None)

    if existing:
        fields = {"devices.$.last_seen": now}
        if device_name:
            fields["devices.$.device_name"] = device_name
        database.customers.update_one(
            {"_id": customer["_id"], "devices.device_uuid": device_uuid},
            {"$set": fields},
        )
        return len(devices)

    limit = customer.get("max_devices", 1)
    if database.customers.find_one(
        {"_id": {"$ne": customer["_id"]}, "devices.device_uuid": device_uuid},
        {"_id": 1},
    ):
        raise HTTPException(status_code=409, detail="device_already_linked")
    try:
        result = database.customers.update_one(
            {
                "_id": customer["_id"],
                "is_active": True,
                "devices.device_uuid": {"$ne": device_uuid},
                "$expr": {
                    "$lt": [
                        {"$size": {"$ifNull": ["$devices", []]}},
                        {"$ifNull": ["$max_devices", 1]},
                    ]
                },
            },
            {
                "$push": {
                    "devices": {
                        "device_uuid": device_uuid,
                        "device_name": device_name,
                        "registered_at": now,
                        "last_seen": now,
                    }
                },
                "$set": {"updated_at": now},
            },
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="device_already_linked") from exc
    if result.modified_count:
        return len(devices) + 1

    # A parallel login may have registered the same UUID while this request ran.
    refreshed = database.customers.find_one(
        {"_id": customer["_id"], "devices.device_uuid": device_uuid}
    )
    if refreshed:
        fields = {"devices.$.last_seen": now}
        if device_name:
            fields["devices.$.device_name"] = device_name
        database.customers.update_one(
            {"_id": customer["_id"], "devices.device_uuid": device_uuid},
            {"$set": fields},
        )
        return len(refreshed.get("devices", []))
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="device_limit_reached",
    )


@router.post("/auth/login", response_model=MobileTokenResponse)
def mobile_login(
    data: MobileLoginRequest,
    request: Request,
    database: Database = Depends(database_dependency),
):
    username = data.username.strip().lower()
    device_uuid = str(data.device_uuid)
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = hashlib.sha256(
        f"mobile\0{username}\0{client_ip}".encode("utf-8")
    ).hexdigest()
    now = datetime.now(timezone.utc)
    attempts = database.auth_attempts.find_one({"_id": attempt_key})
    if attempts and attempts.get("blocked_until") and attempts["blocked_until"] > now:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    if attempts and attempts.get("expires_at") and attempts["expires_at"] <= now:
        database.auth_attempts.delete_one({"_id": attempt_key})

    customer = database.customers.find_one({"username": username})
    password_hash = customer.get("password_hash") if customer else None
    if not verify_password(data.password, password_hash) or not customer or not customer.get("is_active", False):
        database.auth_attempts.update_one(
            {"_id": attempt_key},
            {
                "$inc": {"attempts": 1},
                "$setOnInsert": {"expires_at": now + timedelta(minutes=10)},
            },
            upsert=True,
        )
        current_attempts = database.auth_attempts.find_one({"_id": attempt_key})
        if current_attempts and current_attempts.get("attempts", 0) >= 8:
            blocked_until = now + timedelta(minutes=10)
            database.auth_attempts.update_one(
                {"_id": attempt_key},
                {"$set": {"blocked_until": blocked_until, "expires_at": blocked_until}},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    registered_devices = _register_or_touch_device(
        database, customer, device_uuid, data.device_name
    )
    database.auth_attempts.delete_one({"_id": attempt_key})
    token, expires_in = create_access_token(
        str(customer["_id"]), username, "customer", device_uuid=device_uuid
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": {
            "id": str(customer["_id"]),
            "username": username,
            "role": "customer",
            "is_active": True,
        },
        "device_uuid": device_uuid,
        "registered_devices": registered_devices,
        "max_devices": customer.get("max_devices", 1),
    }


@router.get("/me")
def mobile_me(
    customer: dict = Depends(get_current_customer),
    database: Database = Depends(database_dependency),
):
    now = datetime.now(timezone.utc)
    active_count = database.subscriptions.count_documents(
        {
            "customer_id": customer["_id"],
            "starts_at": {"$lte": now},
            "expires_at": {"$gt": now},
        }
    )
    return {
        "id": str(customer["_id"]),
        "name": customer["name"],
        "username": customer["username"],
        "active_subscriptions": active_count,
        "device_limit": customer.get("max_devices", 1),
        "generated_at": now,
    }


def _active_subscriptions(database: Database, customer_id: ObjectId, now: datetime) -> list[dict]:
    return list(
        database.subscriptions.find(
            {
                "customer_id": customer_id,
                "starts_at": {"$lte": now},
                "expires_at": {"$gt": now},
            }
        ).sort("expires_at", 1)
    )


@router.get("/me/packages")
def mobile_packages(
    customer: dict = Depends(get_current_customer),
    database: Database = Depends(database_dependency),
):
    now = datetime.now(timezone.utc)
    items = []
    for subscription in _active_subscriptions(database, customer["_id"], now):
        package = database.packages.find_one(
            {"_id": subscription["package_id"], "is_active": True}
        )
        if package:
            items.append(
                {
                    "subscription": serialize_subscription(subscription),
                    "package": serialize_package(package),
                }
            )
    return {"items": items, "total": len(items)}


@router.get("/me/channels")
def mobile_channels(
    customer: dict = Depends(get_current_customer),
    database: Database = Depends(database_dependency),
):
    now = datetime.now(timezone.utc)
    subscriptions = _active_subscriptions(database, customer["_id"], now)
    package_ids = list({item["package_id"] for item in subscriptions})
    packages = list(
        database.packages.find(
            {"_id": {"$in": package_ids}, "is_active": True}, {"channel_ids": 1}
        )
    )
    channel_ids = list({channel_id for package in packages for channel_id in package.get("channel_ids", [])})
    channels = list(
        database.channels.find(
            {"_id": {"$in": channel_ids}, "is_active": True}
        ).sort("name", 1)
    ) if channel_ids else []
    return {"items": [serialize_channel(channel) for channel in channels], "total": len(channels)}
