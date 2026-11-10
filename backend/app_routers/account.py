from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app_database import get_db
from backend.app_dependencies import get_current_user
from backend.app_models import Package, User
from backend.app_services.subscription_service import get_active_subscription


router = APIRouter(
    prefix="/api/v1/account",
    tags=["Cuenta"],
)


@router.get("")
def get_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
        "subscription": None,
    }

    if user.role == "customer":
        subscription = get_active_subscription(db, user.id)

        if subscription:
            package = db.get(Package, subscription.package_id)

            result["subscription"] = {
                "id": subscription.id,
                "package": package.name if package else None,
                "starts_at": subscription.starts_at.isoformat(),
                "expires_at": subscription.expires_at.isoformat(),
                "active": subscription.active,
            }

    return result

