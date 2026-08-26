"""
Shared application state and background loops for the deployed service.

This ports the core logic from simulator.py (synthetic data generation)
and monitor.py (proactive health checks) into reusable async functions,
so the same behavior runs as background tasks inside the deployed Cloud
Run service - making the hosted URL self-contained, not dependent on
anything running on a local machine.

simulator.py and monitor.py remain as standalone scripts for local dev
and testing - this isn't a replacement for those, it's the same logic
adapted to run inside main.py's FastAPI app instead.
"""

import asyncio
import os
import random
import time
from datetime import datetime, timezone

import requests
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from backlot_ops_agent.agent import root_agent

METRICS_URL = os.environ["GRAFANA_METRICS_URL"].rstrip("/")
METRICS_USERNAME = os.environ["GRAFANA_METRICS_USERNAME"]
METRICS_API_KEY = os.environ["GRAFANA_METRICS_API_KEY"]
LOGS_URL = os.environ["GRAFANA_LOGS_URL"]
LOGS_USERNAME = os.environ["GRAFANA_LOGS_USERNAME"]
LOGS_API_KEY = os.environ["GRAFANA_LOGS_API_KEY"]
METRICS_PUSH_URL = f"{METRICS_URL}/api/v1/push/influx/write"

CHECK_INTERVAL_SECONDS = 15
MAX_HISTORY = 20


class AppState:
    """In-memory state shared between the background loops and the web dashboard.
       Note: For a production deployment with multiple Cloud Run instances, 
       this should be moved to Redis or Firestore.
    """

    def __init__(self):
        self.status = "starting"
        self.last_message = ""
        self.last_check = None
        self.alert_history = []
        self.force_incident_flag = False

    def request_incident(self):
        self.force_incident_flag = True

    def record_check(self, status: str, message: str):
        now = datetime.now(timezone.utc).isoformat()
        self.status = status
        self.last_message = message
        self.last_check = now
        if status == "incident":
            self.alert_history.append({"timestamp": now, "message": message})
            self.alert_history = self.alert_history[-MAX_HISTORY:]


state = AppState()

# --- Simulator state (ported from simulator.py) ---
sim_data = {
    "cpu_percent": 45.0,
    "active_jobs": 6,
    "queue_depth": 3,
    "encode_fps": 60.0,
    "jobs_completed_total": 0,
    "jobs_failed_total": 0,
}
_incident_active = False
_incident_ticks_remaining = 0


def _send_metrics():
    timestamp_ns = int(time.time() * 1_000_000_000)
    lines = [
        f"render_farm,node=render01 cpu_percent={sim_data['cpu_percent']:.1f},"
        f"active_jobs={sim_data['active_jobs']},queue_depth={sim_data['queue_depth']} {timestamp_ns}",
        f"encode_pipeline,node=encode01 fps={sim_data['encode_fps']:.1f} {timestamp_ns}",
        f"job_stats jobs_completed_total={sim_data['jobs_completed_total']},"
        f"jobs_failed_total={sim_data['jobs_failed_total']} {timestamp_ns}",
    ]
    requests.post(
        METRICS_PUSH_URL,
        headers={
            "Authorization": f"Bearer {METRICS_USERNAME}:{METRICS_API_KEY}",
            "Content-Type": "text/plain",
        },
        data="\n".join(lines),
        timeout=10,
    )


def _send_log(level: str, message: str):
    timestamp_ns = str(int(time.time() * 1_000_000_000))
    payload = {
        "streams": [
            {"stream": {"job": "render-farm", "level": level}, "values": [[timestamp_ns, message]]}
        ]
    }
    requests.post(LOGS_URL, auth=(LOGS_USERNAME, LOGS_API_KEY), json=payload, timeout=10)


