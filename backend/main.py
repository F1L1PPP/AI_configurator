from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.core.logging import configure_logging, get_logger
from backend.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(log_level=settings.log_level, logs_dir=settings.logs_dir)
    get_logger(__name__).info("startup", version="0.0.1")
    yield


app = FastAPI(title="Cisco AI Config Agent", version="0.0.1", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
