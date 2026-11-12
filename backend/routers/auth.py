import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Request as FastAPIRequest
from pymongo.database import Database

from backend.dependencies import database_dependency, get_current_user
from backend.schemas import LoginRequest, TokenResponse, UserResponse
from backend.security import create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación"])


@router.post("/login", response_model=TokenResponse)
def login(
    data: LoginRequest,
    request: FastAPIRequest,
    database: Database = Depends(database_dependency),
):
    username = data.username.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = hashlib.sha256(f"{username}\0{client_ip}".encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    attempts = database.auth_attempts.find_one({"_id": attempt_key})
    if attempts and attempts.get("blocked_until") and attempts["blocked_until"] > now:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    if attempts and attempts.get("expires_at") and attempts["expires_at"] <= now:
        database.auth_attempts.delete_one({"_id": attempt_key})

    user = database.users.find_one({"username": username})
    encoded_hash = user.get("password_hash") if user else None
    if not verify_password(data.password, encoded_hash) or not user or not user.get("is_active", False):
        database.auth_attempts.update_one(
            {"_id": attempt_key},
            {
                "$inc": {"attempts": 1},
                "$setOnInsert": {"expires_at": now + timedelta(minutes=10)},
            },
            upsert=True,
        )
        current_attempts = database.auth_attempts.find_one({"_id": attempt_key})
        if current_attempts and current_attempts.get("attempts", 0) >= 8:
            blocked_until = now + timedelta(minutes=10)
            database.auth_attempts.update_one(
                {"_id": attempt_key},
                {"$set": {"blocked_until": blocked_until, "expires_at": blocked_until}},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    database.auth_attempts.delete_one({"_id": attempt_key})
    token, expires_in = create_access_token(str(user["_id"]), user["username"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": {
            "id": str(user["_id"]),
            "username": user["username"],
            "role": user["role"],
            "is_active": user["is_active"],
        },
    }


@router.get("/me", response_model=UserResponse)
def current_user(user: dict = Depends(get_current_user)):
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "role": user["role"],
        "is_active": user["is_active"],
    }
