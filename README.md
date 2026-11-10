# Patzi IPTV Backend

API and background services for the Patzi IPTV panel and mobile app. Built with FastAPI, SQLAlchemy, SQLite, and optional MongoDB Atlas.

## Requirements

- Python 3.10 or newer
- Git

## Local setup

From the repository root:

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
```

### Ubuntu / Andronix

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cp .env.example .env
```

Edit `.env` and set `PATZI_ADMIN_USER`, `PATZI_ADMIN_PASSWORD`, and a private `PATZI_JWT_SECRET` of at least 32 characters. The sample values are placeholders. Keep `.env` private and do not commit it.

Start the API from the repository root:

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

- API docs: `/docs`
- Health endpoint: `/api/v1/health`
- API contract: [`backend/BACKEND_API.md`](backend/BACKEND_API.md)

SQLite data and backups are created under `data/` by default. MongoDB Atlas can be enabled with `MONGODB_URI`; see `backend/app_config.py` for supported settings. The EPG guide and updater scripts are in `backend/epg/`.

Each operating system needs its own virtual environment. Do not upload `.env`, `.venv`, `venv`, `data/`, or database files.

## Railway deployment

This repository includes `railway.json`, a root `requirements.txt`, and a `Procfile`. Connect the GitHub repository to a Railway service with the repository root (`/`) as its Root Directory. Railpack installs the Python dependencies, starts Uvicorn on Railway's injected `$PORT`, and checks `/api/v1/health`.

In the Railway service's **Variables**, set:

- `PATZI_ADMIN_USER`: administrator login name.
- `PATZI_ADMIN_PASSWORD`: a strong administrator password.
- `PATZI_JWT_SECRET`: a private random value of at least 32 characters.
- `MONGODB_URI`: the MongoDB Atlas connection URI. This is recommended for persistent data; do not commit it.
- `MONGODB_DB_NAME`: database name, for example `patzi_iptv`.
- `PATZI_PANEL_ORIGINS`: comma-separated browser origins allowed by CORS, including `https://panel.patzi.shop` if that panel is used.

The database URI and credentials must be set as Railway variables, not in source control. If SQLite is used instead of MongoDB, attach a Railway volume mounted at `/app/data` so the database survives redeploys; set `PATZI_DATABASE_PATH=/app/data/patzi.db`.
