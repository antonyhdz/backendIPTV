import os
import re
import sqlite3
import tempfile
import asyncio
import logging
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app_config import settings
from backend.app_services.maintenance import maintenance_lock


_logger = logging.getLogger(__name__)


def get_backup_policy() -> dict:
    default = {
        "interval_hours": settings.BACKUP_INTERVAL_HOURS,
        "retention_count": settings.BACKUP_RETENTION_COUNT,
    }
    policy_path = settings.BACKUP_DIR / "backup-policy.json"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        interval = int(policy["interval_hours"])
        retention = int(policy["retention_count"])
        if not 1 <= interval <= 8760 or not 1 <= retention <= 100:
            return default
        return {"interval_hours": interval, "retention_count": retention}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return default


def update_backup_policy(interval_hours: int, retention_count: int) -> dict:
    if not 1 <= interval_hours <= 8760 or not 1 <= retention_count <= 100:
        raise ValueError("La política de respaldo no es válida.")
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    policy_path = settings.BACKUP_DIR / "backup-policy.json"
    temporary_path = policy_path.with_suffix(".tmp")
    policy = {"interval_hours": interval_hours, "retention_count": retention_count}
    temporary_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    os.replace(temporary_path, policy_path)
    return policy


_BACKUP_NAME = re.compile(r"^paneliptv_[0-9]{8}T[0-9]{12}_(manual|automatic|pre-restore)\.db$")
_REQUIRED_TABLES = {
    "users",
    "auth_sessions",
    "packages",
    "subscriptions",
    "channels",
    "package_channels",
}
_REQUIRED_COLUMNS = {
    "users": {"id", "username", "password_hash", "role", "is_active"},
    "auth_sessions": {"id", "user_id", "refresh_token_hash", "device_id", "expires_at", "revoked_at"},
    "packages": {"id", "name", "duration_days", "price_cents", "active"},
    "subscriptions": {"id", "customer_id", "package_id", "starts_at", "expires_at", "active"},
    "channels": {"id", "name", "category", "stream_url", "enabled", "stream_info"},
    "package_channels": {"package_id", "channel_id"},
}


def backup_file_path(filename: str) -> Path:
    if not _BACKUP_NAME.fullmatch(filename):
        raise ValueError("Nombre de respaldo no válido.")
    path = (settings.BACKUP_DIR / filename).resolve()
    if path.parent != settings.BACKUP_DIR.resolve():
        raise ValueError("Ruta de respaldo no válida.")
    return path


def _validate_database(path: Path) -> None:
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("La base de datos tiene errores de integridad.")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing_tables = _REQUIRED_TABLES - tables
            if missing_tables:
                raise ValueError("El archivo no contiene las tablas requeridas por esta versión.")
            for table, required_columns in _REQUIRED_COLUMNS.items():
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if not required_columns <= columns:
                    raise ValueError("El archivo no contiene las columnas requeridas por esta versión.")
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("El archivo no es una base SQLite válida.") from exc


def _backup_record(path: Path) -> dict:
    stat = path.stat()
    return {
        "filename": path.name,
        "kind": path.stem.rsplit("_", 1)[-1],
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
    }


def list_backups() -> list[dict]:
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    paths = [
        path for path in settings.BACKUP_DIR.iterdir()
        if path.is_file() and _BACKUP_NAME.fullmatch(path.name)
    ]
    return [_backup_record(path) for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)]


def create_backup(kind: str = "manual") -> dict:
    if kind not in {"manual", "automatic", "pre-restore"}:
        raise ValueError("Tipo de respaldo no válido.")
    source_path = settings.DATABASE_PATH
    if not source_path.is_file():
        raise ValueError("No se encontró la base de datos que se debe respaldar.")

    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final_path = settings.BACKUP_DIR / f"paneliptv_{timestamp}_{kind}.db"
    fd, temporary_name = tempfile.mkstemp(prefix=".patzi-backup-", suffix=".tmp", dir=settings.BACKUP_DIR)
    os.close(fd)
    temporary_path = Path(temporary_name)

    try:
        source = sqlite3.connect(source_path, timeout=30)
        destination = sqlite3.connect(temporary_path, timeout=30)
        try:
            source.backup(destination, pages=256, sleep=0.1)
        finally:
            destination.close()
            source.close()
        _validate_database(temporary_path)
        os.replace(temporary_path, final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return _backup_record(final_path)


def prune_backups(retention_count: int) -> int:
    paths = [
        backup_file_path(record["filename"])
        for record in list_backups()
    ]
    removed = 0
    for path in paths[retention_count:]:
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def delete_backup(filename: str) -> None:
    path = backup_file_path(filename)
    if not path.is_file():
        raise FileNotFoundError(filename)
    if len(list_backups()) <= 1:
        raise ValueError("Debe conservarse al menos un respaldo.")
    path.unlink()


def restore_backup(filename: str) -> dict:
    source_path = backup_file_path(filename)
    if not source_path.is_file():
        raise FileNotFoundError(filename)
    _validate_database(source_path)

    destination_path = settings.DATABASE_PATH
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(destination_path, timeout=30)
    try:
        source.backup(destination, pages=256, sleep=0.1)
    finally:
        destination.close()
        source.close()
    return _backup_record(source_path)


async def run_backup_scheduler() -> None:
    """Create an initial backup, then enforce the current rolling policy."""
    while True:
        try:
            policy = get_backup_policy()
            existing = list_backups()
            due = not existing
            if existing:
                most_recent = datetime.fromisoformat(existing[0]["created_at"])
                age_seconds = (datetime.now(timezone.utc) - most_recent).total_seconds()
                due = age_seconds >= policy["interval_hours"] * 3600

            if due:
                async with maintenance_lock:
                    # Recheck after acquiring the lock because a manual backup may have finished.
                    current = list_backups()
                    due_now = not current
                    if current:
                        latest = datetime.fromisoformat(current[0]["created_at"])
                        due_now = (datetime.now(timezone.utc) - latest).total_seconds() >= policy["interval_hours"] * 3600
                    if due_now:
                        record = await asyncio.to_thread(create_backup, "automatic")
                        await asyncio.to_thread(prune_backups, policy["retention_count"])
                        _logger.info("Automatic database backup created: %s", record["filename"])
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("Automatic database backup failed")
        await asyncio.sleep(60)


