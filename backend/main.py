from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import routes_approvals, routes_chat, routes_logs, routes_ws
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
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
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


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
