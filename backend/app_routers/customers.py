from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from backend.app_database import get_db
from backend.app_dependencies import get_current_user, require_admin
from backend.app_models import AuthSession, Channel, ChannelFavorite, ChannelView, CustomerMessage, Package, Subscription, User, utc_now
from backend.app_schemas import CustomerCreate, CustomerStatusUpdate, CustomerUpdate
from backend.app_services.subscription_service import (
    get_active_subscription,
    renew_subscription,
)
from backend.app_services.auth_service import revoke_user_client_sessions
from backend.app_services.session_events import session_event_hub

BASE_DIR = Path(__file__).resolve().parents[2]
_panel_dir = (BASE_DIR / "panel_admin") if (BASE_DIR / "panel_admin").is_dir() else (BASE_DIR / "paneladmin")
NOTIFICATION_UPLOAD_DIR = _panel_dir / "uploads" / "notifications"
MAX_NOTIFICATION_IMAGE_BYTES = 5 * 1024 * 1024
NOTIFICATION_IMAGE_TYPES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/webp": (".webp", b"RIFF"),
}


router = APIRouter(
    prefix="/api/v1/admin/customers",
    tags=["Clientes"],
)
client_router = APIRouter(prefix="/api/v1/client", tags=["Cliente"])


def customer_response(
    db: Session,
    customer: User,
) -> dict:
    subscription = get_active_subscription(db, customer.id)
    now = utc_now()
    active_session = db.scalar(
        select(AuthSession)
        .where(
            AuthSession.user_id == customer.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .order_by(AuthSession.created_at.desc())
    )
    next_subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.customer_id == customer.id,
            Subscription.active.is_(True),
            Subscription.starts_at > now,
        )
        .order_by(Subscription.starts_at.asc())
    )

    subscription_data = None

    if subscription:
        package = db.get(Package, subscription.package_id)

        subscription_data = {
            "id": subscription.id,
            "package": package.name if package else None,
            "starts_at": subscription.starts_at.isoformat(),
            "expires_at": subscription.expires_at.isoformat(),
            "active": subscription.active,
        }

    next_subscription_data = None
    if next_subscription:
        package = db.get(Package, next_subscription.package_id)
        next_subscription_data = {
            "id": next_subscription.id,
            "package": package.name if package else None,
            "starts_at": next_subscription.starts_at.isoformat(),
            "expires_at": next_subscription.expires_at.isoformat(),
            "active": next_subscription.active,
        }

    return {
        "id": customer.id,
        "username": customer.username,
        "role": customer.role,
        "is_active": customer.is_active,
        "created_at": customer.created_at.isoformat(),
        "subscription": subscription_data,
        "next_subscription": next_subscription_data,
        "device_session": ({
            "active": True,
            "device_ref": active_session.device_id[-6:] if active_session.device_id else None,
            "created_at": active_session.created_at.isoformat(),
            "last_used_at": active_session.last_used_at.isoformat(),
            "expires_at": active_session.expires_at.isoformat(),
        } if active_session else {"active": False}),
    }


@router.patch("/{customer_id}")
async def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(User, customer_id)
    if customer is None or customer.role != "customer":
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    if not data.model_fields_set:
        raise HTTPException(status_code=400, detail="No se enviaron cambios.")

    if data.username is not None and data.username != customer.username:
        duplicate = db.scalar(select(User).where(User.username == data.username))
        if duplicate:
            raise HTTPException(status_code=409, detail="Ese nombre de usuario ya existe.")
        customer.username = data.username

    sessions_revoked = False
    if data.password is not None:
        from backend.app_security import hash_password
        customer.password_hash = hash_password(data.password)
        revoke_user_client_sessions(db, customer.id)
        sessions_revoked = True

    if "package_id" in data.model_fields_set:
        if data.package_id is None:
            db.query(Subscription).filter(
                Subscription.customer_id == customer.id,
                Subscription.active.is_(True),
            ).update({Subscription.active: False}, synchronize_session=False)
            db.commit()
        else:
            package = db.get(Package, data.package_id)
            if package is None or not package.active:
                db.rollback()
                raise HTTPException(status_code=404, detail="El paquete no existe o está desactivado.")
            if not customer.is_active:
                db.rollback()
                raise HTTPException(status_code=400, detail="Activa el cliente antes de asignarle un paquete.")

            now = utc_now()
            # Editing the subscription means switching packages now. Deactivate
            # both the current period and any queued periods so they cannot
            # unexpectedly take effect later.
            db.query(Subscription).filter(
                Subscription.customer_id == customer.id,
                Subscription.active.is_(True),
            ).update({Subscription.active: False}, synchronize_session=False)
            db.add(Subscription(
                customer_id=customer.id,
                package_id=package.id,
                starts_at=now,
                expires_at=now + timedelta(days=package.duration_days),
                active=True,
            ))
            db.commit()
    else:
        db.commit()

    db.refresh(customer)
    if sessions_revoked:
        await session_event_hub.notify_user(customer.id, "credentials_changed")
    return {"customer": customer_response(db, customer)}


