from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app_config import settings
from backend.app_mongo import MongoSession, get_mongo_db, init_mongo_indexes


# Crear el directorio de datos si todavía no existe.
settings.DATABASE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

# Conexión a SQLite (fallback local).
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Fábrica de sesiones SQLite.
SqliteSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def SessionLocal():
    """Retorna una sesión de MongoDB Atlas si está configurado, o SQLite en su defecto."""
    if settings.USE_MONGO:
        return MongoSession()
    return SqliteSessionLocal()


class Base(DeclarativeBase):
    """Clase base de todos los modelos de la base de datos."""
    pass


def get_db() -> Generator[Any, None, None]:
    """Proporciona una sesión y la cierra al terminar."""
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


