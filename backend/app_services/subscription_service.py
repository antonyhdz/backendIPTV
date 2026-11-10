from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app_models import Package, Subscription, User


def utc_now() -> datetime:
    """Obtiene la hora UTC en el formato utilizado por SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_active_subscription(
    db: Session,
    customer_id: int,
) -> Subscription | None:
    """Busca una suscripción vigente del cliente."""

    now = utc_now()

    return db.scalar(
        select(Subscription)
        .where(
            Subscription.customer_id == customer_id,
            Subscription.active.is_(True),
            Subscription.starts_at <= now,
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
    )


def has_active_subscription(
    db: Session,
    customer_id: int,
) -> bool:
    """Indica si el cliente tiene una suscripción vigente."""

    return get_active_subscription(db, customer_id) is not None


def renew_subscription(
    db: Session,
    customer: User,
    package: Package,
) -> Subscription:
    """Crea una suscripción nueva o programa una renovación."""

    if customer.role != "customer":
        raise ValueError("La cuenta indicada no es un cliente.")

    if not customer.is_active:
        raise ValueError("El cliente está desactivado.")

    if not package.active:
        raise ValueError("El paquete está desactivado.")

    now = utc_now()

    latest = db.scalar(
        select(Subscription)
        .where(
            Subscription.customer_id == customer.id,
            Subscription.active.is_(True),
        )
        .order_by(Subscription.expires_at.desc())
    )

    # Si aún tiene tiempo disponible, la renovación comienza
    # cuando termine la suscripción más reciente.
    if latest is not None and latest.expires_at > now:
        starts_at = latest.expires_at
    else:
        starts_at = now

    subscription = Subscription(
        customer_id=customer.id,
        package_id=package.id,
        starts_at=starts_at,
        expires_at=starts_at + timedelta(days=package.duration_days),
        active=True,
    )

    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    return subscription

