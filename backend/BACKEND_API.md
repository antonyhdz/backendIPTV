# Contrato del backend para la app cliente

Esta guía describe las rutas actuales de la API y el flujo previsto para la futura app móvil. Se deriva de las rutas FastAPI del proyecto; si una ruta cambia, actualizar esta guía junto con el código.

## Direcciones y formato

- Prefijo REST: `/api/v1`.
- Desarrollo en la computadora: `http://127.0.0.1:8000/api/v1`.
- Dispositivos en la LAN: `http://IP_DEL_SERVIDOR:8000/api/v1`.
- Producción: usar HTTPS. WebSocket usa `wss://` cuando la API está bajo HTTPS.
- Las respuestas REST usan JSON excepto la carga multipart de archivos M3U.
- La app nativa debe leer la URL base desde configuración por entorno; no asumir una IP local fija.

## Autenticación y autorización

Las rutas REST protegidas reciben el JWT de acceso en:

```http
Authorization: Bearer <access_token>
```

El administrador usa un token de acceso corto, de 30 minutos por defecto. Los clientes usan el mismo JWT corto, vinculado a una sesión de dispositivo persistente. El servidor comprueba que la sesión vinculada siga activa en cada solicitud protegida.

Roles:

- `admin`: gestiona clientes, paquetes y canales.
- `customer`: consulta su cuenta y canales habilitados, solo con una suscripción vigente.

La base de datos SQLite contiene `users`, `packages`, `subscriptions`, `channels` y `auth_sessions`. `auth_sessions` almacena un hash SHA-256 del refresh token, el ID aleatorio de instalación, vencimiento y revocación; nunca guarda el refresh token en claro. Un índice parcial impide más de una sesión no revocada por usuario. Las comprobaciones de `is_active`, sesión y vencimiento se hacen contra la base, por lo que revocar una sesión invalida los JWT de cliente que dependan de ella.

Errores comunes:

| Código | Significado | Acción sugerida para la app |
| --- | --- | --- |
| `401` | JWT ausente, inválido, vencido o sesión revocada | Si hay refresh token, intentar una sola renovación; si falla, borrar tokens y pedir login. |
| `403` | Rol sin permiso o cliente sin suscripción vigente | Mostrar el mensaje de acceso/suscripción; no repetir automáticamente. |
| `404` | Recurso inexistente o no visible para el rol | Mostrar que el recurso no está disponible. |
| `409` | Usuario/paquete duplicado o login desde otro dispositivo | Mostrar el mensaje de la API. |
| `422` | Datos inválidos o falta `device_id` al autenticar un cliente | Corregir el cuerpo de la solicitud. |

Los errores FastAPI tienen normalmente la forma:

```json
{"detail":"Descripción del error"}
```

## Identidad de instalación y límite de dispositivo

La app genera un identificador aleatorio una sola vez por instalación. Recomendación: 32 bytes aleatorios codificados Base64 URL-safe sin padding (43 caracteres). El identificador debe tener entre 32 y 128 caracteres y usar letras, números, `_` o `-`.

Guardar `device_id` en almacenamiento seguro/persistente del sistema junto al ciclo de vida de la instalación. No pedir IMEI, número telefónico, ubicación ni permisos; el ID distingue una instalación, no revela modelo de hardware ni identidad física. Si se borra el almacenamiento de la app o se reinstala, puede generarse uno nuevo.

Una cuenta cliente admite una sesión persistente no revocada:

- Primer login desde un dispositivo: crea la sesión.
- Login de nuevo con el mismo `device_id`: renueva las credenciales de esa sesión.
- Login con otro `device_id`: responde `409`; el dispositivo anterior debe cerrar sesión o un administrador debe liberar el dispositivo.
- Cerrar sesión o desactivar la cuenta revoca la sesión y sus JWT vinculados.
- Un administrador puede liberar la sesión cuando el dispositivo se pierde.

## Flujo de login, renovación y logout

### 1. Login

`POST /api/v1/auth/login`

Cliente:

```json
{
  "username": "cliente01",
  "password": "contraseña",
  "device_id": "identificador-aleatorio-persistente-de-la-instalacion"
}
```

