from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    device_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{32,128}$",
    )


class RefreshInput(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class LogoutInput(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    duration_days: int = Field(gt=0, le=3650)
    price_cents: int = Field(ge=0, le=100_000_000)
    active: bool = True
    channel_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre del paquete es obligatorio")
        return value


class PackageUpdate(BaseModel):
    channel_ids: list[int] = Field(min_length=1)


class BackupPolicyUpdate(BaseModel):
    interval_hours: int = Field(ge=1, le=8760)
    retention_count: int = Field(ge=1, le=100)


class CustomerCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=256)
    package_id: int | None = Field(default=None, gt=0)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El usuario es obligatorio")
        return value


class CustomerUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    package_id: int | None = Field(default=None, gt=0)

    @field_validator("username")
    @classmethod
    def clean_optional_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("El usuario no puede estar vacío")
        return value


class CustomerStatusUpdate(BaseModel):
    is_active: bool


class ChannelFields(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(default="General", max_length=100)
    logo_url: str | None = Field(default=None, max_length=2048)
    stream_url: str = Field(min_length=8, max_length=4096)
    enabled: bool = True
    country: str = Field(default="SV", max_length=2)
    catchup: str = ""
    catchup_days: str = ""
    catchup_source: str = ""
    tvg_shift: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    tvg_country: str = ""
    tvg_language: str = ""
    tvg_chno: str = ""

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre no puede estar vacío")
        return value

    @field_validator("category")
    @classmethod
    def clean_category(cls, value: str) -> str:
        return value.strip()

    @field_validator("country")
    @classmethod
    def clean_country(cls, value: str) -> str:
        return value.strip().upper() or "SV"

    @field_validator("logo_url")
    @classmethod
    def validate_logo_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("La URL del logo debe comenzar con http:// o https://")
        return value

    @field_validator("stream_url")
    @classmethod
    def validate_stream_url(cls, value: str) -> str:
        value = value.strip()
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https", "rtmp", "rtsp") or not parsed.netloc:
            raise ValueError("La URL de transmisión debe comenzar con http://, https://, rtmp:// o rtsp://")
        return value


class ChannelCreate(ChannelFields):
    pass


class ChannelStatusUpdate(BaseModel):
    enabled: bool


class ChannelUpdate(ChannelFields):
    pass


class BatchDeleteInput(BaseModel):
    ids: list[int]


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    refresh_expires_in: int | None = None
    user: UserResponse
