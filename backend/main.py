from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import (
    routes_approvals,
    routes_chat,
    routes_devices,
    routes_logs,
    routes_snapshots,
    routes_suggestions,
    routes_ws,
)
from backend.core.logging import configure_logging, get_logger
from backend.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(log_level=settings.log_level, logs_dir=settings.logs_dir)
    get_logger(__name__).info("startup", version="0.0.1")
    yield


app = FastAPI(title="Cisco AI Config Agent", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    # Wildcards combined with allow_credentials=True defeat CORS — pin to
    # exactly what the frontend uses. Audit #4.
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


app.include_router(routes_logs.router)
app.include_router(routes_approvals.router)
app.include_router(routes_chat.router)
app.include_router(routes_ws.router)
app.include_router(routes_devices.router)
app.include_router(routes_snapshots.router)
app.include_router(routes_suggestions.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# IMPORTANT: keep this LAST. StaticFiles at "/" shadows any route declared after it.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