@router.get("")
def list_customers(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customers = db.scalars(
        select(User)
        .where(User.role == "customer")
        .order_by(User.id.desc())
    ).all()

    return {
        "customers": [
            customer_response(db, customer)
            for customer in customers
        ]
    }


@router.get("/{customer_id}/detail")
def customer_detail(customer_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    customer = db.get(User, customer_id)
    if customer is None or customer.role != "customer":
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    data = customer_response(db, customer)
    favorite_channels = db.scalars(select(Channel).join(ChannelFavorite, ChannelFavorite.channel_id == Channel.id)
                                   .where(ChannelFavorite.user_id == customer.id).order_by(Channel.name)).all()
    popular_rows = db.execute(select(Channel, ChannelView.id).join(ChannelView, ChannelView.channel_id == Channel.id)
                              .where(ChannelView.user_id == customer.id).order_by(ChannelView.viewed_at.desc())).all()
    counts: dict[int, int] = {}
    channel_by_id = {}
    for channel, _view_id in popular_rows:
        counts[channel.id] = counts.get(channel.id, 0) + 1
        channel_by_id[channel.id] = channel
    data["favorites"] = [{"id": c.id, "name": c.name, "logo_url": c.logo_url} for c in favorite_channels]
    data["most_viewed"] = [{"id": c.id, "name": c.name, "logo_url": c.logo_url, "views": count}
                           for cid, count in sorted(counts.items(), key=lambda item: (-item[1], channel_by_id[item[0]].name))[:12]
                           for c in [channel_by_id[cid]]]
    messages = db.scalars(select(CustomerMessage).where(CustomerMessage.customer_id == customer.id)
                          .order_by(CustomerMessage.created_at.desc()).limit(100)).all()
    now = utc_now()
    changed = False
    for message in messages:
        if message.sender_role == "customer" and message.kind == "chat":
            if message.delivered_at is None:
                message.delivered_at = now
                changed = True
            if message.read_at is None:
                message.read_at = now
                changed = True
    if changed:
        db.commit()
    data["messages"] = [{"id": m.id, "sender_role": m.sender_role, "kind": m.kind, "subject": m.subject, "image_url": m.image_url, "body": m.body,
                         "created_at": m.created_at.isoformat(),
                         "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
                         "read_at": m.read_at.isoformat() if m.read_at else None}
                        for m in messages[::-1]]
    return data


@router.post("/{customer_id}/messages")
def send_customer_message(customer_id: int, payload: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    customer = db.get(User, customer_id)
    body = str(payload.get("body", "")).strip()
    kind = str(payload.get("kind", "chat"))
    subject = str(payload.get("subject", "")).strip() or None
    if customer is None or customer.role != "customer":
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    if not body or len(body) > 4000 or kind not in {"chat", "notification"}:
        raise HTTPException(status_code=400, detail="Mensaje o tipo no válido.")
    if kind == "notification" and (not subject or len(subject) > 200):
        raise HTTPException(status_code=400, detail="La notificación requiere un asunto de hasta 200 caracteres.")
    msg = CustomerMessage(customer_id=customer_id, sender_role="admin", kind=kind, subject=subject if kind == "notification" else None, body=body)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    import asyncio
    try:
        asyncio.get_running_loop().create_task(session_event_hub.push_user_event(customer_id, {
            "type": "notification" if kind == "notification" else "chat_message",
            "id": msg.id,
            "body": msg.body,
            "kind": msg.kind,
            "subject": msg.subject,
            "created_at": msg.created_at.isoformat(),
        }))
    except RuntimeError:
        pass
    return {"id": msg.id, "sender_role": msg.sender_role, "kind": msg.kind, "subject": msg.subject, "body": msg.body,
            "created_at": msg.created_at.isoformat(), "delivered_at": None, "read_at": None}


@router.post("/{customer_id}/notifications")
async def send_customer_notification(
    customer_id: int,
    subject: str = Form(..., max_length=200),
    body: str = Form(..., max_length=4000),
    image: UploadFile | None = File(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(User, customer_id)
    if customer is None or customer.role != "customer":
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    subject = subject.strip()
    body = body.strip()
    if not subject or not body:
        raise HTTPException(status_code=400, detail="Asunto y mensaje son obligatorios.")

    image_url = None
    saved_image: Path | None = None
    if image is not None and image.filename:
        image_type = (image.content_type or "").lower()
        if image_type not in NOTIFICATION_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Adjunta una imagen JPG, PNG o WEBP.")
        contents = await image.read(MAX_NOTIFICATION_IMAGE_BYTES + 1)
        if len(contents) > MAX_NOTIFICATION_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="La imagen no puede superar los 5 MB.")
        extension, signature = NOTIFICATION_IMAGE_TYPES[image_type]
        if not contents.startswith(signature) or (image_type == "image/webp" and contents[8:12] != b"WEBP"):
            raise HTTPException(status_code=400, detail="El archivo adjunto no parece ser una imagen válida.")
        NOTIFICATION_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        saved_image = NOTIFICATION_UPLOAD_DIR / f"{uuid4().hex}{extension}"
        saved_image.write_bytes(contents)
        image_url = f"/uploads/notifications/{saved_image.name}"

    msg = CustomerMessage(customer_id=customer_id, sender_role="admin", kind="notification",
                          subject=subject, body=body, image_url=image_url)
    try:
        db.add(msg)
        db.commit()
        db.refresh(msg)
    except Exception:
        db.rollback()
        if saved_image is not None:
            saved_image.unlink(missing_ok=True)
        raise

    import asyncio
    try:
        asyncio.get_running_loop().create_task(session_event_hub.push_user_event(customer_id, {
            "type": "notification", "id": msg.id, "kind": msg.kind, "subject": msg.subject,
            "body": msg.body, "image_url": msg.image_url, "created_at": msg.created_at.isoformat(),
        }))
    except RuntimeError:
        pass
    return {"id": msg.id, "sender_role": msg.sender_role, "kind": msg.kind, "subject": msg.subject,
            "body": msg.body, "image_url": msg.image_url, "created_at": msg.created_at.isoformat(),
            "delivered_at": None, "read_at": None}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_customer(
    data: CustomerCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username = data.username.strip()

    existing = db.scalar(
        select(User).where(User.username == username)
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Ese nombre de usuario ya existe.",
        )

    package = None

    if data.package_id is not None:
        package = db.get(Package, data.package_id)

        if package is None or not package.active:
            raise HTTPException(
                status_code=404,
                detail="El paquete no existe o está desactivado.",
            )

    # Importación local para mantener explícita la dependencia
    # de seguridad y evitar duplicar la lógica de contraseñas.
    from backend.app_security import hash_password

    customer = User(
        username=username,
        password_hash=hash_password(data.password),
        role="customer",
        is_active=True,
    )

    db.add(customer)
    db.flush()

    if package:
        renew_subscription(db, customer, package)
    else:
        db.commit()

    db.refresh(customer)

    return {
        "customer": customer_response(db, customer)
    }


@router.patch("/{customer_id}/status")
async def update_customer_status(
    customer_id: int,
    data: CustomerStatusUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(User, customer_id)

    if customer is None or customer.role != "customer":
        raise HTTPException(
            status_code=404,
            detail="Cliente no encontrado.",
        )

    customer.is_active = data.is_active
    if not data.is_active:
        db.query(AuthSession).filter(
            AuthSession.user_id == customer.id,
            AuthSession.revoked_at.is_(None),
        ).update({AuthSession.revoked_at: utc_now()}, synchronize_session=False)
    db.commit()
    db.refresh(customer)
    if not customer.is_active:
        await session_event_hub.notify_user(customer.id, "account_disabled")

    return {
        "customer": {
            "id": customer.id,
            "username": customer.username,
            "is_active": customer.is_active,
        }
    }


@router.post("/{customer_id}/revoke-session")
async def revoke_customer_session(
    customer_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Let an administrator release a customer's device slot if that device is lost."""
    customer = db.get(User, customer_id)
    if customer is None or customer.role != "customer":
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    revoked = revoke_user_client_sessions(db, customer.id)
    db.commit()
    if revoked:
        await session_event_hub.notify_user(customer.id, "admin_revoked")
    return {"customer_id": customer.id, "revoked": revoked}


@router.post("/{customer_id}/renew/{package_id}")
def renew_customer(
    customer_id: int,
    package_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(User, customer_id)

    if customer is None or customer.role != "customer":
        raise HTTPException(
            status_code=404,
            detail="Cliente no encontrado.",
        )

    package = db.get(Package, package_id)

    if package is None or not package.active:
        raise HTTPException(
            status_code=404,
            detail="El paquete no existe o está desactivado.",
        )

    try:
        subscription = renew_subscription(db, customer, package)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    return {
        "customer_id": customer.id,
        "username": customer.username,
        "subscription": {
            "id": subscription.id,
            "package_id": subscription.package_id,
            "starts_at": subscription.starts_at.isoformat(),
            "expires_at": subscription.expires_at.isoformat(),
            "active": subscription.active,
        },
    }


@client_router.get("/notifications")
def client_notifications(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    messages = db.scalars(select(CustomerMessage).where(
        CustomerMessage.customer_id == user.id,
        CustomerMessage.kind == "notification",
        CustomerMessage.read_at.is_(None),
    ).order_by(CustomerMessage.created_at.desc()).limit(100)).all()
    static_base = str(request.base_url).rstrip("/")
    return {"notifications": [{"id": m.id, "subject": m.subject or "Notificación", "body": m.body,
                                "image_url": f"{static_base}{m.image_url}" if m.image_url else None,
                                "created_at": m.created_at.isoformat(), "read_at": m.read_at.isoformat() if m.read_at else None} for m in messages]}


@client_router.patch("/notifications/{message_id}/read")
def mark_notification_read(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = db.get(CustomerMessage, message_id)
    if message is None or message.customer_id != user.id or message.kind != "notification":
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")
    if message.read_at is None:
        message.read_at = utc_now()
        db.commit()
    return {"id": message.id, "read": True}


@client_router.get("/messages")
def client_messages(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    messages = db.scalars(select(CustomerMessage).where(CustomerMessage.customer_id == user.id)
                          .order_by(CustomerMessage.created_at.desc()).limit(100)).all()
    now = utc_now()
    for msg in messages:
        if msg.sender_role == "admin" and msg.delivered_at is None:
            msg.delivered_at = now
    if messages:
        db.commit()
    return {"messages": [{"id": m.id, "sender_role": m.sender_role, "kind": m.kind, "subject": m.subject, "image_url": m.image_url, "body": m.body,
                           "created_at": m.created_at.isoformat(),
                           "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
                           "read_at": m.read_at.isoformat() if m.read_at else None} for m in messages[::-1]]}


@client_router.patch("/messages/read")
def mark_client_chat_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    messages = db.scalars(select(CustomerMessage).where(
        CustomerMessage.customer_id == user.id,
        CustomerMessage.sender_role == "admin",
        CustomerMessage.kind == "chat",
        CustomerMessage.read_at.is_(None),
    )).all()
    now = utc_now()
    for message in messages:
        message.delivered_at = message.delivered_at or now
        message.read_at = now
    if messages:
        db.commit()
    return {"updated": len(messages)}


@client_router.post("/messages")
async def client_send_message(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    body = str(payload.get("body", "")).strip()
    if not body or len(body) > 4000:
        raise HTTPException(status_code=400, detail="El mensaje debe tener entre 1 y 4000 caracteres.")
    msg = CustomerMessage(customer_id=user.id, sender_role="customer", kind="chat", body=body)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"id": msg.id, "sender_role": msg.sender_role, "kind": msg.kind, "body": msg.body,
            "created_at": msg.created_at.isoformat()}



