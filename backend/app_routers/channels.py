import asyncio
import html
import json
import logging
import re
import socket
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app_config import settings
from backend.app_database import SessionLocal, get_db
from backend.app_dependencies import get_current_user, require_admin
from backend.app_models import Channel, ChannelFavorite, ChannelView, PackageChannel, User
from backend.app_services.maintenance import maintenance_lock
from backend.app_schemas import (
    BatchDeleteInput,
    ChannelCreate,
    ChannelStatusUpdate,
    ChannelUpdate,
)
from backend.app_services.subscription_service import get_active_subscription
from backend.app_services.epg_service import (
    clear_epg_cache,
    current_epg_version,
    epg_file_for_version,
    epg_for_channel,
    list_epg_channels,
)


router = APIRouter(
    prefix="/api/v1",
    tags=["Canales"],
)

_executor = ThreadPoolExecutor(max_workers=20)
_metadata_semaphore = asyncio.Semaphore(5)
_channel_check_lock = asyncio.Lock()
_logger = logging.getLogger(__name__)


async def _run_reachability_cycle() -> tuple[int, int]:
    """Check enabled channels in bounded batches without probing stream metadata."""
    with SessionLocal() as db:
        channels = db.execute(
            select(Channel.id, Channel.stream_url)
            .where(Channel.enabled.is_(True))
            .order_by(Channel.id)
        ).all()

    checked = 0
    online_count = 0
    batch_size = settings.CHANNEL_CHECK_BATCH_SIZE
    for offset in range(0, len(channels), batch_size):
        batch = channels[offset:offset + batch_size]
        results = await asyncio.gather(
            *[_async_check(stream_url) for _, stream_url in batch],
            return_exceptions=True,
        )
        now = utc_now()
        with SessionLocal() as db:
            for (channel_id, _), result in zip(batch, results):
                channel = db.get(Channel, channel_id)
                if channel is None or not channel.enabled:
                    continue
                online = result is True
                if isinstance(result, BaseException):
                    _logger.warning("Automatic channel check failed for channel id %s", channel_id)
                if online and not channel.is_live:
                    channel.online_since = now
                elif not online:
                    channel.online_since = None
                channel.is_live = online
                channel.last_checked = now
                checked += 1
                online_count += int(online)
            db.commit()

        if offset + batch_size < len(channels):
            await asyncio.sleep(settings.CHANNEL_CHECK_BATCH_DELAY_SECONDS)

    return checked, online_count


