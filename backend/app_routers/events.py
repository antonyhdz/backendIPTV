"""UEFA Champions League scoreboard proxy with a short shared cache."""

import asyncio
import json
import re
import time
import unicodedata
from threading import Lock
from urllib.parse import quote
from urllib.request import Request, urlopen
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app_database import get_db
from backend.app_dependencies import get_current_user
from backend.app_models import Channel, PackageChannel, User
from backend.app_services.epg_service import clear_epg_cache, epg_for_channel
from backend.app_services.subscription_service import get_active_subscription


router = APIRouter(prefix="/api/v1/client/events", tags=["Eventos"])
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/summary"
STADIUMS_DIR = Path(__file__).resolve().parents[1] / "estadios"
STADIUM_DATA_FILE = Path(__file__).resolve().parents[1] / "varios" / "SoccerStadiums.json"
CACHE_SECONDS = 15
_cache_lock = Lock()
_refresh_lock = asyncio.Lock()
_cache_payload: dict | None = None
_cache_expiry = 0.0
_summary_cache: dict[str, tuple[float, dict]] = {}


def _normalized_stadium_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "_".join("".join(char for char in decomposed if not unicodedata.combining(char)).replace("_", " ").split())


def _stadium_image_path(venue: str) -> str | None:
    if not venue or not STADIUMS_DIR.is_dir():
        return None
    expected = _normalized_stadium_name(venue)
    for image in STADIUMS_DIR.glob("*.jpg"):
        if _normalized_stadium_name(image.stem) == expected:
            return f"/stadiums/{quote(image.name)}"
    return None


