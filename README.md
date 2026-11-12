# Patzi IPTV API

Base nueva del backend con FastAPI, lista para desplegar en Railway.

## Ejecutar localmente

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

La API queda disponible en `http://127.0.0.1:8000`; la documentación interactiva está en `/docs`.

Copia `.env.example` a `.env` y configura MongoDB, `JWT_SECRET` y la contraseña inicial del administrador antes de arrancar. El primer administrador se crea al iniciar la API si todavía no existe. Los demás usuarios se crean desde `POST /api/v1/admin/users` con un JWT de administrador.

## Login

- `POST /api/v1/auth/login` recibe `username` y `password`, y devuelve un JWT de acceso de corta duración.
- Envía el token en `Authorization: Bearer <token>`.
- `GET /api/v1/auth/me` devuelve la identidad autenticada.
- Las contraseñas se guardan con Argon2id. El registro público está desactivado.
- Guía detallada del flujo, rutas, configuración de FlutterFlow y solución de errores: [`backend/AUTH.md`](backend/AUTH.md).
- Actualiza esa guía junto con cada cambio del sistema de autenticación.
- El login del cliente móvil usa rutas y cuentas separadas del panel; límites por UUID/dispositivo y paquetes asociados se documentan en [`backend/MANAGEMENT.md`](backend/MANAGEMENT.md).

## Clientes, catálogo y dashboard

- Guía de clientes, canales, paquetes con vencimiento, endpoints de administración y dashboard: [`backend/MANAGEMENT.md`](backend/MANAGEMENT.md).
- Mantén esta documentación al día en el mismo cambio que agregue o modifique estas funciones.

El backend organiza sus routers por pantalla/dominio en `backend/routers/`: `dashboard.py`, `customers.py`, `channels.py`, `packages.py` y `settings.py`. Los archivos de interfaz de las pantallas se crean en FlutterFlow; cada API Call consume las rutas documentadas en `backend/MANAGEMENT.md`.

## Desplegar en Railway

1. Crea un servicio de Railway conectado a este repositorio.
2. Deja **Root Directory** como `/`.
3. Railway detecta `requirements.txt`, instala FastAPI y Uvicorn, y usa el comando de `railway.json` para iniciar la app con el puerto dinámico `$PORT`.
4. Configura en **Variables** `MONGODB_URI`, `MONGODB_DB_NAME`, `JWT_SECRET`, `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_PASSWORD` y, si usas panel web, `ALLOWED_ORIGINS`.
5. El health check consulta `/api/v1/health`.

Usa una contraseña inicial única y guárdala de forma segura. `JWT_SECRET` debe ser aleatorio y tener al menos 32 caracteres.
