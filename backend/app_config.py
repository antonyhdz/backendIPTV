import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]

# Carga .env desde backend/.env o desde la raíz del proyecto
if (Path(__file__).resolve().parent / ".env").is_file():
    load_dotenv(Path(__file__).resolve().parent / ".env")
elif (BASE_DIR / ".env").is_file():
    load_dotenv(BASE_DIR / ".env")
else:
    load_dotenv()


class Settings:
    APP_NAME = "Patzi IPTV API"
    APP_VERSION = "1.0.0"
    PANEL_ORIGINS = os.getenv("PATZI_PANEL_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500")

    ADMIN_USER = os.getenv("PATZI_ADMIN_USER", "").strip()
    ADMIN_PASSWORD = os.getenv("PATZI_ADMIN_PASSWORD", "")
    JWT_SECRET = os.getenv("PATZI_JWT_SECRET", "")

    TOKEN_MINUTES = int(os.getenv("PATZI_TOKEN_MINUTES", "30"))
    CLIENT_SESSION_DAYS = int(os.getenv("PATZI_CLIENT_SESSION_DAYS", "30"))
    CHANNEL_CHECK_INTERVAL_SECONDS = int(os.getenv("PATZI_CHANNEL_CHECK_INTERVAL_SECONDS", "300"))
    CHANNEL_CHECK_BATCH_SIZE = int(os.getenv("PATZI_CHANNEL_CHECK_BATCH_SIZE", "20"))
    CHANNEL_CHECK_BATCH_DELAY_SECONDS = float(os.getenv("PATZI_CHANNEL_CHECK_BATCH_DELAY_SECONDS", "5"))
    BACKUP_INTERVAL_HOURS = int(os.getenv("PATZI_BACKUP_INTERVAL_HOURS", "24"))
    BACKUP_RETENTION_COUNT = int(os.getenv("PATZI_BACKUP_RETENTION_COUNT", "7"))

    MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "patzi_iptv").strip()
    USE_MONGO = bool(MONGODB_URI)

    DATABASE_PATH = Path(os.getenv("PATZI_DATABASE_PATH", str(BASE_DIR / "data" / "patzi.db"))).expanduser()
    if not DATABASE_PATH.is_absolute():
        DATABASE_PATH = BASE_DIR / DATABASE_PATH
    DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
    BACKUP_DIR = Path(os.getenv("PATZI_BACKUP_DIR", str(BASE_DIR / "data" / "backups"))).expanduser()
    if not BACKUP_DIR.is_absolute():
        BACKUP_DIR = BASE_DIR / BACKUP_DIR

    def validate(self):
        if not self.ADMIN_USER:
            raise RuntimeError(
                "Falta PATZI_ADMIN_USER en el archivo .env"
            )

        if not self.ADMIN_PASSWORD:
            raise RuntimeError(
                "Falta PATZI_ADMIN_PASSWORD en el archivo .env"
            )

        if len(self.JWT_SECRET) < 32:
            raise RuntimeError(
                "PATZI_JWT_SECRET debe tener al menos 32 caracteres"
            )

        if self.TOKEN_MINUTES < 1:
            raise RuntimeError(
                "PATZI_TOKEN_MINUTES debe ser mayor que cero"
            )

        if not 1 <= self.CLIENT_SESSION_DAYS <= 30:
            raise RuntimeError("PATZI_CLIENT_SESSION_DAYS debe estar entre 1 y 30")

        if self.CHANNEL_CHECK_INTERVAL_SECONDS < 30:
            raise RuntimeError("PATZI_CHANNEL_CHECK_INTERVAL_SECONDS debe ser al menos 30")
        if self.CHANNEL_CHECK_BATCH_SIZE < 1:
            raise RuntimeError("PATZI_CHANNEL_CHECK_BATCH_SIZE debe ser mayor que cero")
        if self.CHANNEL_CHECK_BATCH_DELAY_SECONDS < 0:
            raise RuntimeError("PATZI_CHANNEL_CHECK_BATCH_DELAY_SECONDS no puede ser negativo")
        if not 1 <= self.BACKUP_INTERVAL_HOURS <= 8760:
            raise RuntimeError("PATZI_BACKUP_INTERVAL_HOURS debe estar entre 1 y 8760")
        if not 1 <= self.BACKUP_RETENTION_COUNT <= 100:
            raise RuntimeError("PATZI_BACKUP_RETENTION_COUNT debe estar entre 1 y 100")


settings = Settings()