`device_id` es requerido para clientes. Para el administrador se envían `username` y `password`; no se crea una sesión persistente.

Respuesta de cliente (los tokens son secretos y el ejemplo es ilustrativo):

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 1800,
  "refresh_token": "<secreto-opaco>",
  "refresh_expires_in": 2592000,
  "user": {
    "id": 24,
    "username": "cliente01",
    "role": "customer",
    "is_active": true
  }
}
```

`PATZI_TOKEN_MINUTES` controla la duración del JWT (30 minutos por defecto). `PATZI_CLIENT_SESSION_DAYS` controla la duración absoluta de la sesión (30 días por defecto, máximo 30). El vencimiento del refresh token no se extiende al renovarlo.

El monitor automático de canales usa `PATZI_CHANNEL_CHECK_INTERVAL_SECONDS` (300 por defecto), `PATZI_CHANNEL_CHECK_BATCH_SIZE` (20) y `PATZI_CHANNEL_CHECK_BATCH_DELAY_SECONDS` (5). Comprueba solo alcance/estado, no vuelve a descargar metadatos en cada ciclo. Los chequeos manuales conservan la actualización de metadatos. Un ciclo lento no se solapa consigo mismo y espera un intervalo completo antes de volver a empezar.

Los respaldos se guardan en `data/backups` por defecto. `PATZI_BACKUP_INTERVAL_HOURS` define el intervalo inicial (24 horas) y `PATZI_BACKUP_RETENTION_COUNT` la retención inicial (7); el administrador puede cambiar ambos valores desde `/backups`. La política se persiste en `data/backups/backup-policy.json`. Se crea una copia inicial cuando aún no hay respaldos. Restaurar genera antes una copia `pre-restore` del estado actual.

Respuesta del administrador: mismos campos de acceso/usuario; `refresh_token` y `refresh_expires_in` son `null`.

### 2. Guardado

- Access token: memoria de la app si es posible.
- Refresh token y `device_id`: almacenamiento protegido del sistema, por ejemplo Android Keystore o iOS Keychain.
- No imprimir tokens en logs, analítica, reportes de fallos ni URLs.

### 3. Renovación

Cuando una llamada protegida devuelve `401`, renovar una sola vez y reintentar la llamada original una vez:

`POST /api/v1/auth/refresh`

```json
{"refresh_token":"<refresh-token-actual>"}
```

La respuesta tiene el formato del login de cliente. La API rota el refresh token: reemplazar el token guardado por el nuevo. Solicitudes simultáneas con el mismo refresh token pueden competir; la app debe coordinar una sola renovación a la vez (single-flight).

Si refresh devuelve `401`, borrar access token y refresh token, cerrar el WebSocket y pedir credenciales otra vez. No entrar en un ciclo de renovación/reintento.

### 4. Logout

`POST /api/v1/auth/logout`

```json
{"refresh_token":"<refresh-token-actual>"}
```

La API revoca la sesión, notifica al WebSocket y devuelve `{"detail":"Sesión cerrada."}`. La app debe cerrar la conexión, borrar tokens y el `device_id` solo si el usuario eligió borrar/olvidar ese dispositivo. Si conserva el `device_id`, podrá autenticar otra vez en la misma instalación.

## WebSocket para avisos de sesión

Ruta: `ws://HOST/api/v1/auth/events` en desarrollo; `wss://HOST/api/v1/auth/events` bajo HTTPS.

Protocolo:

1. Abrir el WebSocket y enviar en los primeros 10 segundos:

   ```json
   {"type":"auth","access_token":"<jwt-de-acceso>"}
   ```

2. Esperar `{"type":"ready"}`. Código de cierre `4401` indica autenticación/sesión no válida; `4403` indica sesión revocada; `4408` indica timeout de autenticación.
3. El servidor envía `{"type":"ping"}` aproximadamente cada 20 segundos. Responder `{"type":"pong"}` para mantener el ciclo de vida de la app/red.
4. Al recibir:

   ```json
   {"type":"session_revoked","reason":"admin_revoked"}
   ```

   cerrar el socket, borrar credenciales locales y mostrar que la sesión fue cerrada. Razones actuales: `admin_revoked`, `account_disabled`, `logout` y `session_inactive`.
