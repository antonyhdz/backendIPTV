from datetime import datetime, timezone

from bson import ObjectId


def serialize_customer(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "name": document["name"],
        "username": document.get("username"),
        "email": document.get("email"),
        "phone": document.get("phone"),
        "notes": document.get("notes"),
        "is_active": document["is_active"],
        "max_devices": document.get("max_devices", 1),
        "devices": document.get("devices", []),
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def serialize_channel(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "name": document["name"],
        "stream_url": document["stream_url"],
        "category": document.get("category"),
        "logo_url": document.get("logo_url"),
        "is_active": document["is_active"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def serialize_package(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "name": document["name"],
        "description": document.get("description"),
        "price": document["price"],
        "duration_days": document["duration_days"],
        "channel_ids": [str(item) for item in document["channel_ids"]],
        "is_active": document["is_active"],
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


def serialize_subscription(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "customer_id": str(document["customer_id"]),
        "package_id": str(document["package_id"]),
        "starts_at": document["starts_at"],
        "expires_at": document["expires_at"],
        "status": "active" if document["expires_at"] > datetime.now(timezone.utc) else "expired",
        "created_at": document["created_at"],
    }


def paginated(collection, filters: dict, page: int, page_size: int, sort: list) -> dict:
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


def valid_object_id(value: str) -> ObjectId | None:
    return ObjectId(value) if ObjectId.is_valid(value) else None
