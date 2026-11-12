from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pymongo import ASCENDING
from pymongo.database import Database

from backend.dependencies import database_dependency, require_admin
from backend.routers.management_helpers import serialize_subscription

router = APIRouter(prefix="/api/v1/admin/dashboard", tags=["Dashboard"])


@router.get("")
def get_dashboard(database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=7)
    total_customers = database.customers.count_documents({})
    active_customers = database.customers.count_documents({"is_active": True})
    active_subscriptions = database.subscriptions.count_documents({"expires_at": {"$gt": now}})
    expired_subscriptions = database.subscriptions.count_documents({"expires_at": {"$lte": now}})
    expiring_subscriptions = database.subscriptions.count_documents({"expires_at": {"$gt": now, "$lte": soon}})
    expiring = list(database.subscriptions.find({"expires_at": {"$gt": now, "$lte": soon}}).sort("expires_at", ASCENDING).limit(10))
    expiring_items = []
    for item in expiring:
        customer = database.customers.find_one({"_id": item["customer_id"]}, {"name": 1})
        package = database.packages.find_one({"_id": item["package_id"]}, {"name": 1})
        summary = serialize_subscription(item)
        summary["customer_name"] = customer.get("name") if customer else None
        summary["package_name"] = package.get("name") if package else None
        expiring_items.append(summary)
    return {
        "generated_at": now,
        "customers": {
            "total": total_customers,
            "active": active_customers,
            "inactive": total_customers - active_customers,
        },
        "subscriptions": {
            "active": active_subscriptions,
            "expired": expired_subscriptions,
            "expiring_in_7_days": expiring_subscriptions,
        },
        "catalog": {
            "active_channels": database.channels.count_documents({"is_active": True}),
            "active_packages": database.packages.count_documents({"is_active": True}),
        },
        "expiring_soon": expiring_items,
    }