5. Si la red se corta, reconectar con backoff exponencial con límite. Si el JWT ya venció, renovar primero y autenticar el socket con el JWT nuevo.

El WebSocket permite aviso inmediato mientras el proceso de la app y el servidor permanecen conectados. Android/iOS pueden suspender la app y cortar el socket en segundo plano; notificaciones inmediatas durante la suspensión requieren integrar APNs/FCM. El hub actual vive en memoria del proceso: para varios workers/procesos, usar un bus compartido (por ejemplo Redis Pub/Sub). Aun así, cada llamada REST comprueba revocación y el socket vuelve a verificar el estado al menos cada 20 segundos.

## API disponible para clientes

### Cuenta

`GET /api/v1/account` — requiere JWT. Devuelve:

```json
{
  "id": 24,
  "username": "cliente01",
  "role": "customer",
  "is_active": true,
  "created_at": "2026-11-01T12:00:00",
  "subscription": {
    "id": 8,
    "package": "Mensual",
    "starts_at": "2026-11-01T12:00:00",
    "expires_at": "2026-12-01T12:00:00",
    "active": true
  }
}
```

`subscription` puede ser `null`. La fecha de suscripción es distinta del límite de sesión.

### Canales

`GET /api/v1/channels` — requiere JWT y suscripción vigente para el cliente. El admin ve todos los canales; el cliente solo los habilitados y asignados al paquete de su suscripción vigente. Respuesta:

```json
{
  "total": 1,
  "live": 1,
  "disabled": 0,
  "channels": [{
    "id": 12,
    "name": "Canal ejemplo",
    "category": "General",
    "logo_url": "https://cdn.example/logo.png",
    "stream_url": "https://stream.example/live.m3u8",
    "enabled": true,
    "is_live": true,
    "uptime": "00h 23m 10s",
    "last_checked": "2026-11-01T12:00:00",
    "stream_info": {
      "format": "HLS",
      "resolution": "1280x720",
      "video_codec": "avc1",
      "audio_codec": "mp4a.40.2",
      "bitrate_kbps": 1700,
      "fps": 30
    }
  }]
}
```

`stream_info` y sus propiedades pueden ser `null`/ausentes cuando el proveedor no publica metadatos accesibles. `stream_url` puede incluir credenciales o parámetros privados: no exponerla en logs ni telemetría. El backend administra y verifica las fuentes; la app cliente reproduce el `stream_url` con LibVLC en modo de decodificación por software.

El campo `is_live` proviene de una comprobación administrativa guardada. Para HTTP(S), el comprobador intenta `HEAD` y considera disponible una respuesta menor que 500; como alternativa puede comprobar solo la conexión TCP. Esto no garantiza que el video se pueda decodificar, que las credenciales sigan vigentes o que el stream continúe funcionando ahora mismo.

## API administrativa actual

Todas estas rutas REST requieren JWT de administrador (`role=admin`). Los importes se expresan en centavos.

