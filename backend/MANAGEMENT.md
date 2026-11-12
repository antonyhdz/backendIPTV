# Clientes, canales, paquetes y dashboard

Esta guía cubre la administración del catálogo y los clientes, la asignación de paquetes con vencimiento y las lecturas del dashboard. Todas las rutas de esta guía requieren un JWT válido con rol `admin`.

## Convenciones

- API base: `https://<dominio-publico-railway>/api/v1`.
- Fechas: ISO 8601 con zona horaria. Las respuestas guardan y devuelven fechas UTC; por ejemplo `2026-11-11T18:30:00Z`.
- IDs: strings que representan el `_id` ObjectId de MongoDB.
- Listados: `{ "items": [...], "pagination": { "page", "page_size", "total", "pages" } }`.
- No se borran físicamente clientes, canales ni paquetes: usa `is_active=false` para archivarlos y conservar referencias históricas.
- Envía `Authorization: Bearer <access_token>` y `Content-Type: application/json`.

## Clientes

### `POST /api/v1/admin/customers`

Crea un cliente. Body:

```json
{
  "name": "Nombre del cliente",
  "username": "cliente01",
  "password": "Una-clave-larga-y-unica",
  "email": "cliente@example.com",
  "phone": "+503 7000 0000",
  "notes": "Información opcional"
}
```

`name`, `username` y `password` son obligatorios. El username debe tener 3–80 caracteres (`a-z`, números, punto, guion o guion bajo); la contraseña debe tener 12–128 caracteres. Se almacena un hash Argon2, nunca la contraseña. Email es opcional y único si se proporciona. Cada cuenta inicia con `max_devices=1` y sin dispositivos asociados. Responde `201` con los datos del cliente, límite y dispositivos (nunca devuelve el hash). Responde `409` si el username o email ya está asignado.

### `GET /api/v1/admin/customers`

Parámetros opcionales: `page` (1), `page_size` (25, máximo 100), `search` (busca nombre, username, email o teléfono) y `active` (`true`/`false`). Devuelve elementos paginados.

### `PATCH /api/v1/admin/customers/{customer_id}`

Actualiza `name`, `username`, `email`, `phone`, `notes`, `is_active` y/o `max_devices`. Envía solo los campos a cambiar. `max_devices` acepta del 1 al 4. Por ejemplo, para permitir hasta tres dispositivos: `{ "max_devices": 3 }`. Si se baja el límite por debajo del número ya asociado, la API responde `409`; primero revoca dispositivos. Para archivar: `{ "is_active": false }`. Puedes enviar `""` para limpiar un campo opcional de texto; `name` no puede quedar vacío. Cambiar username o email duplicados también responde `409`.

### Restablecer contraseña

`PUT /api/v1/admin/customers/{customer_id}/password` con `{"password":"Nueva-clave-larga-y-unica"}`. Exige contraseña de 12–128 caracteres. Al cambiarla, el backend revoca todos los UUID asociados; el cliente debe iniciar sesión de nuevo y volver a asociar el dispositivo dentro del límite.

### Dispositivos asociados y revocación

El cliente listado incluye `max_devices` y `devices`, que contiene el UUID, nombre opcional, fecha de asociación y último uso. Revoca un dispositivo con `DELETE /api/v1/admin/customers/{customer_id}/devices/{device_uuid}`. La revocación invalida inmediatamente los tokens emitidos para ese dispositivo.

El límite se configura por cuenta. Cada móvil presenta un UUID al autenticarse; los UUID nuevos se asocian si hay espacio. Un UUID ya asociado puede volver a iniciar sesión sin ocupar otra plaza. El panel puede establecer cualquier límite de 1 a 4 por cliente.

## Login de la aplicación móvil de clientes

El login de clientes es independiente del login de administradores (`/api/v1/auth/login`). La app de clientes usa estas rutas y recibe tokens de rol `customer`; esos tokens no autorizan las rutas del panel.

### `POST /api/v1/mobile/auth/login`

Body JSON:

```json
{
  "username": "cliente01",
  "password": "Una-clave-larga-y-unica",
  "device_uuid": "b2fb766d-7fb5-4e3d-93e1-663aa52b6201",
  "device_name": "Android de Juan"
}
```

`device_uuid` debe ser un UUID válido. La app móvil debe generar uno por instalación/dispositivo y persistirlo para reutilizarlo en futuros inicios de sesión; no generar uno nuevo en cada login. El UUID solo puede asociarse a una cuenta a la vez. La respuesta `200` incluye `access_token`, `user`, `device_uuid`, `registered_devices` y `max_devices`. Los intentos fallidos se limitan por username e IP (8 fallos bloquean 10 minutos). Credenciales inválidas dan `401`; una cuenta sin plazas para un UUID nuevo da `409` con `detail: "device_limit_reached"`; si el UUID ya pertenece a otro cliente, da `409` con `detail: "device_already_linked"`.

