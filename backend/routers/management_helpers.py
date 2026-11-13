from datetime import datetime, timezone
import json

from bson import ObjectId


def channel_identity(name: str, url: str) -> str | None:
    if not name and not url:
        return None
    return json.dumps([name, url], ensure_ascii=False, separators=(",", ":"))


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
    group_title = document.get("group_title", document.get("category", "")) or ""
    tvg_logo = document.get("tvg_logo", document.get("logo_url", "")) or ""
    return {
        "id": str(document["_id"]),
        "name": document.get("name", "") or "",
        "url": document.get("url", document.get("stream_url", "")) or "",
        "stream_url": document.get("stream_url", document.get("url", "")) or "",
        "group_title": group_title,
        "category": group_title,
        "tvg_id": document.get("tvg_id", "") or "",
        "tvg_name": document.get("tvg_name", "") or "",
        "tvg_chno": document.get("tvg_chno", "") or "",
        "tvg_logo": tvg_logo,
        "logo_url": tvg_logo,
        "tvg_country": document.get("tvg_country", "") or "",
        "tvg_language": document.get("tvg_language", "") or "",
        "tvg_shift": document.get("tvg_shift", "") or "",
        "catchup": document.get("catchup", "") or "",
        "catchup_days": document.get("catchup_days", "") or "",
        "catchup_source": document.get("catchup_source", "") or "",
        "tvg_type": document.get("tvg_type", "") or "",
        "tvg_season": document.get("tvg_season", "") or "",
        "tvg_episode": document.get("tvg_episode", "") or "",
        "serie_title": document.get("serie_title", "") or "",
        "tvg_tmdb": document.get("tvg_tmdb", "") or "",
        "audio_track": document.get("audio_track", "") or "",
        "aspect_ratio": document.get("aspect_ratio", "") or "",
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
