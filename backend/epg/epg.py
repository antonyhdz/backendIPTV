import csv
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# Desfases horarios de los países respecto a UTC.
# Se utilizan únicamente cuando el XMLTV no incluye offset.
DESFASES_PAISES = {
    "SV": -6,
    "GT": -6,
    "HN": -6,
    "NI": -6,
    "CR": -6,
    "PA": -5,
    "MX": -6,
    "CO": -5,
    "EC": -5,
    "PE": -5,
    "CL": -3,
    "AR": -3,
    "UY": -3,
    "PY": -3,
    "BO": -4,
    "VE": -4,
    "DO": -4,
    "CU": -5,
    "US": -5,
    "ES": 1
}

# Zona horaria de El Salvador
ZONA_SV = timezone(timedelta(hours=-6))
CARPETA_EPG = Path(__file__).resolve().parent
TIMEOUT_DESCARGA = 30


def convertir_a_hora_sv(fecha_str, pais):
    """
    Convierte una fecha XMLTV a la hora de El Salvador.

    Si el XMLTV incluye offset, lo respeta.
    Si no incluye offset, utiliza el país de origen.

    Devuelve: YYYYMMDDHHMMSS
    """

    try:
        texto = str(fecha_str).strip()

        coincidencia = re.fullmatch(
            r"(\d{14})\s*([+-]\d{4})?",
            texto
        )

        if not coincidencia:
            return None

        fecha_base = datetime.strptime(
            coincidencia.group(1),
            "%Y%m%d%H%M%S"
        )

        offset_xml = coincidencia.group(2)

        if offset_xml:
            # El XMLTV declara explícitamente su zona horaria.
            fecha_origen = fecha_base.replace(
                tzinfo=datetime.strptime(
                    offset_xml, "%z"
                ).tzinfo
            )
        else:
            # Sin offset, se interpreta como hora local del país.
            offset_pais = DESFASES_PAISES.get(
                pais.upper(), -6
            )

            fecha_origen = fecha_base.replace(
                tzinfo=timezone(
                    timedelta(hours=offset_pais)
                )
            )

        # Convertir a El Salvador.
        fecha_sv = fecha_origen.astimezone(ZONA_SV)

        # Guardar sin información de zona horaria.
        return fecha_sv.strftime("%Y%m%d%H%M%S")

    except Exception as e:
        print(f"Error convirtiendo fecha {fecha_str}: {e}")
        return None


def incorporar_cache_gatotv(epg_data_dict):
    """Conserva los canales GatoTV al regenerar la guia de iptv-epg.org."""
    gatotv_path = CARPETA_EPG / "gatotv.json"
    if not gatotv_path.is_file():
        return 0

    try:
        with gatotv_path.open("r", encoding="utf-8") as file:
            gatotv_data = json.load(file)
        if not isinstance(gatotv_data, dict):
            print("⚠️ gatotv.json no tiene el formato esperado; se omite.", flush=True)
            return 0

        added_programs = 0
        for channel_id, source in gatotv_data.items():
            if not isinstance(source, dict):
                continue
            programs = source.get("programas", [])
            epg_data_dict[channel_id] = {
                "name": source.get("name") or channel_id,
                "logo": source.get("logo") or "",
                "country": source.get("country") or "",
                "shift": "",
                "programas": programs if isinstance(programs, list) else [],
            }
            added_programs += len(epg_data_dict[channel_id]["programas"])
        if added_programs:
            print(f"📺 Se conservaron {added_programs} programas de GatoTV.", flush=True)
        return added_programs
    except (OSError, json.JSONDecodeError) as exc:
        print(f"⚠️ No se pudo leer gatotv.json: {exc}", flush=True)
        return 0


