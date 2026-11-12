from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import DuplicateKeyError

from backend.config import settings
from backend.database import close_database, connect_database
from backend.routers import auth, channels, customers, dashboard, packages, settings, users
from backend.security import hash_password


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate()
    database = connect_database()
    try:
        database.users.create_index("username", unique=True, name="uq_users_username")
        database.users.create_index("role", name="ix_users_role")
        database.auth_attempts.create_index(
            "expires_at", expireAfterSeconds=0, name="ttl_auth_attempts"
        )
        database.customers.create_index("email", unique=True, sparse=True, name="uq_customers_email")
        database.customers.create_index([("created_at", -1)], name="ix_customers_created_at")
        database.channels.create_index("name", unique=True, name="uq_channels_name")
        database.packages.create_index("name", unique=True, name="uq_packages_name")
        database.subscriptions.create_index([("expires_at", 1)], name="ix_subscriptions_expires_at")
        database.subscriptions.create_index([("customer_id", 1), ("expires_at", -1)], name="ix_subscriptions_customer_expiry")

        admin = database.users.find_one({"username": settings.initial_admin_username})
        if admin is None:
            admin_document = {
                "username": settings.initial_admin_username,
                "password_hash": hash_password(settings.initial_admin_password),
                "role": "admin",
                "is_active": True,
            }
            try:
                database.users.insert_one(admin_document)
            except DuplicateKeyError:
                pass
            admin = database.users.find_one({"username": settings.initial_admin_username})

        if admin is None or admin.get("role") != "admin":
            raise RuntimeError("INITIAL_ADMIN_USERNAME is already assigned to a non-admin account.")

        yield
    finally:
        close_database()


app = FastAPI(
    title="Patzi IPTV API",
    version="0.2.0",
    description="FastAPI backend for Patzi IPTV.",
    lifespan=lifespan,
)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(customers.router)
app.include_router(channels.router)
app.include_router(packages.router)
app.include_router(settings.router)


@app.get("/", tags=["Sistema"])
def root() -> dict[str, str]:
    return {"app": app.title, "version": app.version, "status": "online", "docs": "/docs"}


@app.get("/api/v1/health", tags=["Sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/status", tags=["Sistema"])
def status() -> dict[str, str]:
    return {"app": app.title, "version": app.version, "status": "online", "docs": "/docs"}