def _simulator_tick():
    global _incident_active, _incident_ticks_remaining

    if state.force_incident_flag and not _incident_active:
        _incident_active = True
        _incident_ticks_remaining = random.randint(2, 3)
        state.force_incident_flag = False
        _send_log("error", "Render node render01 reporting elevated job failures")

    if _incident_active:
        sim_data["cpu_percent"] = min(98, sim_data["cpu_percent"] + random.uniform(5, 12))
        sim_data["queue_depth"] += random.randint(1, 3)
        sim_data["encode_fps"] = max(10, sim_data["encode_fps"] - random.uniform(3, 8))
        if random.random() < 0.6:
            sim_data["jobs_failed_total"] += 1
            _send_log("error", "Job render_job_%d failed: GPU memory allocation error" % random.randint(1000, 9999))

        _incident_ticks_remaining -= 1
        if _incident_ticks_remaining <= 0:
            _incident_active = False
            sim_data["cpu_percent"] = random.uniform(40, 55)
            sim_data["queue_depth"] = random.randint(2, 4)
            sim_data["encode_fps"] = random.uniform(55, 62)
            _send_log("info", "Render farm metrics returning to normal range")
    else:
        sim_data["cpu_percent"] = max(20, min(70, sim_data["cpu_percent"] + random.uniform(-3, 3)))
        sim_data["queue_depth"] = max(0, sim_data["queue_depth"] + random.randint(-1, 1))
        sim_data["encode_fps"] = max(45, min(65, sim_data["encode_fps"] + random.uniform(-2, 2)))
        if random.random() < 0.7:
            sim_data["jobs_completed_total"] += 1
            _send_log("info", "Job render_job_%d completed successfully" % random.randint(1000, 9999))

    sim_data["active_jobs"] = max(0, sim_data["queue_depth"] // 2 + random.randint(3, 6))
    _send_metrics()


async def run_simulator_loop():
    while True:
        try:
            _simulator_tick()
        except Exception as e:
            print(f"[simulator] error: {e}")
        await asyncio.sleep(15)


# --- Monitor loop (ported from monitor.py) ---
CHECK_PROMPT = (
    "Check the render farm's current CPU usage, queue depth, and recent "
    "logs for errors. If everything looks normal, reply with only the "
    "single word HEALTHY and nothing else. If you find a real problem, "
    "start your reply with INCIDENT: followed by a 2-3 sentence plain "
    "English explanation of what's wrong and likely why."
)


async def _check_once(runner: InMemoryRunner) -> str:
    session = await runner.session_service.create_session(app_name=runner.app_name, user_id="monitor")
    message = genai_types.Content(role="user", parts=[genai_types.Part(text=CHECK_PROMPT)])
    final_text = ""
    async for event in runner.run_async(user_id="monitor", session_id=session.id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text or ""
    return final_text.strip()


async def run_monitor_loop():
    runner = InMemoryRunner(agent=root_agent)

    # Warm-up routine: The initial MCP tool connection can sometimes take a few 
    # seconds to initialize. We ping it a few times to ensure the pipe is open 
    # before starting the real monitoring cycle.
    warm_up_succeeded = False
    for attempt in range(1, 6):
        try:
            result = await _check_once(runner)
            result_lower = result.lower()
            
            # Check for the specific error strings the LLM returns on cold-start
            if "unable to retrieve" in result_lower or "issue connecting" in result_lower:
                print(f"[monitor] warm-up attempt {attempt} failed (tool error string returned)")
                await asyncio.sleep(5)
                continue
                
            print(f"[monitor] warm-up check succeeded (attempt {attempt})")
            warm_up_succeeded = True
            break
        except Exception as e:
            print(f"[monitor] warm-up attempt {attempt} crashed: {e}")
            await asyncio.sleep(5)

    if not warm_up_succeeded:
        print("[monitor] warm-up never succeeded after 5 attempts, continuing anyway")

    while True:
        try:
            result = await _check_once(runner)
        except Exception as e:
            state.record_check("error", str(e))
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            continue

        print(f"\n[monitor] Agent replied: {result}\n")
        
        if "INCIDENT" in result.upper():
            state.record_check("incident", result)
        else:
            state.record_check("healthy", result)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
