# IMPORTANT: this MUST run before any asyncio loop is created — including
# before FastAPI / uvicorn imports that may instantiate one. Keep it at the
# very top of the module.
#
# Windows + Playwright sync API + FastAPI thread pool = NotImplementedError.
# Root cause: uvicorn defaults to asyncio.WindowsProactorEventLoop on
# Windows. The Proactor loop does not implement `add_reader`/`add_writer`.
# Playwright's sync API runs a private asyncio loop inside its own thread,
# and that loop inherits the *process-wide* policy — so when Playwright
# tries to wire up a pipe reader for stdout/stderr of the Chromium driver
# subprocess, it raises bare `NotImplementedError`.
#
# Fix: pin the policy to SelectorEventLoop on Windows. FastAPI/uvicorn
# do not require Proactor-specific features for our workload (no subprocess
# transport, no named pipes from request handlers), so this is safe.
# Effect is invisible on Linux / macOS where the default policy is fine.
#
# Verified against the `NotImplementedError()` raised inside
# `webui_browser()` at `sync_playwright().__enter__()` on a Windows host
# running uvicorn for a local C1111 test.
import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
