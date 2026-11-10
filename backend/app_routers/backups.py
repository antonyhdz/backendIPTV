import asyncio
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app_database import engine, get_db
from backend.app_dependencies import require_admin
from backend.app_models import User
from backend.app_schemas import BackupPolicyUpdate
from backend.app_services.backup_service import (
    backup_file_path,
    create_backup,
    delete_backup,
    get_backup_policy,
    list_backups,
    prune_backups,
    restore_backup,
    update_backup_policy,
)
from backend.app_services.maintenance import maintenance_lock


router = APIRouter(
    prefix="/api/v1/admin/backups",
    tags=["Respaldos"],
)


@router.get("")
def get_backups(
    admin: User = Depends(require_admin),
):
    policy = get_backup_policy()
    return {
        "backups": list_backups(),
        "policy": {
            **policy,
            "directory": "data/backups",
        },
    }


@router.put("/policy")
def save_backup_policy(
    data: BackupPolicyUpdate,
    admin: User = Depends(require_admin),
):
    try:
        policy = update_backup_policy(data.interval_hours, data.retention_count)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"policy": policy}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_manual_backup(
    admin: User = Depends(require_admin),
):
    async with maintenance_lock:
        policy = get_backup_policy()
        record = await asyncio.to_thread(create_backup, "manual")
        await asyncio.to_thread(prune_backups, policy["retention_count"])
    return {"backup": record}


@router.get("/{filename}/download")
def download_backup(
    filename: str,
    admin: User = Depends(require_admin),
):
    try:
        path = backup_file_path(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Respaldo no encontrado.")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.delete("/{filename}")
async def remove_backup(
    filename: str,
    admin: User = Depends(require_admin),
):
    async with maintenance_lock:
        try:
            await asyncio.to_thread(delete_backup, filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Respaldo no encontrado.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": filename}


@router.post("/{filename}/restore")
async def apply_backup(
    filename: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    async with maintenance_lock:
        try:
            source_path = backup_file_path(filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not source_path.is_file():
            raise HTTPException(status_code=404, detail="Respaldo no encontrado.")

        db.close()
        try:
            safety_copy = await asyncio.to_thread(create_backup, "pre-restore")
            engine.dispose()
            restored = await asyncio.to_thread(restore_backup, filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Respaldo no encontrado.") from exc
        except (ValueError, sqlite3.DatabaseError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"No se pudo restaurar el respaldo: {exc}") from exc

    return {"restored": restored, "safety_backup": safety_copy}


