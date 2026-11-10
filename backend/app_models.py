from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app_database import Base


def utc_now() -> datetime:
    """Devuelve la fecha y hora UTC sin información de zona horaria."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    username: Mapped[str] = mapped_column(
        String(80),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="customer",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class AuthSession(Base):
    """Revocable refresh session for a client device."""
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index(
            "uq_auth_sessions_one_active_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Package(Base):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    duration_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Precio expresado en centavos.
    # Ejemplo: 500 = $5.00.
    price_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class PackageChannel(Base):
    """Channel included in a subscription package."""
    __tablename__ = "package_channels"

    package_id: Mapped[int] = mapped_column(
        ForeignKey("packages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class ChannelFavorite(Base):
    """A channel saved by a user. One record exists for each user/channel pair."""
    __tablename__ = "channel_favorites"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class ChannelView(Base):
    __tablename__ = "channel_views"
    __table_args__ = (Index("ix_channel_views_user_channel", "user_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class CustomerMessage(Base):
    __tablename__ = "customer_messages"
    __table_args__ = (Index("ix_customer_messages_customer_created", "customer_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="chat", nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        nullable=False,
    )

    package_id: Mapped[int] = mapped_column(
        ForeignKey("packages.id"),
        nullable=False,
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        index=True,
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        default="General",
        nullable=False,
    )

    logo_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    stream_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_live: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    last_checked: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    online_since: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    stream_info: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Original IPTV M3U metadata. Keep empty values so every channel has a
    # consistent set of editable/imported attributes.
    country: Mapped[str] = mapped_column(String(2), default="SV", nullable=False)
    catchup: Mapped[str] = mapped_column(Text, default="", nullable=False)
    catchup_days: Mapped[str] = mapped_column(Text, default="", nullable=False)
    catchup_source: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_shift: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_id: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_logo: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_country: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_language: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tvg_chno: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