async def run_channel_monitor() -> None:
    """Run periodic, non-overlapping reachability scans for enabled channels."""
    while True:
        started = asyncio.get_running_loop().time()
        try:
            async with _channel_check_lock:
                async with maintenance_lock:
                    checked, online = await _run_reachability_cycle()
            _logger.info(
                "Automatic channel check completed: %s checked, %s online, %s offline",
                checked,
                online,
                checked - online,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("Automatic channel check cycle failed")

        elapsed = asyncio.get_running_loop().time() - started
        interval = settings.CHANNEL_CHECK_INTERVAL_SECONDS
        # Keep at least one full interval between slow cycles to avoid continuous load.
        delay = interval - elapsed if elapsed < interval else interval
        await asyncio.sleep(delay)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def channel_dict(channel: Channel, *, is_favorite: bool = False) -> dict:
    uptime_secs = 0
    if channel.is_live and channel.online_since:
        uptime_secs = int((utc_now() - channel.online_since).total_seconds())

    h = uptime_secs // 3600
    m = (uptime_secs % 3600) // 60
    s = uptime_secs % 60

    return {
        "id": channel.id,
        "name": channel.name,
        "category": channel.category,
        "logo_url": channel.logo_url,
        "country": channel.country,
        "catchup": channel.catchup,
        "catchup_days": channel.catchup_days,
        "catchup_source": channel.catchup_source,
        "tvg_shift": channel.tvg_shift,
        "tvg_id": channel.tvg_id,
        "tvg_name": channel.tvg_name,
        "tvg_logo": channel.tvg_logo,
        "tvg_country": channel.tvg_country,
        "tvg_language": channel.tvg_language,
        "tvg_chno": channel.tvg_chno,
        "stream_url": channel.stream_url,
        "enabled": channel.enabled,
        "is_live": channel.is_live,
        "is_favorite": is_favorite,
        "uptime": f"{h:02d}h {m:02d}m {s:02d}s",
        "last_checked": channel.last_checked.isoformat() if channel.last_checked else None,
        "stream_info": json.loads(channel.stream_info) if channel.stream_info else None,
    }


def client_channel_dict(channel: Channel, *, is_favorite: bool = False) -> dict:
    """Add current/next programme metadata from the generated JSON guide."""
    result = channel_dict(channel, is_favorite=is_favorite)
    epg = epg_for_channel(
        channel.name,
        channel_id=channel.tvg_id or None,
    )
    if epg:
        result.update(epg)
        result["logo_url"] = epg.get("epg_logo_url") or channel.logo_url
    return result


def _do_check(url: str, timeout: int = 6) -> bool:
    """Check if a stream URL is reachable via HTTP HEAD or TCP."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)

        if parsed.scheme in ("http", "https"):
            try:
                req = urllib.request.Request(url, method="HEAD")
                req.add_header("User-Agent", "Mozilla/5.0 (compatible; PatziBOT/1.0)")
                req.add_header("Accept", "*/*")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status < 500
            except urllib.error.HTTPError as e:
                return e.code not in (404, 410, 500, 502, 503, 504)
            except Exception:
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if host:
                    with socket.create_connection((host, port), timeout=timeout):
                        return True
                return False
        else:
            # RTMP / RTSP – TCP reachability
            host = parsed.hostname
            port = parsed.port or (1935 if parsed.scheme == "rtmp" else 554)
            if host:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            return False
    except Exception:
        return False


async def _async_check(url: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _do_check, url)


def _read_hls_info(url: str, timeout: int = 6) -> dict | None:
    """Read real media metadata from an accessible HLS media playlist/segment."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PatziBOT/1.0)",
            "Accept": "*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            body = response.read(512 * 1024)
        text = body.decode("utf-8", errors="ignore")
        if not text.lstrip().startswith("#EXTM3U"):
            return _info_from_media_headers(content_type, body)

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        master_entries = []
        for i, line in enumerate(lines):
            if line.startswith(("#EXT-X-STREAM-INF:", "#EXT-X-I-FRAME-STREAM-INF:")):
                attrs = line.partition(":")[2]
                uri_match = re.search(r'URI="([^"]+)"', attrs)
                uri = uri_match.group(1) if uri_match else next(
                    (item for item in lines[i + 1:] if not item.startswith("#")), None
                )
                if uri:
                    master_entries.append((attrs, urllib.parse.urljoin(final_url, uri)))

        if master_entries:
            attrs, media_url = max(master_entries, key=lambda item: _hls_bandwidth(item[0]))
            variant = _read_hls_info(media_url, timeout)
            if variant:
                variant["bitrate_kbps"] = _hls_bandwidth(attrs) // 1000 or variant.get("bitrate_kbps")
                res_match = re.search(r'RESOLUTION=(\d+x\d+)', attrs)
                if res_match:
                    variant["resolution"] = res_match.group(1)
                codecs_match = re.search(r'CODECS="([^"]+)"', attrs)
                if codecs_match:
                    codecs = codecs_match.group(1).lower().split(",")
                    variant["video_codec"] = next((c.strip() for c in codecs if c.strip().startswith(("avc", "hvc", "hev", "av01", "vp"))), variant.get("video_codec"))
                    variant["audio_codec"] = next((c.strip() for c in codecs if c.strip().startswith(("mp4a", "ac-3", "ec-3", "opus"))), variant.get("audio_codec"))
                return variant

        info = {"format": "HLS", "resolution": None, "video_codec": None,
                "audio_codec": None, "bitrate_kbps": None, "fps": None}
        codecs = re.search(r'CODECS="([^"]+)"', text, re.IGNORECASE)
        if codecs:
            parts = [c.strip().lower() for c in codecs.group(1).split(",")]
            info["video_codec"] = next((c for c in parts if c.startswith(("avc", "hvc", "hev", "av01", "vp"))), None)
            info["audio_codec"] = next((c for c in parts if c.startswith(("mp4a", "ac-3", "ec-3", "opus"))), None)
        resolution = re.search(r'RESOLUTION=(\d+x\d+)', text)
        if resolution:
            info["resolution"] = resolution.group(1)
        fps = re.search(r'FRAME-RATE=([\d.]+)', text)
        if fps:
            info["fps"] = float(fps.group(1))
        if not any(info[key] for key in ("resolution", "video_codec", "audio_codec", "bitrate_kbps", "fps")):
            return _probe_media_segment(lines, final_url, info, timeout)
        return info
    except Exception:
        return None


def _info_from_media_headers(content_type: str, body: bytes) -> dict:
    """Return only information actually present in HTTP media headers/body."""
    info = {"format": content_type.split(";", 1)[0] or "video", "resolution": None,
            "video_codec": None, "audio_codec": None, "bitrate_kbps": None, "fps": None}
    text = body.decode("utf-8", errors="ignore")
    match = re.search(r'(?:WIDTH|RESOLUTION)\s*[=:]\s*(\d{3,4})\s*[,xX]\s*(\d{3,4})', text, re.I)
    if match:
        info["resolution"] = f"{match.group(1)}x{match.group(2)}"
    return info


def _probe_media_segment(lines: list[str], playlist_url: str, info: dict, timeout: int) -> dict:
    """Inspect one small TS/fMP4 segment for stream descriptors and dimensions."""
    segment = next((line for line in lines if not line.startswith("#")), None)
    if not segment:
        return info
    try:
        req = urllib.request.Request(urllib.parse.urljoin(playlist_url, segment), headers={
            "User-Agent": "Mozilla/5.0 (compatible; PatziBOT/1.0)", "Range": "bytes=0-262143"
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(262144)
        # MPEG-TS stream_type values: AVC, HEVC, MPEG-2 video, AAC, AC-3.
        if b"\x1b\xe0" in data or b"\x1b\xc0" in data:
            info["video_codec"] = "h264"
        elif b"\x24\xe0" in data:
            info["video_codec"] = "hevc"
        if b"\x0f\xc0" in data or b"\x11\xc0" in data:
            info["audio_codec"] = "aac"
        elif b"\x81\xc0" in data:
            info["audio_codec"] = "ac3"
        # ISO BMFF sample entry markers are real codec identifiers.
        if b"avc1" in data or b"avc3" in data:
            info["video_codec"] = "avc1"
        elif b"hvc1" in data or b"hev1" in data:
            info["video_codec"] = "hevc"
        if b"mp4a" in data:
            info["audio_codec"] = "aac"
        elif b"ac-3" in data or b"ec-3" in data:
            info["audio_codec"] = "ac3"
        # H.264 SPS dimensions: parse the common baseline/main/high SPS form.
        for sps in re.finditer(rb"\x00\x00\x00?\x01\x67([^\x00\x00]{8,96})", data):
            dimensions = _h264_sps_dimensions(sps.group(1))
            if dimensions:
                info["resolution"] = f"{dimensions[0]}x{dimensions[1]}"
                break
        return info
    except Exception:
        return info


def _h264_sps_dimensions(sps: bytes) -> tuple[int, int] | None:
    """Decode frame dimensions from an H.264 sequence parameter set."""
    try:
        rbsp = bytearray()
        zeros = 0
        for byte in sps:
            if zeros >= 2 and byte == 3:
                zeros = 0
                continue
            rbsp.append(byte)
            zeros = zeros + 1 if byte == 0 else 0

        class Bits:
            def __init__(self, data: bytes): self.data, self.pos = data, 0
            def read(self, n: int) -> int:
                value = 0
                for _ in range(n):
                    value = (value << 1) | ((self.data[self.pos // 8] >> (7 - self.pos % 8)) & 1)
                    self.pos += 1
                return value
            def ue(self) -> int:
                zeros = 0
                while self.read(1) == 0: zeros += 1
                return (1 << zeros) - 1 + (self.read(zeros) if zeros else 0)
            def se(self) -> int:
                value = self.ue()
                return (value + 1) // 2 if value & 1 else -(value // 2)

        bits = Bits(bytes(rbsp))
        profile = bits.read(8); bits.read(8); bits.read(8); bits.ue()
        chroma = 1
        if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            chroma = bits.ue()
            if chroma == 3: bits.read(1)
            bits.ue(); bits.ue(); bits.read(1)
            if bits.read(1):
                for i in range(8 if chroma != 3 else 12):
                    if bits.read(1):
                        size = 16 if i < 6 else 64
                        last, nxt = 8, 8
                        for _ in range(size):
                            if nxt: nxt = (last + bits.se() + 256) % 256
                            last = nxt or last
        bits.ue()
        poc = bits.ue()
        if poc == 0: bits.ue()
        elif poc == 1:
            bits.read(1); bits.se(); bits.se()
            for _ in range(bits.ue()): bits.se()
        bits.ue(); bits.read(1)
        width = (bits.ue() + 1) * 16
        height = (bits.ue() + 1) * 16
        frame_only = bits.read(1)
        if not frame_only: bits.read(1)
        bits.read(1)
        if bits.read(1):
            left, right, top, bottom = bits.ue(), bits.ue(), bits.ue(), bits.ue()
            sub_width = 1 if chroma == 0 else (2 if chroma in (1, 2) else 1)
            sub_height = 2 if chroma == 1 else 1
            crop_x, crop_y = sub_width, sub_height * (2 - frame_only)
            width -= (left + right) * crop_x
            height -= (top + bottom) * crop_y
        return (width, height) if 0 < width <= 8192 and 0 < height <= 4320 else None
    except Exception:
        return None


def _hls_bandwidth(attrs: str) -> int:
    match = re.search(r'(?:^|,)(?:AVERAGE-)?BANDWIDTH=(\d+)', attrs)
    return int(match.group(1)) if match else 0


async def _async_stream_info(url: str) -> dict | None:
    async with _metadata_semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, _read_hls_info, url)


def parse_m3u(content: str) -> list[dict]:
    def attribute(line: str, *names: str) -> str | None:
        for name in names:
            match = re.search(
                rf'''(?:^|\s){re.escape(name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s,]+))''',
                line,
                re.IGNORECASE,
            )
            if match:
                value = next((part for part in match.groups() if part is not None), "").strip()
                if value:
                    return html.unescape(value)
        return None

    def display_name(line: str) -> str:
        # EXTINF metadata ends at the first comma; the channel name follows it.
        comma = line.find(",")
        return line[comma + 1:].strip() if comma >= 0 else ""

    def raw_attribute(line: str, name: str) -> str:
        return attribute(line, name) or ""

    header_line = next((line.lstrip("\ufeff ").rstrip() for line in content.splitlines() if line.lstrip("\ufeff ").upper().startswith("#EXTM3U")), "")
    global_shift_raw = raw_attribute(header_line, "tvg-shift")

    channels = []
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            category = attribute(line, "group-title", "group") or ""
            logo_url = attribute(line, "tvg-logo", "tvg_logo", "tvgLogo")
            channel_shift_raw = raw_attribute(line, "tvg-shift") or global_shift_raw
            name = display_name(line)
            if not name:
                name = attribute(line, "tvg-name", "tvg_name") or ""
            if not name:
                name = "Canal sin nombre"

            i += 1
            while i < len(lines) and lines[i].startswith("#"):
                i += 1
            if i < len(lines):
                stream_url = lines[i].strip()
                if stream_url.lower().startswith(
                    ("http://", "https://", "rtmp://", "rtsp://")
                ):
                    channels.append({
                        "name": name[:150],
                        "category": category[:100],
                        "logo_url": logo_url[:2048] if logo_url else None,
                        "tvg_logo": raw_attribute(line, "tvg-logo"),
                        "country": "SV",
                        "catchup": raw_attribute(line, "catchup"),
                        "catchup_days": raw_attribute(line, "catchup-days"),
                        "catchup_source": raw_attribute(line, "catchup-source"),
                        "tvg_shift": channel_shift_raw,
                        "tvg_id": raw_attribute(line, "tvg-id"),
                        "tvg_name": raw_attribute(line, "tvg-name"),
                        "tvg_country": raw_attribute(line, "tvg-country"),
                        "tvg_language": raw_attribute(line, "tvg-language"),
                        "tvg_chno": raw_attribute(line, "tvg-chno"),
                        "stream_url": stream_url[:4096],
                        "enabled": True,
                    })
        i += 1
    return channels


# ─────────────────────────────────────────────
#  Public – list channels
# ─────────────────────────────────────────────

@router.get("/channels")
def list_channels(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Channel).order_by(Channel.category, Channel.name)

    if user.role != "admin":
        subscription = get_active_subscription(db, user.id)
        if subscription is None:
            raise HTTPException(
                status_code=403,
                detail="Necesitas una suscripción vigente para ver los canales.",
            )
        assigned_channel_ids = select(PackageChannel.channel_id).where(
            PackageChannel.package_id == subscription.package_id
        )
        query = query.where(
            Channel.enabled.is_(True),
            Channel.id.in_(assigned_channel_ids),
        )

    channels = db.scalars(query).all()
    total   = len(channels)
    live    = sum(1 for c in channels if c.is_live)

    favorite_ids = set(db.scalars(
        select(ChannelFavorite.channel_id).where(ChannelFavorite.user_id == user.id)
    ).all())

    try:
        return {
            "total": total,
            "live": live,
            "disabled": sum(1 for c in channels if not c.enabled),
            "channels": [client_channel_dict(c, is_favorite=c.id in favorite_ids) for c in channels],
        }
    finally:
        clear_epg_cache()


@router.get("/admin/epg/channels")
def admin_epg_channels(admin: User = Depends(require_admin)):
    try:
        return {"channels": list_epg_channels()}
    finally:
        clear_epg_cache()


@router.get("/client/epg/version")
def client_epg_version(user: User = Depends(get_current_user)):
    return {"version": current_epg_version()}


@router.get("/client/epg/{version}.json")
def download_client_epg(version: str, user: User = Depends(get_current_user)):
    guide_path = epg_file_for_version(version)
    if guide_path is None:
        raise HTTPException(status_code=404, detail="La versión de la guía no está disponible.")
    return FileResponse(
        guide_path,
        media_type="application/json",
        filename="epg.json",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _channel_available_to_user(db: Session, user: User, channel_id: int) -> Channel:
    channel = db.get(Channel, channel_id)
    if channel is None or (user.role != "admin" and not channel.enabled):
        raise HTTPException(status_code=404, detail="Canal no encontrado.")
    if user.role != "admin":
        subscription = get_active_subscription(db, user.id)
        if subscription is None:
            raise HTTPException(status_code=403, detail="Necesitas una suscripción vigente.")
        assigned = db.scalar(select(PackageChannel.channel_id).where(
            PackageChannel.package_id == subscription.package_id,
            PackageChannel.channel_id == channel_id,
        ))
        if assigned is None:
            raise HTTPException(status_code=404, detail="Canal no encontrado.")
    return channel


@router.put("/channels/{channel_id}/favorite")
def add_favorite_channel(
    channel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    channel = _channel_available_to_user(db, user, channel_id)
    favorite = db.get(ChannelFavorite, (user.id, channel.id))
    if favorite is None:
        db.add(ChannelFavorite(user_id=user.id, channel_id=channel.id))
        db.commit()
    return {"channel_id": channel.id, "is_favorite": True}


@router.delete("/channels/{channel_id}/favorite")
def remove_favorite_channel(
    channel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _channel_available_to_user(db, user, channel_id)
    db.execute(delete(ChannelFavorite).where(
        ChannelFavorite.user_id == user.id,
        ChannelFavorite.channel_id == channel_id,
    ))
    db.commit()
    return {"channel_id": channel_id, "is_favorite": False}


@router.post("/channels/{channel_id}/view")
def record_channel_view(
    channel_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _channel_available_to_user(db, user, channel_id)
    db.add(ChannelView(user_id=user.id, channel_id=channel_id))
    db.commit()
    return {"channel_id": channel_id, "recorded": True}


# ─────────────────────────────────────────────
#  Admin – CRUD
# ─────────────────────────────────────────────

@router.get("/admin/channels")
def list_admin_channels(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    channels = db.scalars(select(Channel).order_by(Channel.id)).all()
    return {
        "total": len(channels),
        "live": sum(1 for channel in channels if channel.is_live),
        "disabled": sum(1 for channel in channels if not channel.enabled),
        "channels": [channel_dict(channel) for channel in channels],
    }


@router.post("/admin/channels", status_code=status.HTTP_201_CREATED)
def create_channel(
    data: ChannelCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    channel = Channel(
        name=data.name.strip(),
        category=data.category.strip(),
        logo_url=data.logo_url,
        stream_url=data.stream_url.strip(),
        enabled=data.enabled,
        country=data.country,
        catchup=data.catchup,
        catchup_days=data.catchup_days,
        catchup_source=data.catchup_source,
        tvg_shift=data.tvg_shift,
        tvg_id=data.tvg_id,
        tvg_name=data.tvg_name,
        tvg_logo=data.tvg_logo or data.logo_url or "",
        tvg_country=data.tvg_country,
        tvg_language=data.tvg_language,
        tvg_chno=data.tvg_chno,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return {"channel": channel_dict(channel), "detail": "Canal creado correctamente."}


@router.get("/admin/channels/{channel_id}")
def get_admin_channel(
    channel_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal no encontrado.")
    return {"channel": channel_dict(channel)}


@router.put("/admin/channels/{channel_id}")
def update_channel(
    channel_id: int,
    data: ChannelUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal no encontrado.")
    channel.name       = data.name.strip()
    channel.category   = data.category.strip()
    channel.logo_url   = data.logo_url
    channel.stream_url = data.stream_url.strip()
    channel.enabled    = data.enabled
    channel.country = data.country
    channel.catchup = data.catchup
    channel.catchup_days = data.catchup_days
    channel.catchup_source = data.catchup_source
    channel.tvg_shift = data.tvg_shift
    channel.tvg_id = data.tvg_id
    channel.tvg_name = data.tvg_name
    channel.tvg_logo = data.tvg_logo or data.logo_url or ""
    channel.tvg_country = data.tvg_country
    channel.tvg_language = data.tvg_language
    channel.tvg_chno = data.tvg_chno
    db.commit()
    db.refresh(channel)
    return {"channel": channel_dict(channel), "detail": "Canal actualizado correctamente."}


@router.patch("/admin/channels/{channel_id}/status")
def update_channel_status(
    channel_id: int,
    data: ChannelStatusUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal no encontrado.")
    channel.enabled = data.enabled
    db.commit()
    db.refresh(channel)
    return {
        "channel": channel_dict(channel),
        "detail": f"Canal {'habilitado' if data.enabled else 'deshabilitado'} correctamente.",
    }


@router.delete("/admin/channels/{channel_id}")
def delete_channel(
    channel_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal no encontrado.")
    db.query(PackageChannel).filter(PackageChannel.channel_id == channel.id).delete(synchronize_session=False)
    db.delete(channel)
    db.commit()
    return {"detail": "Canal eliminado correctamente."}


# ─────────────────────────────────────────────
#  Admin – Batch
# ─────────────────────────────────────────────

@router.post("/admin/channels/batch-delete")
def batch_delete_channels(
    data: BatchDeleteInput,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not data.ids:
        return {"detail": "No se proporcionaron IDs.", "deleted": 0}
    channels = db.scalars(select(Channel).where(Channel.id.in_(data.ids))).all()
    count = len(channels)
    channel_ids = [channel.id for channel in channels]
    if channel_ids:
        db.query(PackageChannel).filter(PackageChannel.channel_id.in_(channel_ids)).delete(synchronize_session=False)
    for c in channels:
        db.delete(c)
    db.commit()
    return {"detail": f"Se eliminaron {count} canales.", "deleted": count}


@router.post("/admin/channels/batch-status")
def batch_update_status(
    data: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ids     = data.get("ids", [])
    enabled = bool(data.get("enabled", True))
    if not ids:
        return {"detail": "No se proporcionaron IDs.", "updated": 0}
    channels = db.scalars(select(Channel).where(Channel.id.in_(ids))).all()
    for c in channels:
        c.enabled = enabled
    db.commit()
    return {
        "detail": f"{len(channels)} canales {'habilitados' if enabled else 'deshabilitados'}.",
        "updated": len(channels),
    }


# ─────────────────────────────────────────────
#  Admin – Health check
# ─────────────────────────────────────────────

@router.get("/admin/channels/{channel_id}/check")
async def check_channel(
    channel_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Check if a single channel stream URL is reachable."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal no encontrado.")

    online, stream_info = await asyncio.gather(
        _async_check(channel.stream_url), _async_stream_info(channel.stream_url)
    )
    now    = utc_now()

    if online and not channel.is_live:
        channel.online_since = now
    elif not online:
        channel.online_since = None

    channel.is_live      = online
    channel.last_checked = now
    channel.stream_info  = json.dumps(stream_info) if stream_info else None
    db.commit()
    db.refresh(channel)

    return {"id": channel_id, "online": online, "channel": channel_dict(channel)}


@router.post("/admin/channels/check-all")
async def check_all_channels(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Check all enabled channels concurrently and persist results."""
    async with _channel_check_lock:
        async with maintenance_lock:
            return await _check_all_channels_with_metadata(db)


async def _check_all_channels_with_metadata(db: Session) -> dict:
    channels = db.scalars(select(Channel).where(Channel.enabled.is_(True))).all()
    if not channels:
        return {"checked": 0, "online": 0, "offline": 0}

    results = await asyncio.gather(*[
        asyncio.gather(_async_check(c.stream_url), _async_stream_info(c.stream_url))
        for c in channels
    ])

    now  = utc_now()
    live = 0
    for ch, (online, stream_info) in zip(channels, results):
        if online and not ch.is_live:
            ch.online_since = now
        elif not online:
            ch.online_since = None
        ch.is_live      = online
        ch.last_checked = now
        ch.stream_info = json.dumps(stream_info) if stream_info else None
        if online:
            live += 1

    db.commit()
    return {"checked": len(channels), "online": live, "offline": len(channels) - live}


# ─────────────────────────────────────────────
#  Admin – Import M3U
# ─────────────────────────────────────────────

@router.post("/admin/channels/import-m3u")
async def import_m3u(
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = content_bytes.decode("latin-1", errors="ignore")

    parsed_channels = parse_m3u(content)
    if not parsed_channels:
        raise HTTPException(
            status_code=400,
            detail="No se encontraron canales válidos en el archivo M3U.",
        )

    stream_urls = [item["stream_url"] for item in parsed_channels]
    existing = {
        channel.stream_url: channel
        for channel in db.scalars(select(Channel).where(Channel.stream_url.in_(stream_urls))).all()
    }
    new_channels = []
    logos_updated = 0
    for item in parsed_channels:
        channel = existing.get(item["stream_url"])
        if channel is None:
            channel = Channel(**item)
            new_channels.append(channel)
            existing[item["stream_url"]] = channel
        else:
            if not channel.logo_url and item.get("logo_url"):
                logos_updated += 1
            for field in (
                "name", "category", "catchup", "catchup_days",
                "catchup_source", "tvg_shift", "tvg_id", "tvg_name", "tvg_logo",
                "tvg_country", "tvg_language", "tvg_chno",
            ):
                setattr(channel, field, item.get(field, ""))
            # Keep an existing manually supplied app logo if this playlist has no tvg-logo.
            if item.get("logo_url"):
                channel.logo_url = item["logo_url"]
    db.add_all(new_channels)
    db.commit()

    return {
        "imported": len(new_channels),
        "logos_updated": logos_updated,
        "with_logo": sum(1 for item in parsed_channels if item.get("logo_url")),
        "without_logo": sum(1 for item in parsed_channels if not item.get("logo_url")),
        "detail": f"Se agregaron {len(new_channels)} canales y se completaron {logos_updated} logos faltantes.",
    }



