from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from backend.dependencies import database_dependency, require_admin
from backend.schemas import CreateUserRequest, UserResponse
from backend.security import hash_password

router = APIRouter(prefix="/api/v1/admin/users", tags=["Usuarios"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    data: CreateUserRequest,
    database: Database = Depends(database_dependency),
    _admin: dict = Depends(require_admin),
):
    document = {
        "username": data.username,
        "password_hash": hash_password(data.password),
        "role": data.role,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = database.users.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Username already exists.") from exc

    return {
        "id": str(result.inserted_id),
        "username": document["username"],
        "role": document["role"],
        "is_active": document["is_active"],
    }