try:
    _STADIUM_RECORDS = json.loads(STADIUM_DATA_FILE.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    _STADIUM_RECORDS = []


def _stadium_metadata(venue: str) -> dict:
    aliases = {
        _normalized_stadium_name("RAMS Park"): _normalized_stadium_name("Türk Telekom Stadyumu"),
        _normalized_stadium_name("Riyadh Air Metropolitano"): _normalized_stadium_name("Wanda Metropolitano"),
        _normalized_stadium_name("Spotify Camp Nou"): _normalized_stadium_name("Camp Nou"),
        _normalized_stadium_name("Raiffeisen Arena"): _normalized_stadium_name("Linzer Stadion"),
        _normalized_stadium_name("Raiffeisen Arena (Linz)"): _normalized_stadium_name("Linzer Stadion"),
    }
    target = aliases.get(_normalized_stadium_name(venue), _normalized_stadium_name(venue))
    for stadium in _STADIUM_RECORDS:
        if _normalized_stadium_name(str(stadium.get("Name", ""))) == target:
            return {
                "city": stadium.get("Town") or "",
                "country": stadium.get("Nation") or "",
                "capacity": stadium.get("Capacity") or "",
            }
    return {}


def _fetch_scoreboard() -> dict:
    request = Request(SCOREBOARD_URL, headers={"User-Agent": "PatziIPTV/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_summary(event_id: str) -> dict:
    # ESPN summary expects the top-level scoreboard event.id; venue.id is a stadium ID.
    request = Request(f"{SUMMARY_URL}?event={quote(event_id)}", headers={"User-Agent": "PatziIPTV/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", plain)


def _team_match_aliases(team: dict) -> set[str]:
    aliases = set()
    for key in ("name", "short_name"):
        value = str(team.get(key) or "")
        normalized = _normalize_match_text(value)
        if len(normalized) >= 4:
            aliases.add(normalized)
        # Feeds commonly omit club prefixes such as VfB, FC, or AFC.
        words = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE).split()
        while len(words) > 1 and words[0].casefold() in {"vfb", "fc", "cf", "afc", "ac", "sc", "sporting"}:
            words.pop(0)
        shortened = _normalize_match_text(" ".join(words))
        if len(shortened) >= 4:
            aliases.add(shortened)
    return aliases


def _live_broadcast_channels(events: list[dict], user: User, db: Session) -> dict[str, list[dict]]:
    """Match live event team names against current ESPN programme titles in the user's lineup."""
    if user.role == "admin":
        query = select(Channel).where(Channel.enabled.is_(True))
    else:
        subscription = get_active_subscription(db, user.id)
        if subscription is None:
            return {}
        assigned_channels = select(PackageChannel.channel_id).where(
            PackageChannel.package_id == subscription.package_id
        )
        query = select(Channel).where(Channel.enabled.is_(True), Channel.id.in_(assigned_channels))
    channels = db.scalars(query.order_by(Channel.name)).all()

    matches: dict[str, list[dict]] = {}
    for event in events:
        if event.get("state") != "in":
            continue
        home_aliases = _team_match_aliases(event.get("home") or {})
        away_aliases = _team_match_aliases(event.get("away") or {})
        if not home_aliases or not away_aliases:
            continue
        candidates = []
        for channel in channels:
            channel_name = f"{channel.name} {channel.tvg_name}".casefold()
            if "espn" not in channel_name:
                continue
            programme = epg_for_channel(
                channel.name,
                channel_id=channel.tvg_id or None,
            )
            title = programme.get("current_program_title")
            if not title:
                continue
            normalized_title = _normalize_match_text(title)
            home_match = any(alias in normalized_title for alias in home_aliases)
            away_match = any(alias in normalized_title for alias in away_aliases)
            if not (home_match and away_match):
                continue
            country = (channel.tvg_country or channel.country or "").upper()
            local_priority = 0 if country.startswith("SV") or "el salvador" in channel.name.casefold() else 1
            candidates.append((local_priority, channel.name.casefold(), {
                "id": channel.id,
                "name": channel.name,
                "logo_url": channel.logo_url or channel.tvg_logo or programme.get("epg_logo_url"),
                "program_title": title,
            }))
        matches[event["id"]] = [candidate[2] for candidate in sorted(candidates, key=lambda item: (item[0], item[1]))]
    return matches


def _normalize_scoreboard(raw: dict) -> dict:
    league = (raw.get("leagues") or [{}])[0]
    events = []
    for event in raw.get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        status = competition.get("status") or event.get("status") or {}
        status_type = status.get("type") or {}
        period = status.get("period") or 0
        competitors = competition.get("competitors") or []
        home = next((team for team in competitors if team.get("homeAway") == "home"), None)
        away = next((team for team in competitors if team.get("homeAway") == "away"), None)
        if home is None or away is None:
            continue

        def team_data(team: dict) -> dict:
            info = team.get("team") or {}
            return {
                "id": str(info.get("id") or team.get("id") or ""),
                "name": info.get("displayName") or info.get("name") or "Equipo",
                "short_name": info.get("shortDisplayName") or info.get("abbreviation") or info.get("name") or "Equipo",
                "logo": info.get("logo"),
                "score": team.get("score"),
            }

        venue_data = competition.get("venue") or event.get("venue") or {}
        venue_address = venue_data.get("address") or {}
        venue_name = venue_data.get("fullName") or ""
        venue_metadata = _stadium_metadata(venue_name)
        league_name = league.get("name") or "UEFA Champions League"
        phase = str(competition.get("altGameNote") or "")
        if phase.startswith(league_name):
            phase = phase[len(league_name):].lstrip(" ,·-")
        if not phase:
            season_slug = str((event.get("season") or {}).get("slug") or "")
            phase = season_slug.replace("-", " ").title()
        match_status = status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or "Programado"
        if status_type.get("state") == "in":
            match_status = {
                1: "Primer tiempo",
                2: "Segundo tiempo",
                3: "Prórroga · primer tiempo",
                4: "Prórroga · segundo tiempo",
            }.get(period, match_status)
        events.append({
            "id": str(event.get("id") or competition.get("id") or ""),
            "name": event.get("name") or "Partido",
            "date": event.get("date") or competition.get("date"),
            "state": status_type.get("state") or "pre",
            "status": match_status,
            "clock": status.get("displayClock") or "",
            "period": period,
            "league": league_name,
            "phase": phase,
            "venue": venue_name,
            "city": venue_metadata.get("city") or venue_address.get("city") or "",
            "country": venue_metadata.get("country") or venue_address.get("country") or "",
            "capacity": venue_metadata.get("capacity") or "",
            "stadium_image_path": _stadium_image_path(venue_name),
            "home": team_data(home),
            "away": team_data(away),
            "summary_url": next((link.get("href") for link in event.get("links", []) if "summary" in link.get("rel", [])), None),
        })
    events.sort(key=lambda item: (0 if item["state"] == "in" else 1 if item["state"] == "pre" else 2, item["date"] or ""))
    return {"league": league.get("name") or "UEFA Champions League", "events": events,
            "has_live": any(event["state"] == "in" for event in events), "updated_at": time.time()}


@router.get("")
async def get_events(
    request: FastAPIRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    global _cache_payload, _cache_expiry
    now = time.monotonic()

    async def response_with_broadcasts(payload: dict) -> dict:
        base_url = str(request.base_url).rstrip("/")
        try:
            broadcasts = await asyncio.to_thread(_live_broadcast_channels, payload["events"], _user, db)
        finally:
            clear_epg_cache()
        return {
            **payload,
            "events": [
                {
                    **event,
                    "stadium_image_url": f"{base_url}{event['stadium_image_path']}" if event.get("stadium_image_path") else None,
                    "broadcast_channels": broadcasts.get(event["id"], []),
                }
                for event in payload["events"]
            ],
        }

    with _cache_lock:
        cached_payload = _cache_payload if _cache_payload is not None and now < _cache_expiry else None
    if cached_payload is not None:
        return await response_with_broadcasts(cached_payload)
    async with _refresh_lock:
        with _cache_lock:
            cached_payload = _cache_payload if _cache_payload is not None and time.monotonic() < _cache_expiry else None
        if cached_payload is not None:
            return await response_with_broadcasts(cached_payload)
        try:
            raw = await asyncio.to_thread(_fetch_scoreboard)
            payload = _normalize_scoreboard(raw)
        except Exception as exc:
            with _cache_lock:
                cached_payload = _cache_payload
            if cached_payload is not None:
                return await response_with_broadcasts(cached_payload)
            raise HTTPException(status_code=502, detail="No se pudo consultar el marcador de ESPN.") from exc
        with _cache_lock:
            _cache_payload = payload
            _cache_expiry = time.monotonic() + CACHE_SECONDS
        return await response_with_broadcasts(payload)


@router.get("/{event_id}/summary")
async def get_event_summary(event_id: str, _user: User = Depends(get_current_user)):
    if not event_id.isdigit():
        raise HTTPException(status_code=400, detail="El ID del evento no es válido.")
    now = time.monotonic()
    cached = _summary_cache.get(event_id)
    if cached and now < cached[0]:
        return cached[1]
    try:
        payload = await asyncio.to_thread(_fetch_summary, event_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo consultar el resumen de ESPN.") from exc
    _summary_cache[event_id] = (time.monotonic() + CACHE_SECONDS, payload)
    return payload




