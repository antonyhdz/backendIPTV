import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = os.getenv("MONGODB_URI", "").strip()
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "patzi_iptv").strip()
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_expiration_minutes: int = int(os.getenv("JWT_EXPIRATION_MINUTES", "15"))
    jwt_issuer: str = os.getenv("JWT_ISSUER", "patzi-iptv-api")
    initial_admin_username: str = os.getenv("INITIAL_ADMIN_USERNAME", "").strip().lower()
    initial_admin_password: str = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    allowed_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    )

    def validate(self) -> None:
        if not self.mongodb_uri:
            raise RuntimeError("MONGODB_URI is required.")
        if not self.mongodb_db_name:
            raise RuntimeError("MONGODB_DB_NAME cannot be empty.")
        if len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must contain at least 32 characters.")
        if self.jwt_expiration_minutes < 1:
            raise RuntimeError("JWT_EXPIRATION_MINUTES must be greater than zero.")
        if len(self.initial_admin_username) < 3:
            raise RuntimeError("INITIAL_ADMIN_USERNAME must contain at least 3 characters.")
        if len(self.initial_admin_password) < 12:
            raise RuntimeError("INITIAL_ADMIN_PASSWORD must contain at least 12 characters.")


settings = Settings()
