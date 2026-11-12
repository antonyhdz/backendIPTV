from fastapi import FastAPI

app = FastAPI(
    title="Patzi IPTV API",
    version="0.1.0",
    description="Backend API for Patzi IPTV.",
)


@app.get("/", tags=["Sistema"])
def root() -> dict[str, str]:
    return {
        "app": app.title,
        "version": app.version,
        "status": "online",
        "docs": "/docs",
    }


@app.get("/api/v1/health", tags=["Sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/status", tags=["Sistema"])
def status() -> dict[str, str]:
    return {
        "app": app.title,
        "version": app.version,
        "status": "online",
        "docs": "/docs",
    }
