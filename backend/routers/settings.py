from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from backend.dependencies import database_dependency, require_admin
from backend.schemas import AppSettingsUpdate

router = APIRouter(prefix="/api/v1/admin/settings", tags=["Configuración"])

DEFAULT_SETTINGS = {
    "company_name": "Patzi IPTV",
    "support_email": "",
    "support_phone": "",
    "currency": "USD",
    "timezone": "America/El_Salvador",
}


@router.get("")
def get_settings(database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    document = database.app_settings.find_one({"_id": "main"}) or {}
    return {**DEFAULT_SETTINGS, **{key: document[key] for key in DEFAULT_SETTINGS if key in document}}


@router.patch("")
def update_settings(data: AppSettingsUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one setting to update.")
    updates["updated_at"] = datetime.now(timezone.utc)
    database.app_settings.update_one({"_id": "main"}, {"$set": updates}, upsert=True)
    document = database.app_settings.find_one({"_id": "main"}) or {}
    return {**DEFAULT_SETTINGS, **{key: document[key] for key in DEFAULT_SETTINGS if key in document}}
