import re

MAX_PLAYLIST_SIZE = 10 * 1024 * 1024
MAX_PLAYLIST_ENTRIES = 5000

_FIELDS = (
    "group_title",
    "tvg_id",
    "tvg_name",
    "tvg_chno",
    "tvg_logo",
    "tvg_country",
    "tvg_language",
    "tvg_shift",
    "catchup",
    "catchup_days",
    "catchup_source",
    "tvg_type",
    "tvg_season",
    "tvg_episode",
    "serie_title",
    "tvg_tmdb",
    "audio_track",
    "aspect_ratio",
)
_ATTRIBUTE = re.compile(
    r'''([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s,]*))'''
)


def _new_entry(extinf: str) -> dict[str, str]:
    line = extinf.partition(":")[2]
    quote = None
    comma = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in ("\"", "'"):
            quote = char
        elif char == ",":
            comma = index
            break
    metadata = line if comma is None else line[:comma]
    title = "" if comma is None else line[comma + 1 :].strip()
    attributes = {}
    for match in _ATTRIBUTE.finditer(metadata):
        key = match.group(1).lower().replace("-", "_")
        value = next((part for part in match.groups()[1:] if part is not None), "")
        attributes[key] = value.strip()
    entry = {field: attributes.get(field, "") for field in _FIELDS}
    entry["name"] = title
    entry["stream_url"] = ""
    return entry


def _finish(entry: dict[str, str] | None, results: list[dict[str, str]]) -> None:
    if entry is not None:
        results.append(entry)


def parse_playlist(content: bytes) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("Playlist must be UTF-8 or Windows-1252 encoded.") from exc

    lines = [line.strip() for line in text.splitlines()]
    if not any(line.upper() == "#EXTM3U" or line.upper().startswith("#EXTM3U ") for line in lines[:5]):
        raise ValueError("File does not contain an #EXTM3U playlist header.")

    results: list[dict[str, str]] = []
    pending: dict[str, str] | None = None
    for line in lines:
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            _finish(pending, results)
            pending = _new_entry(line)
            continue
        if pending is None:
            continue
        if line.upper().startswith("#EXTVLCOPT:"):
            option = line.partition(":")[2]
            key, separator, value = option.partition("=")
            key = key.strip().lower().replace("-", "_")
            if separator and key in {"audio_track", "aspect_ratio"}:
                pending[key] = value.strip()
            continue
        if line.startswith("#"):
            continue
        pending["stream_url"] = line
        _finish(pending, results)
        pending = None

    _finish(pending, results)
    return results
