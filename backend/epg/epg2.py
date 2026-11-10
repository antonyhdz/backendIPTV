"""Actualiza GatoTV XML y lo fusiona en el archivo epg.json de Patzi."""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EPG_DIR = Path(__file__).resolve().parent
GATOTV_SCRIPT = EPG_DIR / "gatotv.py"
GATOTV_OUTPUT_DIR = EPG_DIR / "gatotv_cache"
EL_SALVADOR_TZ = timezone(timedelta(hours=-6))
# gatotv.py localiza los horarios scrapeados en UTC-5 y escribe ese offset
# explícitamente en XMLTV con xmltv_time(...). Si falta el offset, se asume -0500.
GATOTV_SOURCE_TZ = timezone(timedelta(hours=-5))
VERSION_RE = re.compile(r"^\d{14}$")
XMLTV_TIME_RE = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?$")
CHANNEL_NAME_RE = re.compile(r"^\s*([A-Za-z]{2})\s*-\s*GatoTV\s*-\s*(.*?)\s*$", re.I)


def to_el_salvador(value: str) -> str | None:
    """Parse XMLTV time (including GatoTV's -0500 offset) and return local time."""
    match = XMLTV_TIME_RE.fullmatch((value or "").strip())
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        offset = match.group(2)
        if offset:
            source_tz = datetime.strptime(offset, "%z").tzinfo
        else:
            source_tz = GATOTV_SOURCE_TZ
        return naive.replace(tzinfo=source_tz).astimezone(EL_SALVADOR_TZ).strftime("%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None


def parse_gatotv_xml(xml_path: Path) -> tuple[dict, int, int]:
    """Extract channel metadata and schedules into the approved Patzi schema."""
    channels: dict[str, dict] = {}
    parsed_programs = 0
    skipped_programs = 0
    root = None

    for event, element in ET.iterparse(xml_path, events=("start", "end")):
        if event == "start":
            if root is None:
                root = element
            continue

        if element.tag == "channel":
            channel_id = (element.get("id") or "").strip()
            if channel_id:
                display_names = [
                    (node.text or "").strip()
                    for node in element.findall("display-name")
                    if (node.text or "").strip()
                ]
                display_name = display_names[0] if display_names else channel_id
                name_match = CHANNEL_NAME_RE.match(display_name)
                country = name_match.group(1).upper() if name_match else ""
                channel_name = name_match.group(2).strip() if name_match else display_name
                icon = element.find("icon")
                logo = (icon.get("src") or "").strip() if icon is not None else ""
                record = channels.setdefault(channel_id, {
                    "name": channel_name,
                    "logo": logo,
                    "country": country,
                    "shift": "",
                    "programas": [],
                })
                if not record.get("logo") and logo:
                    record["logo"] = logo
            element.clear()
            root.clear()

        elif element.tag == "programme":
            channel_id = (element.get("channel") or "").strip()
            start = to_el_salvador(element.get("start") or "")
            stop = to_el_salvador(element.get("stop") or "")
            title_node = element.find("title")
            title = (title_node.text or "").strip() if title_node is not None else ""
            if channel_id and start and stop and title:
                record = channels.setdefault(channel_id, {
                    "name": channel_id,
                    "logo": "",
                    "country": "",
                    "shift": "",
                    "programas": [],
                })
                record["programas"].append({
                    "title": title,
                    "start": start,
                    "stop": stop,
                })
                parsed_programs += 1
            else:
                skipped_programs += 1
            element.clear()
            root.clear()

    for record in channels.values():
        record["programas"].sort(key=lambda program: (program["start"], program["stop"], program["title"]))
    return channels, parsed_programs, skipped_programs


def refresh_gatotv_xml(days: int, delay: float, max_channels: int) -> Path:
    """Run the supplied GatoTV scraper; it does not request iptv-epg.org feeds."""
    if not GATOTV_SCRIPT.is_file():
        raise FileNotFoundError(f"No se encontro el scraper de GatoTV: {GATOTV_SCRIPT}")

    GATOTV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # This dedicated output directory belongs to this updater. Remove prior XMLs
    # so GatoTV's unifier cannot mix old dated exports with today's refresh.
    for old_xml in GATOTV_OUTPUT_DIR.glob("*.xml"):
        old_xml.unlink()

    command = [
        sys.executable, str(GATOTV_SCRIPT),
        "--days", str(days),
        "--delay", str(delay),
        "--output-dir", str(GATOTV_OUTPUT_DIR),
    ]
    if max_channels > 0:
        command.extend(["--max-channels", str(max_channels)])
    print("Descargando/actualizando XML desde GatoTV (sin consultar las URLs de epg.py)...", flush=True)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(command, check=True, cwd=EPG_DIR, env=child_env)

    xml_path = GATOTV_OUTPUT_DIR / "epg.xml"
    if not xml_path.is_file() or xml_path.stat().st_size == 0:
        raise RuntimeError("gatotv.py termino sin producir un epg.xml valido.")
    return xml_path


def latest_existing_json() -> Path | None:
    fixed_path = EPG_DIR / "epg.json"
    if fixed_path.is_file():
        return fixed_path
    versions = [
        path for path in EPG_DIR.glob("*.json")
        if VERSION_RE.fullmatch(path.stem)
    ]
    if versions:
        return max(versions, key=lambda path: path.name)
    return None


def save_gatotv_cache(channels: dict) -> None:
    cache_path = EPG_DIR / "gatotv.json"
    temporary_path = cache_path.with_suffix(".json.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(channels, file, ensure_ascii=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary_path, cache_path)


def merge_and_publish(gatotv_channels: dict, preview: bool = False) -> Path | None:
    """Merge GatoTV schedules into the current guide, preserving existing sources."""
    existing_path = latest_existing_json()
    if existing_path:
        with existing_path.open("r", encoding="utf-8") as file:
            combined = json.load(file)
        if not isinstance(combined, dict):
            raise ValueError(f"El JSON EPG base no tiene el formato esperado: {existing_path}")
        print(f"Guia base preservada: {existing_path.name}", flush=True)
    else:
        combined = {}
        print("No hay JSON EPG previo; se generara la guia solo con GatoTV.", flush=True)

    added_channels = 0
    added_programs = 0
    for channel_id, source in gatotv_channels.items():
        if channel_id not in combined:
            added_channels += 1
        # These IDs come from GatoTV, so replace their old schedule instead of
        # appending stale times to each refresh. Other EPG sources remain intact.
        combined[channel_id] = {
            "name": source.get("name") or channel_id,
            "logo": source.get("logo") or "",
            "country": source.get("country") or "",
            "shift": "",
            "programas": source.get("programas", []),
        }
        added_programs += len(combined[channel_id]["programas"])

    if preview:
        print(f"Vista previa: {len(gatotv_channels)} canales GatoTV, {sum(len(c['programas']) for c in gatotv_channels.values())} programas; no se publico JSON.")
        return None

    # Keep a source-specific cache so a later epg.py run can preserve GatoTV.
    save_gatotv_cache(gatotv_channels)
    output_path = EPG_DIR / "epg.json"
    temp_path = output_path.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(combined, file, ensure_ascii=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, output_path)

    total_programs = sum(len(channel.get("programas", [])) for channel in combined.values())
    print("\nEPG combinado generado correctamente")
    print(f"Canales totales: {len(combined)} (canales GatoTV nuevos: {added_channels})")
    print(f"Programas GatoTV añadidos: {added_programs}")
    print(f"Programas totales: {total_programs}")
    print("Zona horaria del JSON: El Salvador (UTC-6)")
    print(f"Archivo EPG: {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Actualiza GatoTV, convierte su XML a la hora de El Salvador y lo fusiona al EPG Patzi."
    )
    parser.add_argument("--xml", type=Path, help="Usa un XML ya generado; omite el scraping de gatotv.py.")
    parser.add_argument("--days", type=int, default=4, help="Días que descargara gatotv.py (1-14).")
    parser.add_argument("--delay", type=float, default=0.3, help="Pausa entre peticiones del scraper GatoTV.")
    parser.add_argument("--max-channels", type=int, default=0, help="Limite de canales por pais (0 = sin limite).")
    parser.add_argument("--preview", action="store_true", help="Parsea y valida el XML sin publicar JSON.")
    args = parser.parse_args()
    if not 1 <= args.days <= 14:
        parser.error("--days debe estar entre 1 y 14")
    if args.delay < 0:
        parser.error("--delay no puede ser negativo")

    xml_path = args.xml.resolve() if args.xml else refresh_gatotv_xml(args.days, args.delay, args.max_channels)
    if not xml_path.is_file():
        raise FileNotFoundError(f"No se encontro el XML de GatoTV: {xml_path}")
    print(f"\nConvirtiendo XML: {xml_path} ({xml_path.stat().st_size / 1024 / 1024:.1f} MB)", flush=True)
    channels, program_count, skipped_count = parse_gatotv_xml(xml_path)
    if not channels or not program_count:
        raise RuntimeError("El XML no contiene canales con programas y horarios validos; no se actualizo el JSON publicado.")
    print(f"Programas XML validos: {program_count}; omitidos por datos incompletos: {skipped_count}", flush=True)
    merge_and_publish(channels, preview=args.preview)


if __name__ == "__main__":
    main()
