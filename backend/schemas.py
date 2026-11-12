from typing import Literal

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("Username cannot be empty.")
        return value


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    role: Literal["customer", "admin"] = "customer"

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    username: str
    role: str
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("name", "email", "phone", "notes")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    max_devices: int | None = Field(default=None, ge=1, le=4)

    @model_validator(mode="before")
    @classmethod
    def validate_update_fields(cls, value):
        if isinstance(value, dict):
            if not value:
                raise ValueError("Provide at least one field to update.")
            if any(item is None for key, item in value.items() if key in {"name", "username", "email", "is_active", "max_devices"}):
                raise ValueError("name, username, email, is_active and max_devices cannot be null.")
        return value

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else value

    @field_validator("name", "email", "phone", "notes")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    stream_url: str = Field(min_length=1, max_length=2048)
    category: str | None = Field(default=None, max_length=80)
    logo_url: str | None = Field(default=None, max_length=2048)

    @field_validator("name", "stream_url", "category", "logo_url")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    stream_url: str | None = Field(default=None, min_length=1, max_length=2048)
    category: str | None = Field(default=None, max_length=80)
    logo_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_update_fields(cls, value):
        if isinstance(value, dict) and (not value or any(item is None for key, item in value.items() if key in {"name", "stream_url", "is_active"})):
            raise ValueError("Provide non-null fields to update.")
        return value

    @field_validator("name", "stream_url", "category", "logo_url")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price: float = Field(ge=0, le=1_000_000)
    duration_days: int = Field(ge=1, le=3650)
    channel_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("channel_ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        from bson import ObjectId

        if any(not ObjectId.is_valid(item) for item in value):
            raise ValueError("channel_ids must contain valid IDs.")
        if len(value) != len(set(value)):
            raise ValueError("channel_ids cannot contain duplicates.")
        return value


class PackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price: float | None = Field(default=None, ge=0, le=1_000_000)
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    channel_ids: list[str] | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_update_fields(cls, value):
        if isinstance(value, dict) and (not value or any(item is None for item in value.values())):
            raise ValueError("Provide non-null fields to update.")
        return value

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("channel_ids")
    @classmethod
    def validate_ids(cls, value: list[str] | None) -> list[str] | None:
        from bson import ObjectId

        if value is not None and (any(not ObjectId.is_valid(item) for item in value) or len(value) != len(set(value))):
            raise ValueError("channel_ids must contain unique valid IDs.")
        return value


class SubscriptionCreate(BaseModel):
    customer_id: str
    package_id: str
    starts_at: datetime | None = None

    @field_validator("customer_id", "package_id")
    @classmethod
    def validate_object_ids(cls, value: str) -> str:
        from bson import ObjectId

        if not ObjectId.is_valid(value):
            raise ValueError("Must be a valid ID.")
        return value

    @field_validator("starts_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("starts_at must include a timezone, preferably UTC.")
        return value


class CustomerPasswordUpdate(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class MobileLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=128)
    device_uuid: UUID
    device_type: Literal["mobile", "tablet", "tv", "box", "desktop"]
    device_name: str | None = Field(default=None, max_length=80)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("device_name")
    @classmethod
    def normalize_device_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class MobileTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    device_uuid: str
    device_type: Literal["mobile", "tablet", "tv", "box", "desktop"]
    registered_devices: int
    max_devices: int


class AppSettingsUpdate(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=120)
    support_email: str | None = Field(default=None, max_length=254)
    support_phone: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="before")
    @classmethod
    def validate_settings(cls, value):
        if isinstance(value, dict) and not value:
            raise ValueError("Provide at least one setting to update.")
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("Settings cannot be null; use an empty string to clear contact fields.")
        return value

    @field_validator("company_name", "support_email", "support_phone", "currency", "timezone")
    @classmethod
    def strip_setting(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("timezone must be a valid IANA timezone.") from exc
        return value
