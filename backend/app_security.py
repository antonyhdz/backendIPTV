from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from backend.app_config import settings


password_hasher = PasswordHash.recommended()

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Genera un hash seguro de una contraseña."""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Comprueba una contraseña sin almacenarla en texto plano."""
    return password_hasher.verify(password, password_hash)


def create_access_token(
    user_id: int,
    role: str,
    expires_minutes: int | None = None,
    session_id: int | None = None,
) -> str:
    """Genera un token JWT con vencimiento."""
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes or settings.TOKEN_MINUTES),
    }
    if session_id is not None:
        payload["sid"] = session_id

    return jwt.encode(
        payload,
        settings.JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """Valida el token y devuelve sus datos."""
    return jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
    )


