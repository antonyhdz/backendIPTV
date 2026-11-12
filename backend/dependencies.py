from collections.abc import Generator

import jwt
from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.database import Database

from backend.database import get_database
from backend.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def database_dependency() -> Generator[Database, None, None]:
    yield get_database()


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    database: Database = Depends(database_dependency),
) -> dict:
    if credentials is None:
        raise unauthorized()
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = ObjectId(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise unauthorized()

    user = database.users.find_one({"_id": user_id, "is_active": True})
    if user is None:
        raise unauthorized()
    return user


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    database: Database = Depends(database_dependency),
) -> dict:
    if credentials is None:
        raise unauthorized()
    try:
        claims = decode_access_token(credentials.credentials)
        if claims.get("role") != "customer" or not claims.get("device_uuid"):
            raise unauthorized()
        customer_id = ObjectId(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise unauthorized()

    customer = database.customers.find_one(
        {
            "_id": customer_id,
            "is_active": True,
            "devices.device_uuid": claims["device_uuid"],
        }
    )
    if customer is None:
        raise unauthorized()
    return customer


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return user