### Llamadas móviles autenticadas

En las llamadas siguientes, envía `Authorization: Bearer <access_token>`:

| Método | Ruta | Resultado |
| --- | --- | --- |
| `GET` | `/api/v1/mobile/me` | Perfil resumido del cliente y cantidad de suscripciones activas. |
| `GET` | `/api/v1/mobile/me/packages` | Todos los paquetes con suscripciones vigentes del cliente y fechas de vencimiento. |
| `GET` | `/api/v1/mobile/me/channels` | Canales activos combinados de todos sus paquetes vigentes, sin duplicados. |

Cada petición verifica que el token corresponda a un UUID todavía asociado y que la cuenta siga activa. Si el panel revoca el dispositivo, el token deja de funcionar inmediatamente. Los paquetes se consideran vigentes cuando `starts_at <= ahora < expires_at`.

En FlutterFlow, define una API Call móvil `MobileLogin` con método POST, URL `/api/v1/mobile/auth/login`, `Content-Type: application/json` y variables `username`, `password`, `device_uuid` y `device_name`. En la app móvil, asigna username/password desde el formulario y el UUID persistido; no uses las constantes compartidas con el panel para las credenciales del cliente. Guarda `access_token` de forma persistente y pásalo como Bearer a las llamadas móviles autenticadas.

El UUID sirve como identificador de control de cupos. Por sí solo no demuestra que el dispositivo sea hardware genuino: quien pueda copiar un UUID podría imitarlo, así que la app debe proteger el almacenamiento y no exponerlo en logs o enlaces.

## Canales

### `POST /api/v1/admin/channels`

Body: `name` y `stream_url` obligatorios; `category` y `logo_url` opcionales.

```json
{
  "name": "Canal de noticias",
  "stream_url": "https://stream.example/live.m3u8",
  "category": "Noticias",
  "logo_url": "https://example.com/logo.png"
}
```

El nombre es único. Evita guardar URLs con credenciales privadas en el catálogo.

### `GET /api/v1/admin/channels`

Parámetros: `page` (1), `page_size` (100, máximo 200), `active` opcional. Devuelve canales ordenados por nombre y paginados.

### `PATCH /api/v1/admin/channels/{channel_id}`

Actualiza `name`, `stream_url`, `category`, `logo_url` y/o `is_active`. Para retirar un canal sin romper los paquetes existentes, usa `is_active=false`.

## Paquetes

### `POST /api/v1/admin/packages`

Body: `name`, `price` y `duration_days` obligatorios; `description` y `channel_ids` opcionales.

```json
{
  "name": "Mensual",
  "description": "Paquete básico",
  "price": 10.0,
  "duration_days": 30,
  "channel_ids": ["<channel_id>"]
}
```

Precio debe estar entre 0 y 1,000,000; duración entre 1 y 3650 días. Los canales indicados deben existir y estar activos. El nombre del paquete es único.

### `GET /api/v1/admin/packages`

Parámetros: `page` (1), `page_size` (100, máximo 200), `active` opcional.

### `PATCH /api/v1/admin/packages/{package_id}`

Actualiza `name`, `description`, `price`, `duration_days`, `channel_ids` y/o `is_active`. Las nuevas suscripciones copian duración y precio del paquete al momento de comprarse (la duración se aplica a la fecha final); actualizar un paquete no altera suscripciones ya emitidas.

## Suscripciones y vencimiento

### `POST /api/v1/admin/subscriptions`

Asigna un paquete existente y activo a un cliente existente y activo. Body:

```json
{
  "customer_id": "<customer_id>",
  "package_id": "<package_id>",
  "starts_at": "2026-11-11T18:30:00Z"
}
```

`starts_at` es opcional y por defecto usa el instante de creación. Debe incluir zona horaria. El backend calcula `expires_at = starts_at + duration_days` días usando la duración actual del paquete. Responde `201` con IDs, fechas y estado calculado (`active` o `expired`). No existe renovación automática; para extender el acceso crea otra suscripción con la fecha de inicio deseada.

### `GET /api/v1/admin/subscriptions`

Parámetros: `page` (1), `page_size` (25, máximo 100), `state` (`all`, `active`, `expired` o `expiring`; por defecto `all`) y `customer_id` opcional. `expiring` significa vencimiento dentro de los próximos 7 días. Incluye `customer_name` y `package_name` para mostrar en tablas. Usa `customer_id` para ver y administrar varios paquetes asignados a un solo cliente. Asigna otro paquete creando otra suscripción con `POST`; el cliente recibirá todos sus paquetes vigentes en la app móvil.

## Dashboard

### `GET /api/v1/admin/dashboard`

