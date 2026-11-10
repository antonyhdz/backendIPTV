# Patzi IPTV

Panel administrativo de IPTV basado en FastAPI, SQLAlchemy y SQLite. El proyecto puede ejecutarse en Windows o Ubuntu; cada sistema necesita su propio entorno virtual.

## Requisitos

- Python 3.10 o posterior.
- Git, si vas a clonar el proyecto.

## Configuración inicial

En la carpeta del proyecto, copia `.env.example` como `.env` y cambia la contraseña y el secreto JWT. El secreto debe tener al menos 32 caracteres y ser privado.

En PowerShell puedes generar un secreto así:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

En Ubuntu:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Pega el valor generado en `PATZI_JWT_SECRET` dentro de `.env`.

## Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
```

Edita `.env` antes de iniciar y luego ejecuta:

```powershell
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Abre `http://127.0.0.1:8000`. Desde otro equipo de tu red local, usa la IP LAN de esta computadora, por ejemplo `http://192.168.1.90:8000`.

## Ubuntu

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cp .env.example .env
```

Edita `.env` y ejecuta:

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Abre `http://127.0.0.1:8000` o `http://IP_DE_UBUNTU:8000` desde la red local.

## Mover los datos de Windows a Ubuntu

1. Detén el servidor en Windows con `Ctrl+C`.
2. Copia `data/patzi.db` a la carpeta `data/` del proyecto en Ubuntu. Esa base contiene canales, usuarios y suscripciones.
3. Copia `.env` de forma privada o crea uno nuevo en Ubuntu con el mismo `PATZI_JWT_SECRET`. No lo publiques ni lo subas a Git.
4. En Ubuntu, crea un entorno virtual nuevo e instala `requirements.txt` siguiendo los pasos anteriores.
5. Inicia el servidor en Ubuntu. No copies `.venv` ni `venv`: los entornos virtuales son específicos de cada sistema operativo.

La ruta de SQLite se construye desde la ubicación del proyecto y `data/patzi.db`, por lo que no depende de una ruta fija de Windows.

### Subir una actualización desde Windows

El script `deploy-ubuntu.ps1` empaqueta y sube el código y sus recursos por SSH/SCP al Ubuntu Server `anton@servidorpc:~/iptv_server`. Omite `.env`, `data/`, entornos virtuales y cachés; al extraer conserva la base de datos y la configuración existentes en el servidor. Requiere OpenSSH Client en Windows y acceso SSH al servidor por el puerto 22.

Configura una llave una sola vez (pedirá la contraseña de `anton` durante esta configuración):

```powershell
.\setup-ubuntu-ssh-key.ps1
```

La llave privada queda en `%USERPROFILE%\.ssh\patzi_ubuntu`. Se crea sin frase de paso para evitar solicitudes interactivas; mantén protegida tu cuenta de Windows y no compartas ese archivo. El script de despliegue detecta y usa esa llave automáticamente.

Desde PowerShell, en la carpeta del proyecto:

```powershell
.\deploy-ubuntu.ps1
```

Si el servidor usa otro host, usuario, puerto o carpeta, puedes cambiar los valores con `-HostName`, `-UserName`, `-Port` y `-RemotePath` en ambos scripts. También puedes indicar otra llave al despliegue con `-IdentityFile`. Después de subir, instala o actualiza dependencias en el servidor y reinicia el backend. Para la primera migración, copia `data/patzi.db` por separado y configura `.env` manualmente en el servidor; el script no reemplaza esos archivos.

## Rutas útiles

- Panel: `/`
- Canales: `/channels`
- Respaldos: `/backups` (copias automáticas y manuales, descarga y restauración)
- Documentación API: `/docs`
- Contrato del backend para la futura app: [`backend/BACKEND_API.md`](backend/BACKEND_API.md)
- Estado: `/api/v1/health`

## Actualización de la guía EPG

Al terminar correctamente, `epg/epg.py` publica la guía en `epg/epg.json` mediante escritura atómica. El backend y la API leen ese archivo fijo. Para que la app y el panel detecten cambios sin nombres de archivo versionados, `GET /api/v1/client/epg/version` devuelve la fecha de modificación del archivo como token y `GET /api/v1/client/epg/{version}.json` entrega el contenido actual de `epg.json`; ambos requieren autenticación.

