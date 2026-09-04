"""
Core application state and background tasks.
Integrates the synthetic data simulator and the monitoring agent directly into 
the FastAPI service so the deployed Cloud Run instance is completely self-contained.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

import asyncio

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from google.adk.cli.fast_api import get_fast_api_app

import shared_state

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    sim_task = asyncio.create_task(shared_state.run_simulator_loop())
    mon_task = asyncio.create_task(shared_state.run_monitor_loop())
    yield
    sim_task.cancel()
    mon_task.cancel()

GRAFANA_WEBHOOK_SECRET = os.environ.get("GRAFANA_WEBHOOK_SECRET")

limiter = Limiter(key_func=get_remote_address)

cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:8080")
allowed_origins = [origin.strip() for origin in cors_origins_str.split(",")]

app = get_fast_api_app(
    agents_dir=".",
    web=False,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/api/status")
async def get_status():
    return {
        "status": shared_state.state.status,
        "severity": shared_state.state.last_severity,
        "summary": shared_state.state.last_summary,
        "message": shared_state.state.last_message,
        "last_check": shared_state.state.last_check,
    }


@app.get("/api/history")
async def get_history():
    return {"alerts": shared_state.state.alert_history}


@app.post("/api/trigger-incident")
@limiter.limit("3/minute")
async def trigger_incident(request: Request):
    shared_state.state.request_incident()
    return {"triggered": True}

background_tasks = set()
@app.post("/api/grafana-webhook")
async def grafana_webhook(request: Request):
    payload = await request.json()
    task = asyncio.create_task(shared_state.handle_webhook_alert(payload))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return {"received": True}

# Dashboard frontend - mounted last so it doesn't shadow the API routes
# above or ADK's own agent routes.
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
