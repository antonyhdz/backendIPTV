import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app_config import settings
from backend.app_models import AuthSession, User
from backend.app_security import create_access_token, verify_password


class ActiveDeviceSessionError(Exception):
    pass


def authenticate_user(
    db: Session,
    username: str,
    password: str,
) -> User | None:
    """Valida las credenciales de un usuario activo."""

    user = db.scalar(
        select(User).where(User.username == username.strip())
    )

    if user is None or not user.is_active:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_login_response(user: User, db: Session, device_id: str | None = None) -> dict:
    """Genera la respuesta de inicio de sesión."""
    response = {
        "access_token": create_access_token(user.id, user.role),
        "token_type": "bearer",
        "expires_in": settings.TOKEN_MINUTES * 60,
        "refresh_token": None,
        "refresh_expires_in": None,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
        },
    }
    if user.role == "customer":
        now = _utc_now()
        db.query(AuthSession).filter(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at <= now,
        ).update({AuthSession.revoked_at: now}, synchronize_session=False)
        current = db.scalar(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        )
        if current is not None and current.device_id != device_id:
            raise ActiveDeviceSessionError(
                "Esta cuenta ya tiene una sesión activa en otro dispositivo. Cierra sesión allí para continuar."
            )

        refresh_token = secrets.token_urlsafe(48)
        if current is not None:
            current.refresh_token_hash = _hash_refresh_token(refresh_token)
            current.last_used_at = now
            current.expires_at = now + timedelta(days=settings.CLIENT_SESSION_DAYS)
        else:
            current = AuthSession(
                user_id=user.id,
                device_id=device_id,
                refresh_token_hash=_hash_refresh_token(refresh_token),
                created_at=now,
                last_used_at=now,
                expires_at=now + timedelta(days=settings.CLIENT_SESSION_DAYS),
            )
            db.add(current)
        try:
            db.flush()
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ActiveDeviceSessionError(
                "Esta cuenta ya inició sesión en otro dispositivo. Cierra esa sesión para continuar."
            ) from exc
        response["refresh_token"] = refresh_token
        response["refresh_expires_in"] = settings.CLIENT_SESSION_DAYS * 24 * 60 * 60
        response["access_token"] = create_access_token(
            user.id, user.role, session_id=current.id
        )
    return response


def refresh_client_access(db: Session, token: str) -> dict | None:
    """Rotate a client's refresh token and issue another short-lived JWT."""
    token_hash = _hash_refresh_token(token)
    session = db.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
    )
    now = _utc_now()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active or user.role != "customer":
        session.revoked_at = now
        db.commit()
        return None

    new_token = secrets.token_urlsafe(48)
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session.id,
            AuthSession.refresh_token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .values(
            refresh_token_hash=_hash_refresh_token(new_token),
            last_used_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return {
        "access_token": create_access_token(user.id, user.role, session_id=session.id),
        "token_type": "bearer",
        "expires_in": settings.TOKEN_MINUTES * 60,
        "refresh_token": new_token,
        "refresh_expires_in": max(0, int((session.expires_at - now).total_seconds())),
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
        },
    }


def revoke_client_session(db: Session, token: str) -> tuple[int, int] | None:
    session = db.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == _hash_refresh_token(token))
    )
    if session is None or session.revoked_at is not None:
        return None
    user_id, session_id = session.user_id, session.id
    session.revoked_at = _utc_now()
    db.commit()
    return user_id, session_id


def revoke_user_client_sessions(db: Session, user_id: int) -> int:
    now = _utc_now()
    return db.query(AuthSession).filter(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at: now}, synchronize_session=False)