La app consulta la versión al actualizar TV en vivo y vuelve a comprobarla cada minuto mientras esa pestaña está abierta. El panel de canales la revisa cada 30 segundos mientras la página está visible, además de hacerlo al volver a ella o recuperar conexión. Cada instalación guarda su propia versión y solo la cambia cuando terminó de descargar el JSON. Por eso, los clientes que estuvieron desconectados detectan la guía nueva al reconectarse; no hace falta reiniciar el backend.

Los respaldos SQLite se guardan en `data/backups`. El panel conserva siete copias por defecto y crea una copia automática cada 24 horas; ambas opciones se pueden cambiar desde **Respaldo**. Descarga una copia fuera del equipo con regularidad. Las copias contienen información de clientes y hashes de contraseñas, así que guárdalas en un lugar privado.

### Fuente adicional GatoTV

`python epg/epg2.py` ejecuta `epg/gatotv.py` para actualizar el XML de GatoTV (por defecto, cuatro días de programación) y fusiona sus canales y programas con `epg/epg.json`. No consulta las URLs de `ipEPG.csv` usadas por `epg.py`. El scraper siempre descarga por defecto aunque encuentre XML antiguos; al unificar, usa solo los XML de esa ejecución, así no conserva catálogos desactualizados ni pierde canales por una carpeta con nombres antiguos. `--reuse-existing` solicita explícitamente unificar sin descargar; `--country venezuela` permite actualizar un país específico. La salida conserva el esquema JSON existente y guarda la fuente de GatoTV en `epg/gatotv.json`; `epg.py` vuelve a incorporar esa caché al publicar su actualización, sin acumular horarios obsoletos.

GatoTV declara los horarios XMLTV con `-0500` porque su scraper fija `DEFAULT_TZ` a UTC-5. `epg2.py` respeta el offset incluido por cada programa y lo convierte a UTC-6, hora de El Salvador; si un registro no tiene offset, usa el UTC-5 declarado por el scraper. Para validar un XML ya generado sin descargar ni publicar una guía: `python epg/epg2.py --xml RUTA\A\epg.xml --preview`.

## Sesiones de clientes para la app

- `POST /api/v1/auth/login` devuelve un JWT de acceso (30 minutos por defecto). Para cuentas cliente también devuelve un `refresh_token`, válido hasta 30 días por defecto.
- La app genera un identificador aleatorio de instalación una sola vez, lo conserva en almacenamiento seguro y lo manda como `device_id` junto al usuario y la contraseña. No solicita IMEI, número de teléfono ni permisos del dispositivo.
- Cada cliente puede tener una sola sesión activa. Un inicio desde una instalación distinta recibe HTTP `409`; el inicio desde la misma instalación puede renovar sus credenciales.
- La app debe guardar el refresh token en almacenamiento seguro del sistema (Android Keystore/iOS Keychain), no en preferencias o texto plano.
- Cuando la API responda `401` por vencimiento del JWT, la app envía el refresh token a `POST /api/v1/auth/refresh`. La API rota el refresh token y devuelve un JWT nuevo. La fecha límite original no se extiende; después de 30 días el cliente vuelve a iniciar sesión.
- Al cerrar sesión, la app llama `POST /api/v1/auth/logout` con el refresh token y elimina localmente ambos tokens.
- Desactivar una cuenta cliente revoca las sesiones persistentes. El vencimiento de la suscripción sigue siendo independiente.
- Un administrador puede liberar la sesión de un dispositivo perdido con `POST /api/v1/admin/customers/{customer_id}/revoke-session`.
- Para avisos inmediatos mientras la app está conectada, abre `ws://HOST/api/v1/auth/events` (usa `wss://` bajo HTTPS), envía `{"type":"auth","access_token":"<JWT>"}` y espera `{"type":"ready"}`. Responde a cada `{"type":"ping"}` con `{"type":"pong"}`. Al recibir `{"type":"session_revoked","reason":"..."}`, borra los tokens y vuelve al login.
- El administrador continúa usando únicamente el JWT corto; no recibe refresh token.

Los días configurables están limitados a un máximo de 30 mediante `PATZI_CLIENT_SESSION_DAYS`. La app móvil todavía debe implementar el almacenamiento seguro y el ciclo de renovación.

El WebSocket avisa al instante mientras el proceso que mantiene la conexión está activo. Si el sistema operativo suspende la app en segundo plano, el socket puede cortarse; para avisos aun con la app suspendida se necesitarían notificaciones push. El hub actual es local al proceso, así que para ejecutar varios workers se deberá añadir un bus compartido como Redis.


