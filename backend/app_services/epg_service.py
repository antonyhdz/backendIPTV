"""Reader for the generated El Salvador-localized epg/epg.json guide."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EPG_DIR = Path(__file__).resolve().parents[2] / "backend" / "epg"
LEGACY_EPG_FILE = EPG_DIR / "epg.json"
_VERSION_PATTERN = re.compile(r"^\d{14}$")
_logger = logging.getLogger(__name__)
_lock = Lock()
_cached_signature: tuple[str, int, int] | None = None
_catalog: dict[str, dict] = {}
_aliases: dict[str, dict] = {}
_schedules: dict[str, tuple[list[datetime], list[tuple[datetime, datetime, str, str]]]] = {}


def clear_epg_cache() -> None:
    """Discard the in-memory index so the next API request reads epg.json again."""
    global _cached_signature, _catalog, _aliases, _schedules
    with _lock:
        _cached_signature = None
        _catalog, _aliases, _schedules = {}, {}, {}


def _el_salvador_timezone():
    try:
        return ZoneInfo("America/El_Salvador")
    except ZoneInfoNotFoundError:  # Windows installations may not include the system tz database.
        return timezone(timedelta(hours=-6), "America/El_Salvador")


_EL_SALVADOR = _el_salvador_timezone()


def latest_epg_file() -> Path | None:
    """Read the single generated guide used by the API."""
    return LEGACY_EPG_FILE if LEGACY_EPG_FILE.is_file() else None


def current_epg_version() -> str | None:
    path = latest_epg_file()
    if path is None:
        return None
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None
    return modified.astimezone(_EL_SALVADOR).strftime("%Y%m%d%H%M%S")


def epg_file_for_version(version: str) -> Path | None:
    if not _VERSION_PATTERN.fullmatch(version):
        return None
    return latest_epg_file()


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value)


def _channel_aliases(channel_id: str, display_name: str) -> set[str]:
    aliases = {_normalize(display_name)}
    slug = channel_id.split(".", 1)[0]
    aliases.add(_normalize(slug.replace("_", " ")))
    numeric_channel = re.match(r"^(\d+)(?:_de)?(?:_|$)", slug, flags=re.IGNORECASE)
    if numeric_channel:
        number = numeric_channel.group(1)
        aliases.update({number, f"canal{number}"})
    slug_aliases = {
        "2": "canal2", "4": "canal4", "6": "canal6", "12": "canal12", "21": "canal21",
        "nickelodeon": "nick", "nickjunior": "nickjr", "nickjr": "nickjr",
        "eentertainmenttelevision": "e", "usanetwork": "usa", "canalusanetwork": "usa",
        "tntnovelas": "tntnovelassv",
    }
    alias = slug_aliases.get(_normalize(slug.replace("_", " ")))
    if alias:
        aliases.add(_normalize(alias))
    return {alias for alias in aliases if alias}


def _parse_json_timestamp(value: object) -> datetime | None:
    """Parse compact timestamps already converted to El Salvador local time."""
    if value is None:
        return None
    text = str(value).strip()
    for pattern in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=_EL_SALVADOR).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _load_index() -> None:
    global _cached_signature, _catalog, _aliases, _schedules
    epg_file = latest_epg_file()
    if epg_file is None:
        with _lock:
            _cached_signature = None
            _catalog, _aliases, _schedules = {}, {}, {}
        _logger.warning("EPG JSON file is unavailable in %s", EPG_DIR)
        return

    try:
        stat = epg_file.stat()
    except OSError:
        with _lock:
            _cached_signature = None
            _catalog, _aliases, _schedules = {}, {}, {}
        _logger.warning("EPG JSON file is unavailable: %s", epg_file)
        return

    signature = (epg_file.name, stat.st_mtime_ns, stat.st_size)
    if signature == _cached_signature:
        return

    with _lock:
        if signature == _cached_signature:
            return
        catalog: dict[str, dict] = {}
        aliases: dict[str, dict] = {}
        schedules: dict[str, tuple[list[datetime], list[tuple[datetime, datetime, str, str]]]] = {}
        try:
            with epg_file.open("r", encoding="utf-8-sig") as source:
                data = json.load(source)
            if not isinstance(data, dict):
                raise ValueError("EPG JSON root must be an object keyed by channel ID")

            for raw_channel_id, channel_data in data.items():
                if not isinstance(channel_data, dict):
                    continue
                channel_id = str(raw_channel_id)
                fallback_name = re.sub(r"\.[a-z]{2}$", "", channel_id, flags=re.IGNORECASE).replace("_", " ")
                display_name = str(channel_data.get("name") or fallback_name).strip()
                record = {
                    "id": channel_id,
                    "name": str(channel_data.get("name") or display_name).strip(),
                    "logo": channel_data.get("logo") or channel_data.get("tvg-logo"),
                    "country": str(channel_data.get("country") or "").upper(),
                }
                catalog[channel_id] = record
                for key in _channel_aliases(channel_id, display_name):
                    aliases.setdefault(key, record)

                schedule = []
                for program in channel_data.get("programas", []) or []:
                    if not isinstance(program, dict):
                        continue
                    start = _parse_json_timestamp(program.get("start"))
                    stop = _parse_json_timestamp(program.get("stop"))
                    title = str(program.get("title") or "").strip()
                    if start and stop and stop > start and title:
                        schedule.append((start, stop, title, str(program.get("category") or "")))
                if schedule:
                    schedule.sort(key=lambda item: item[0])
                    schedules[channel_id] = ([item[0] for item in schedule], schedule)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _logger.exception("Could not read EPG JSON file %s: %s", epg_file, exc)
            catalog, aliases, schedules = {}, {}, {}

        _catalog, _aliases, _schedules, _cached_signature = catalog, aliases, schedules, signature


def _current_program(channel_id: str, now_utc: datetime):
    indexed = _schedules.get(channel_id)
    if not indexed:
        return None, None
    starts, schedule = indexed
    index = bisect_right(starts, now_utc) - 1
    current = None
    if index >= 0:
        start, stop, title, category = schedule[index]
        if start <= now_utc < stop:
            current = {"start": start, "stop": stop, "title": title, "category": category}
    next_index = index + 1 if current else bisect_left(starts, now_utc)
    next_program = None
    if 0 <= next_index < len(schedule):
        start, stop, title, category = schedule[next_index]
        next_program = {"start": start, "stop": stop, "title": title, "category": category}
    return current, next_program


def _local_time(value: datetime | None) -> str | None:
    return value.astimezone(_EL_SALVADOR).isoformat(timespec="seconds") if value else None


def list_epg_channels() -> list[dict]:
    """Return JSON guide channels, including the programme airing now in El Salvador."""
    _load_index()
    now = datetime.now(timezone.utc)
    result = []
    for channel in _catalog.values():
        item = dict(channel)
        current, _next = _current_program(channel["id"], now)
        if current:
            item["current_program_title"] = current["title"]
            item["current_program_start"] = _local_time(current["start"])
            item["current_program_end"] = _local_time(current["stop"])
        result.append(item)
    return sorted(result, key=lambda channel: (channel["name"].casefold(), channel["id"]))


_CHANNEL_NAME_ALIASES = {
    "canal 2": "2", "canal 4": "4", "canal 6": "6", "canal 12": "12", "canal 21": "21",
    "nick": "nickelodeon", "nick jr.": "nick jr", "e!": "e! entertainment television",
    "usa": "usa network", "usa network sv": "usa network", "tnt novelas": "tnt novelas sv",
    "discovery home & health": "discovery home and health", "h&h": "discovery home and health",
    "universal channel": "universal tv", "warner": "warner tv", "amc series": "amc", "fx": "fx",
}


def epg_for_channel(
    channel_name: str,
    *,
    channel_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Return current/next JSON guide data; programme times are already El Salvador local."""
    _load_index()
    lookup_name = _CHANNEL_NAME_ALIASES.get(channel_name.strip().casefold(), channel_name)
    record = (_catalog.get(channel_id) if channel_id else None) or _aliases.get(_normalize(lookup_name)) or _aliases.get(_normalize(channel_name))
    if record is None:
        return {}

    if now is None:
        now_utc = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now_utc = now.replace(tzinfo=timezone.utc)
    else:
        now_utc = now.astimezone(timezone.utc)
    current, next_program = _current_program(record["id"], now_utc)

    progress = 0
    if current:
        duration = (current["stop"] - current["start"]).total_seconds()
        progress = max(0, min(100, int((now_utc - current["start"]).total_seconds() * 100 / duration))) if duration else 0
    return {
        "epg_logo_url": record["logo"],
        "epg_channel_name": record["name"] or channel_name,
        "current_program_title": current["title"] if current else None,
        "current_program_category": current["category"] if current else None,
        "current_program_start": _local_time(current["start"]) if current else None,
        "current_program_end": _local_time(current["stop"]) if current else None,
        "current_program_progress": progress if current else None,
        "next_program_title": next_program["title"] if next_program else None,
        "next_program_start": _local_time(next_program["start"]) if next_program else None,
    }
