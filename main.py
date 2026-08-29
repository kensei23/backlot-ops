"""
Core application state and background tasks.
Integrates the synthetic data simulator and the monitoring agent directly into 
the FastAPI service so the deployed Cloud Run instance is completely self-contained.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from google.adk.cli.fast_api import get_fast_api_app

import shared_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim_task = asyncio.create_task(shared_state.run_simulator_loop())
    mon_task = asyncio.create_task(shared_state.run_monitor_loop())
    yield
    sim_task.cancel()
    mon_task.cancel()


app = get_fast_api_app(
    agents_dir=".",
    web=False,
    lifespan=lifespan,
)


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
async def trigger_incident():
    shared_state.state.request_incident()
    return {"triggered": True}


# Dashboard frontend - mounted last so it doesn't shadow the API routes
# above or ADK's own agent routes.
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
