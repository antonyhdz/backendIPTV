# Login de usuarios

Esta guía describe el flujo de autenticación implementado en Patzi IPTV API y sirve como referencia para FlutterFlow y futuros cambios.

## Resumen del flujo

1. El servidor crea el primer administrador al iniciar si el username configurado en Railway todavía no existe.
2. FlutterFlow envía username y password a `POST /api/v1/auth/login` como JSON.
3. El servidor normaliza el username a minúsculas, busca el usuario en MongoDB y verifica el hash de la contraseña con Argon2.
4. Si las credenciales son válidas y la cuenta está activa, responde con un JWT de acceso.
5. El cliente conserva el token en estado persistente y lo envía como `Authorization: Bearer <token>` a las rutas protegidas.
6. `GET /api/v1/auth/me` valida el token y devuelve los datos del usuario autenticado.

Las contraseñas nunca se guardan en texto plano. El registro público no está habilitado; los usuarios se crean mediante una ruta protegida de administrador.

## Configuración requerida en Railway

Configura estas variables en el servicio de Railway:

| Variable | Uso |
| --- | --- |
| `MONGODB_URI` | URI de conexión de MongoDB Atlas. Codifica los caracteres especiales de la contraseña para URL. |
| `MONGODB_DB_NAME` | Nombre de la base de datos. |
| `JWT_SECRET` | Secreto aleatorio de al menos 32 caracteres para firmar tokens. |
| `JWT_ISSUER` | Emisor esperado del JWT; por defecto `patzi-iptv-api`. |
| `JWT_EXPIRATION_MINUTES` | Duración del token; por defecto 15 minutos. |
| `INITIAL_ADMIN_USERNAME` | Username del primer administrador (mínimo 3 caracteres). |
| `INITIAL_ADMIN_PASSWORD` | Contraseña del primer administrador (mínimo 12 caracteres). |
| `ALLOWED_ORIGINS` | Orígenes web permitidos separados por comas, si aplica. |

El proceso de inicio comprueba las variables, conecta a MongoDB y crea los índices. Si MongoDB rechaza la autenticación, verifica el usuario/contraseña de Database Access en Atlas, el URI y el acceso de red de Atlas. Cambiar `INITIAL_ADMIN_PASSWORD` después de crear al administrador no cambia el hash de la cuenta existente.

## Configurar la llamada Login en FlutterFlow

En **API Calls**, crea `Login` con:

- Método: `POST`
- URL: `https://<dominio-publico-railway>/api/v1/auth/login`
- Header: `Content-Type: application/json`
- Body: JSON

Crea variables de API llamadas exactamente `username` y `password`, ambas tipo `String`. En el editor del body, inserta las variables usando la interfaz de FlutterFlow (por ejemplo, arrastrándolas al valor del campo); no escribas marcadores como texto literal. La estructura que debe enviarse al servidor es:

```json
{
  "username": "valor del username",
  "password": "valor de la contraseña"
}
```

### Probar en Response & Test

Los valores de **App Constants** no se cargan automáticamente en el panel **Response & Test**. Para la prueba de definición, escribe valores de prueba en la columna **Value** de las variables y revisa la pestaña **Body** del preview para confirmar que se están sustituyendo. Esta prueba puede realizar una autenticación real; evita guardar o compartir capturas con credenciales.

### Usar desde el formulario de login

En el botón de inicio de sesión configura una acción **Backend/Database → API Call → Login**. Asigna los valores de cada parámetro de la acción:

- `username` ← valor del widget TextField de usuario (o una App Constant solo si quieres un usuario fijo de prueba).
- `password` ← valor del widget TextField de contraseña (o una App Constant solo para una prueba controlada).

Las variables de API son parámetros; no se conectan automáticamente con constantes o campos de formulario. En la acción del botón hay que asignar la fuente de cada parámetro. Para credenciales que cambian según el usuario, usa los valores de los TextField, no constantes.

## Respuesta de login

En caso de éxito (`200 OK`):

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "<id MongoDB como texto>",
    "username": "admin",
    "role": "admin",
    "is_active": true
  }
}
```

Guarda `access_token` en una variable de App State marcada como persistente si la sesión debe sobrevivir al cierre de la app. Para rutas protegidas, añade el header `Authorization` con el valor `Bearer <token>`. Si usas una variable en la definición de API Call, pásale el token guardado desde cada acción que ejecute la ruta protegida.

El token dura lo indicado por `JWT_EXPIRATION_MINUTES` (15 minutos por defecto). Al expirar, el usuario debe volver a iniciar sesión; actualmente no hay refresh token.

## Rutas relacionadas

| Método | Ruta | Acceso | Resultado |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Público | Inicia sesión y devuelve token y usuario. |
| `GET` | `/api/v1/auth/me` | JWT requerido | Devuelve el usuario autenticado. |
| `POST` | `/api/v1/admin/users` | Administrador | Crea una cuenta `customer` o `admin`. |

`POST /api/v1/admin/users` recibe `username`, `password` y un `role` opcional (`customer` por defecto). La contraseña debe tener al menos 12 caracteres. Envía el JWT de administrador en `Authorization: Bearer <token>`.

## Errores comunes

- `401 Incorrect username or password.`: username/password incorrectos, cuenta inactiva o body con nombres/valores incorrectos. Comprueba el Body preview y que la acción del botón asigne ambas variables.
- `422 Unprocessable Entity`: el body no es JSON válido, falta `username`/`password`, o alguno incumple los límites del esquema.
- `429 Too Many login attempts`: se excedió el límite de 8 fallos por username e IP dentro de la ventana de bloqueo de 10 minutos; espera antes de volver a intentar.
- Error de autenticación MongoDB durante el arranque: corrige `MONGODB_URI`/credenciales de Atlas y permite el acceso de red desde el servicio.
- `401` en una ruta protegida: token ausente, inválido o expirado. Vuelve a autenticar y manda el prefijo `Bearer `.

## Mantenimiento de esta documentación

Cada vez que se agregue o cambie una función relacionada con autenticación, actualizar esta guía en el mismo cambio: rutas y permisos, esquema de entrada/salida, variables de entorno, comportamiento de seguridad, instrucciones de cliente y errores relevantes. Si una nueva función tiene documentación más extensa, crear una guía temática en `backend/` y enlazarla desde este archivo.