def actualizar_cache_json():
    epg_csv_path = CARPETA_EPG / "ipEPG.csv"
    cache_json_path = CARPETA_EPG / "epg.json"

    if not epg_csv_path.is_file():
        print("❌ No se encontró el archivo ipEPG.csv")
        print(f"   Se buscó en: {epg_csv_path}")
        return

    print("🔄 Iniciando descarga de EPG...", flush=True)
    print("🕒 Todos los horarios se convertirán a El Salvador (UTC-6)", flush=True)

    epg_data_dict = {}
    urls_procesadas = 0
    urls_fallidas = 0

    with open(
        epg_csv_path,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if row.get("mostrar", "0").strip() != "1":
                continue

            url = row.get("url", "").strip()
            pais = row.get("pais", "").strip().upper()

            if not url:
                continue

            print(f"\n📥 Descargando EPG [{pais}]: {url}", flush=True)

            try:

                programas_procesados = 0
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=TIMEOUT_DESCARGA) as response:
                    xml_content = response.read()

                root = ET.fromstring(xml_content)
                # Extraer canales, logos y nombres.
                for channel in root.findall("channel"):
                    ch_id = channel.get("id", "").strip()
                    if not ch_id:
                        continue
                    if ch_id not in epg_data_dict:
                        epg_data_dict[ch_id] = {
                            "logo": "", "country": pais, "shift": "", "programas": []
                        }
                    entry = epg_data_dict[ch_id]
                    icon = channel.find("icon")
                    if icon is not None and icon.get("src"):
                        entry["logo"] = icon.get("src").strip()
                    display_names = [
                        (node.text or "").strip()
                        for node in channel.findall("display-name")
                        if (node.text or "").strip()
                    ]
                    if display_names and not entry.get("name"):
                        entry["name"] = display_names[0]

                # Convertir horarios de cada programa a la hora de El Salvador.
                for programme in root.findall("programme"):
                    channel_id = programme.get("channel", "").strip()
                    if not channel_id:
                        continue
                    start_sv = convertir_a_hora_sv(
                        programme.get("start", "").strip(), pais
                    )
                    stop_sv = convertir_a_hora_sv(
                        programme.get("stop", "").strip(), pais
                    )
                    if not start_sv or not stop_sv:
                        continue
                    title_elem = programme.find("title")
                    title = (
                        title_elem.text
                        if title_elem is not None and title_elem.text
                        else "Sin Título"
                    )
                    entry = epg_data_dict.setdefault(channel_id, {
                        "logo": "", "country": pais, "shift": "", "programas": []
                    })
                    entry["programas"].append({
                        "title": title,
                        "start": start_sv,
                        "stop": stop_sv,
                    })
                    programas_procesados += 1
                urls_procesadas += 1

                print(
                    f"  ✅ {pais}: "
                    f"{programas_procesados} programas procesados",
                    flush=True,
                )

            except Exception as e:
                urls_fallidas += 1

                print(
                    f"  ⚠️ Error al descargar EPG de "
                    f"{pais}: {type(e).__name__}: {e}",
                    flush=True,
                )

    # Add the independently refreshed GatoTV feed without dropping it when
    # this primary iptv-epg.org updater publishes the next epg.json.
    incorporar_cache_gatotv(epg_data_dict)

    # 3. Guardar el JSON con horarios de El Salvador.
    try:

        temporary_path = cache_json_path.with_suffix(".json.tmp")
        with temporary_path.open("w", encoding="utf-8") as f:
            json.dump(epg_data_dict, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, cache_json_path)

        total_canales = len(epg_data_dict)

        total_programas = sum(
            len(c["programas"])
            for c in epg_data_dict.values()
        )

        print("\n===================================")
        print("✅ CACHÉ EPG GENERADA CORRECTAMENTE")
        print("===================================")
        print(f"Canales: {total_canales}")
        print(f"Programas: {total_programas}")
        print(f"URLs procesadas: {urls_procesadas}")
        print(f"URLs fallidas: {urls_fallidas}")
        print("Zona horaria: El Salvador (UTC-6)")
        print(f"Archivo: {cache_json_path}")

    except Exception as e:

        print(f"❌ Error guardando JSON: {e}")


if __name__ == "__main__":
    actualizar_cache_json()
