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
  "email": "cliente@example.com",
  "phone": "+503 7000 0000",
  "notes": "Información opcional"
}
```

Solo `name` es obligatorio. Email es opcional y único si se proporciona. Responde `201` con el cliente, `409` si el email ya está asignado.

### `GET /api/v1/admin/customers`

Parámetros opcionales: `page` (1), `page_size` (25, máximo 100), `search` (busca nombre, email o teléfono) y `active` (`true`/`false`). Devuelve elementos paginados.

### `PATCH /api/v1/admin/customers/{customer_id}`

Actualiza `name`, `email`, `phone`, `notes` y/o `is_active`. Envía solo los campos a cambiar. Para archivar: `{ "is_active": false }`. Puedes enviar `""` para limpiar un campo opcional de texto; `name` no puede quedar vacío.

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

Parámetros: `page` (1), `page_size` (25, máximo 100), `state` (`all`, `active`, `expired` o `expiring`; por defecto `all`). `expiring` significa vencimiento dentro de los próximos 7 días. Incluye `customer_name` y `package_name` para mostrar en tablas.

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
| Clientes | `backend/routers/customers.py` | `GET/POST /api/v1/admin/customers`, `PATCH /api/v1/admin/customers/{id}` |
| Canales | `backend/routers/channels.py` | `GET/POST /api/v1/admin/channels`, `PATCH /api/v1/admin/channels/{id}` |
| Paquetes y vencimientos | `backend/routers/packages.py` | `/api/v1/admin/packages`, `/api/v1/admin/subscriptions` |
| Configuración | `backend/routers/settings.py` | `GET/PATCH /api/v1/admin/settings` |

Los serializers y la paginación compartida están en `backend/routers/management_helpers.py`. Los routers se registran en `backend/main.py`; mantén esa conexión y esta tabla actualizadas al agregar una pantalla o dominio.

## Índices MongoDB

Al iniciar, la API crea índices únicos para email no vacío, nombre de canal y nombre de paquete, además de índices para ordenar/buscar vencimientos y clientes recientes. No se requiere script manual de migración.

## Errores

- `401`: JWT ausente, inválido o vencido.
- `403`: el usuario autenticado no tiene rol `admin`.
- `404`: ID inválido o cliente/paquete/canal objetivo no encontrado según la operación.
- `409`: email o nombre duplicado.
- `422`: body incompleto/incorrecto, IDs de canal inválidos, fecha sin zona horaria o canales inactivos al crear paquete.

## Actualización de documentación

Cada cambio de entidades, rutas, filtros, cálculos de vencimiento, métricas o payloads del dashboard debe actualizar esta guía en el mismo cambio. Mantén `README.md` enlazando esta referencia.