| Método | Ruta | Uso |
| --- | --- | --- |
| `GET` | `/api/v1/admin/overview` | Conteos para el dashboard. |
| `GET` | `/api/v1/admin/packages` | Lista paquetes e incluye `channel_ids` y los canales asignados. |
| `POST` | `/api/v1/admin/packages` | Crea paquete: `name`, `duration_days`, `price_cents`, `active`; `channel_ids` es opcional y puede configurarse después. |
| `PATCH` | `/api/v1/admin/packages/{id}` | Reemplaza la lista `channel_ids` (al menos un canal); afecta de inmediato a clientes con ese paquete vigente. |
| `GET` | `/api/v1/admin/customers` | Lista clientes, suscripción y sesión/dispositivo activo. |
| `POST` | `/api/v1/admin/customers` | Crea cliente: `username`, `password` (mínimo 8), `package_id` opcional. |
| `PATCH` | `/api/v1/admin/customers/{id}` | Edita `username`, `password` opcional y/o `package_id`. La contraseña nueva revoca la sesión del cliente. Un `package_id` nuevo reemplaza la suscripción vigente y todos los períodos programados de inmediato; la vigencia comienza al guardar. `package_id: null` cancela períodos y quita el acceso de inmediato. |
| `PATCH` | `/api/v1/admin/customers/{id}/status` | Bloquea/activa: `{"is_active":false}`. Bloquear revoca y notifica sesiones. |
| `POST` | `/api/v1/admin/customers/{id}/revoke-session` | Libera dispositivo perdido y notifica el WebSocket. |
| `POST` | `/api/v1/admin/customers/{id}/renew/{package_id}` | Renueva/programa la suscripción. |
| `GET` | `/api/v1/admin/backups` | Lista respaldos y muestra política de retención e intervalo. |
| `PUT` | `/api/v1/admin/backups/policy` | Configura `interval_hours` (1–8760) y `retention_count` (1–100) desde el panel. |
| `POST` | `/api/v1/admin/backups` | Crea un respaldo manual de SQLite. |
| `GET` | `/api/v1/admin/backups/{filename}/download` | Descarga un respaldo. |
| `POST` | `/api/v1/admin/backups/{filename}/restore` | Crea primero una copia previa y restaura el respaldo elegido. |
| `DELETE` | `/api/v1/admin/backups/{filename}` | Elimina un respaldo; siempre debe quedar al menos uno. |
| `GET` | `/api/v1/channels` | Para administrador devuelve todos los canales; para cliente, solo los habilitados y asignados a su paquete vigente. |
| `POST` | `/api/v1/admin/channels` | Crea canal. Campos: `name`, `category`, `stream_url`, `logo_url`, `enabled`. |
| `PUT` | `/api/v1/admin/channels/{id}` | Reemplaza los datos editables del canal. |
| `PATCH` | `/api/v1/admin/channels/{id}/status` | Actualiza `enabled`. |
| `DELETE` | `/api/v1/admin/channels/{id}` | Elimina canal. |
| `POST` | `/api/v1/admin/channels/batch-delete` | `{"ids":[1,2]}`. |
| `POST` | `/api/v1/admin/channels/batch-status` | `{"ids":[1,2],"enabled":true}`. |
| `GET` | `/api/v1/admin/channels/{id}/check` | Comprueba un stream y persiste estado/metadatos observados. |
| `POST` | `/api/v1/admin/channels/check-all` | Comprueba canales habilitados. |
| `POST` | `/api/v1/admin/channels/import-m3u` | Sube archivo multipart `file`; agrega canales y completa logos faltantes por URL de stream. |

Los paquetes tienen `duration_days` (1–3650), `price_cents` (0–100,000,000), nombre único y una lista de canales. Los canales admiten URLs `http`, `https`, `rtmp` y `rtsp`; los logos admiten `http` y `https`. La relación paquete-canal se guarda en `package_channels` y la app cliente debe tratar `/api/v1/channels` como el catálogo autorizado para esa cuenta.

## Estado y metadatos del sistema

- `GET /api/v1/health` → `{"status":"ok"}`.
- `GET /api/v1/status` → nombre, versión, estado y ruta `/docs`.
- `GET /docs` → documentación OpenAPI interactiva de FastAPI.

## Capacidades que la app aún debe implementar

- UI/login y recuperación de errores `409` (otro dispositivo) y `401` (sesión revocada).
- Generación/persistencia segura de `device_id`, refresh token y single-flight de renovación.
- WebSocket, heartbeat, reconexión y cierre local al recibir revocación.
- Reproductor nativo compatible con los protocolos/codecs de las fuentes.
- Pantalla de cuenta/suscripción y estados vencidos.
- Si se requiere compra/renovación por el cliente, catálogo de paquetes, pagos y endpoints de suscripción para clientes; actualmente la renovación es administrativa.
- Notificaciones push para avisos cuando el sistema suspenda la app.

## Reglas para cambios futuros

- Mantener los prefijos/versiones de API y actualizar esta guía si cambia un campo, permiso, expiración o evento.
- No enviar hashes de refresh token al cliente; solo el secreto opaco emitido al login/refresh.
- Cambios de contraseña futuros deben revocar todas las sesiones del cliente y emitir el evento WebSocket.
- Probar en HTTPS y con redes interrumpidas antes de publicar la app.