Devuelve datos agregados en una sola llamada, sin listas completas:

```json
{
  "generated_at": "2026-11-11T18:30:00Z",
  "customers": {"total": 20, "active": 18, "inactive": 2},
  "subscriptions": {"active": 12, "expired": 8, "expiring_in_7_days": 3},
  "catalog": {"active_channels": 45, "active_packages": 4},
  "expiring_soon": []
}
```

`expiring_soon` contiene hasta 10 suscripciones próximas a vencer, ordenadas por vencimiento, con `customer_name`, `package_name`, `starts_at`, `expires_at` y `status`. Para un listado completo con filtros y paginación, usa `GET /subscriptions?state=expiring`.

En FlutterFlow, configura una API Call GET a `/api/v1/admin/dashboard`, añade el header Authorization y asigna `Bearer <App State access_token>`. Los valores de tarjetas se leen de `customers.total`, `subscriptions.active`, `subscriptions.expiring_in_7_days`, etc. Define JSON Paths `$.customers.total`, `$.subscriptions.active`, `$.subscriptions.expiring_in_7_days`, `$.catalog.active_channels` y `$.expiring_soon` según los widgets. Llama al endpoint al entrar al dashboard y usa sus resultados para las tarjetas y la tabla de próximos vencimientos.

## Configuración de la aplicación

### `GET /api/v1/admin/settings`

Devuelve la configuración comercial que puede mostrarse en el dashboard o editarse desde la sección Configuración. No expone secretos de Railway ni parámetros internos de infraestructura.

```json
{
  "company_name": "Patzi IPTV",
  "support_email": "",
  "support_phone": "",
  "currency": "USD",
  "timezone": "America/El_Salvador"
}
```

### `PATCH /api/v1/admin/settings`

Envía uno o más campos para actualizar:

```json
{
  "company_name": "Patzi IPTV",
  "support_email": "soporte@example.com",
  "support_phone": "+503 7000 0000",
  "currency": "USD",
  "timezone": "America/El_Salvador"
}
```

La moneda usa un código de tres letras mayúsculas (por ejemplo `USD`); la zona horaria debe ser un nombre IANA válido. La configuración se guarda en MongoDB (`app_settings`, documento `main`). Los valores predeterminados se usan mientras no se haya guardado un valor. El endpoint requiere administrador.

## Organización del backend por pantalla/dominio

Los archivos de FlutterFlow contienen las pantallas visuales. En este repositorio, los routers organizan la API que consume cada pantalla:

| Pantalla/dominio | Archivo backend | Rutas |
| --- | --- | --- |
| Dashboard | `backend/routers/dashboard.py` | `GET /api/v1/admin/dashboard` |
| Clientes y dispositivos | `backend/routers/customers.py` | CRUD parcial de clientes, reset de contraseña y revocación de dispositivos |
| Login móvil | `backend/routers/mobile_auth.py` | Login y perfil/catálogos de la aplicación de clientes |
| Canales | `backend/routers/channels.py` | `GET/POST /api/v1/admin/channels`, `PATCH /api/v1/admin/channels/{id}` |
| Paquetes y vencimientos | `backend/routers/packages.py` | `/api/v1/admin/packages`, `/api/v1/admin/subscriptions` |
| Configuración | `backend/routers/settings.py` | `GET/PATCH /api/v1/admin/settings` |

Los serializers y la paginación compartida están en `backend/routers/management_helpers.py`. Los routers se registran en `backend/main.py`; mantén esa conexión y esta tabla actualizadas al agregar una pantalla o dominio.

## Índices MongoDB

Al iniciar, la API crea índices únicos para username de panel, username de cliente, UUID de dispositivo entre clientes, email no vacío, nombre de canal y nombre de paquete, además de índices para ordenar/buscar vencimientos y clientes recientes. Los clientes existentes antes de habilitar login móvil no tienen username/hash y deben editarse en el panel: asignarles username y restablecer contraseña antes de poder iniciar sesión. No se requiere script manual de migración.

## Errores

- `401`: JWT ausente, inválido o vencido.
- `403`: el usuario autenticado no tiene rol `admin`.
- `404`: ID inválido o cliente/paquete/canal objetivo no encontrado según la operación.
- `409`: email o nombre duplicado.
- `409 device_limit_reached`: la cuenta ya tiene asociados todos los dispositivos permitidos; el administrador debe aumentar el límite (máximo 4) o revocar un UUID.
- `422`: body incompleto/incorrecto, IDs de canal inválidos, UUID no válido, fecha sin zona horaria o canales inactivos al crear paquete.

## Actualización de documentación

Cada cambio de entidades, rutas, filtros, cálculos de vencimiento, métricas o payloads del dashboard debe actualizar esta guía en el mismo cambio. Mantén `README.md` enlazando esta referencia.
