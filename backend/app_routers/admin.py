from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app_database import get_db
from backend.app_dependencies import require_admin
from backend.app_models import Channel, Package, Subscription, User
from backend.app_services.subscription_service import utc_now


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["Administración"],
)


@router.get("/overview")
def overview(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    now = utc_now()

    customers = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == "customer")
    ) or 0

    active_customers = db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.role == "customer",
            User.is_active.is_(True),
        )
    ) or 0

    packages = db.scalar(
        select(func.count()).select_from(Package)
    ) or 0

    channels = db.scalar(
        select(func.count())
        .select_from(Channel)
        .where(Channel.enabled.is_(True))
    ) or 0

    subscriptions = db.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.active.is_(True),
            Subscription.starts_at <= now,
            Subscription.expires_at > now,
        )
    ) or 0

    return {
        "customers": customers,
        "active_customers": active_customers,
        "packages": packages,
        "enabled_channels": channels,
        "active_subscriptions": subscriptions,
    }

