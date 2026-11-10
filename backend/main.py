import sys
from pathlib import Path

# Permite ejecutar uvicorn tanto desde la raíz (backend.main:app) como desde backend/ (main:app)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy import inspect, text
from fastapi.responses import FileResponse

from backend.app_config import settings
from backend.app_database import Base, SessionLocal, engine
from backend.app_mongo import init_mongo_indexes
from backend.app_models import User, Package, Subscription, Channel
from backend.app_security import hash_password

from backend.app_routers import auth, account, channels, packages, customers, admin, backups, events
from backend.app_services.backup_service import run_backup_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validar la configuración antes de iniciar
    settings.validate()

    if settings.USE_MONGO:
        init_mongo_indexes()
    else:
        # Crear las tablas si todavía no existen
        Base.metadata.create_all(bind=engine)

        # Actualizar bases SQLite existentes sin borrar los canales guardados.
        if "channels" in inspect(engine).get_table_names():
            channel_columns = {column["name"] for column in inspect(engine).get_columns("channels")}
            if "stream_info" not in channel_columns:
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE channels ADD COLUMN stream_info TEXT"))
            if "epg_shift_minutes" in channel_columns:
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE channels DROP COLUMN epg_shift_minutes"))
                channel_columns.remove("epg_shift_minutes")
            channel_metadata_columns = {
                "country": "VARCHAR(2) NOT NULL DEFAULT 'SV'",
                "catchup": "TEXT NOT NULL DEFAULT ''",
                "catchup_days": "TEXT NOT NULL DEFAULT ''",
                "catchup_source": "TEXT NOT NULL DEFAULT ''",
                "tvg_shift": "TEXT NOT NULL DEFAULT ''",
                "tvg_id": "TEXT NOT NULL DEFAULT ''",
                "tvg_name": "TEXT NOT NULL DEFAULT ''",
                "tvg_logo": "TEXT NOT NULL DEFAULT ''",
                "tvg_country": "TEXT NOT NULL DEFAULT ''",
                "tvg_language": "TEXT NOT NULL DEFAULT ''",
                "tvg_chno": "TEXT NOT NULL DEFAULT ''",
            }
            for column_name, column_type in channel_metadata_columns.items():
                if column_name not in channel_columns:
                    with engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE channels ADD COLUMN {column_name} {column_type}"))

        if "customer_messages" in inspect(engine).get_table_names():
            message_columns = {column["name"] for column in inspect(engine).get_columns("customer_messages")}
            with engine.begin() as connection:
                if "read_at" not in message_columns:
                    connection.execute(text("ALTER TABLE customer_messages ADD COLUMN read_at DATETIME"))
                if "delivered_at" not in message_columns:
                    connection.execute(text("ALTER TABLE customer_messages ADD COLUMN delivered_at DATETIME"))
                if "subject" not in message_columns:
                    connection.execute(text("ALTER TABLE customer_messages ADD COLUMN subject VARCHAR(200)"))
                if "image_url" not in message_columns:
                    connection.execute(text("ALTER TABLE customer_messages ADD COLUMN image_url VARCHAR(500)"))

        # Añadir el identificador de dispositivo en bases con sesiones cliente previas.
        if "auth_sessions" in inspect(engine).get_table_names():
            session_columns = {column["name"] for column in inspect(engine).get_columns("auth_sessions")}
            with engine.begin() as connection:
                if "device_id" not in session_columns:
                    connection.execute(text("ALTER TABLE auth_sessions ADD COLUMN device_id VARCHAR(128)"))
                    # Las sesiones antiguas no estaban asociadas a una instalación verificable.
                    connection.execute(text(
                        "UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP "
                        "WHERE device_id IS NULL AND revoked_at IS NULL"
                    ))
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_sessions_one_active_per_user "
                    "ON auth_sessions (user_id) WHERE revoked_at IS NULL"
                ))

    # Crear el administrador inicial
    with SessionLocal() as db:
        user = db.scalar(
            select(User).where(User.username == settings.ADMIN_USER)
        )

        if user is None:
            user = User(
                username=settings.ADMIN_USER,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="admin",
                is_active=True,
            )
            db.add(user)
            db.commit()
            print("Administrador inicial creado.")
        elif user.role != "admin":
            user.role = "admin"
            db.commit()
            print("Rol del administrador actualizado.")

    print("Base de datos inicializada.")
    channel_monitor_task = asyncio.create_task(channels.run_channel_monitor())
    backup_scheduler_task = asyncio.create_task(run_backup_scheduler())
    try:
        yield
    finally:
        for task in (channel_monitor_task, backup_scheduler_task):
            task.cancel()
        await asyncio.gather(channel_monitor_task, backup_scheduler_task, return_exceptions=True)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="API modular para administrar Patzi IPTV.",
    lifespan=lifespan,
)

cors_origins = [origin.strip() for origin in settings.PANEL_ORIGINS.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Registrar módulos
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(channels.router)
app.include_router(packages.router)
app.include_router(customers.router)
app.include_router(customers.client_router)
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(backups.router)


@app.get("/", tags=["Sistema"], include_in_schema=False)
def root():
    return {"app": settings.APP_NAME, "version": settings.APP_VERSION, "status": "online", "docs": "/docs"}


@app.get("/api/v1/status", tags=["Sistema"])
def system_status():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "online",
        "docs": "/docs",
    }


@app.get("/api/v1/health", tags=["Sistema"])
def health():
    return {"status": "ok"}






