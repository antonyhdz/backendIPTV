from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app_database import get_db
from backend.app_models import AuthSession, User, utc_now
from backend.app_security import decode_access_token


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    db: Session = Depends(get_db),
) -> User:
    """Obtiene el usuario autenticado mediante su token JWT."""

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token ausente, inválido o expirado.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (
        jwt.InvalidTokenError,
        ValueError,
        KeyError,
        TypeError,
    ):
        raise unauthorized

    user = db.get(User, user_id)

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta no existe o está desactivada.",
        )

    if user.role == "customer":
        try:
            session_id = int(payload["sid"])
        except (KeyError, ValueError, TypeError):
            raise unauthorized
        session = db.get(AuthSession, session_id)
        if (
            session is None
            or session.user_id != user.id
            or session.revoked_at is not None
            or session.expires_at <= utc_now()
        ):
            raise unauthorized

    return user


def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Permite acceder únicamente al administrador."""

    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos de administrador.",
        )

    return user


def require_customer(
    user: User = Depends(get_current_user),
) -> User:
    """Permite acceder únicamente a cuentas de clientes."""

    if user.role != "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta operación requiere una cuenta de cliente.",
        )

    return user


