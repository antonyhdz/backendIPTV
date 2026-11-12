from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from backend.config import settings

_password_hash = PasswordHash.recommended()
_dummy_hash = _password_hash.hash("nonexistent-account-dummy-password")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, encoded_hash: str | None) -> bool:
    candidate = encoded_hash or _dummy_hash
    try:
        verified = _password_hash.verify(password, candidate)
    except (PwdlibError, ValueError, TypeError):
        verified = False
    if encoded_hash is None:
        return False
    return verified


def create_access_token(user_id: str, username: str, role: str) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires_in = settings.jwt_expiration_minutes * 60
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_in


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        options={"require": ["sub", "exp", "iat", "iss"]},
    )
