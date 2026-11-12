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

## Desplegar en Railway

1. Crea un servicio de Railway conectado a este repositorio.
2. Deja **Root Directory** como `/`.
3. Railway detecta `requirements.txt`, instala FastAPI y Uvicorn, y usa el comando de `railway.json` para iniciar la app con el puerto dinámico `$PORT`.
4. El health check consulta `/api/v1/health`.

No se requieren variables de entorno para esta base inicial.
