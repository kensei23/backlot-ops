"""
Synthetic render-farm / encode-pipeline data generator for Backlot Ops.

Simulates a media studio's infrastructure and continuously pushes
realistic metrics and logs to Grafana Cloud, so the agent has real data
to work with instead of an empty account. Runs as a standalone background
process - leave it running while the agent is in use. Ctrl+C to stop.
"""

import os
import time
import random
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

METRICS_URL = os.environ["GRAFANA_METRICS_URL"].rstrip("/")
METRICS_USERNAME = os.environ["GRAFANA_METRICS_USERNAME"]
METRICS_API_KEY = os.environ["GRAFANA_METRICS_API_KEY"]

LOGS_URL = os.environ["GRAFANA_LOGS_URL"]
LOGS_USERNAME = os.environ["GRAFANA_LOGS_USERNAME"]
LOGS_API_KEY = os.environ["GRAFANA_LOGS_API_KEY"]

# Grafana Cloud accepts plain-text Influx Line Protocol at this path -
# simpler than the full Prometheus remote_write protobuf setup.
METRICS_PUSH_URL = f"{METRICS_URL}/api/v1/push/influx/write"

# Current simulated state of the render farm. Nudged each tick, with an
# occasional simulated incident, so the data reads as a living system
# rather than random noise.
state = {
    "cpu_percent": 45.0,
    "active_jobs": 6,
    "queue_depth": 3,
    "encode_fps": 60.0,
    "jobs_completed_total": 0,
    "jobs_failed_total": 0,
}

incident_active = False
incident_ticks_remaining = 0


def send_metrics():
    """Push current state as metrics using Influx Line Protocol: one line
    per measurement, formatted as
    measurement,tag=value field1=val1,field2=val2 timestamp_ns
    """
    timestamp_ns = int(time.time() * 1_000_000_000)

    lines = [
        f"render_farm,node=render01 cpu_percent={state['cpu_percent']:.1f},"
        f"active_jobs={state['active_jobs']},queue_depth={state['queue_depth']} {timestamp_ns}",

        f"encode_pipeline,node=encode01 fps={state['encode_fps']:.1f} {timestamp_ns}",

        f"job_stats jobs_completed_total={state['jobs_completed_total']},"
        f"jobs_failed_total={state['jobs_failed_total']} {timestamp_ns}",
    ]
    body = "\n".join(lines)

    resp = requests.post(
        METRICS_PUSH_URL,
        headers={
            "Authorization": f"Bearer {METRICS_USERNAME}:{METRICS_API_KEY}",
            "Content-Type": "text/plain",
        },
        data=body,
        timeout=10,
    )
    if resp.status_code >= 300:
        print(f"  [metrics] FAILED ({resp.status_code}): {resp.text[:200]}")
    else:
        print(f"  [metrics] sent OK")


def send_log(level: str, message: str):
    """Push a single log line to Loki. Expects a list of streams, each
    with labels (job/level) and [timestamp_ns, message] pairs.
    """
    timestamp_ns = str(int(time.time() * 1_000_000_000))

    payload = {
        "streams": [
            {
                "stream": {"job": "render-farm", "level": level},
                "values": [[timestamp_ns, message]],
            }
        ]
    }

    resp = requests.post(
        LOGS_URL,
        auth=(LOGS_USERNAME, LOGS_API_KEY),
        json=payload,
        timeout=10,
    )
    if resp.status_code >= 300:
        print(f"  [logs]    FAILED ({resp.status_code}): {resp.text[:200]}")
    else:
        print(f"  [logs]    sent OK: {message}")


def tick():
    """One simulated moment in time: update state, decide if anything
    interesting happened, and push metrics and logs for it.
    """
    global incident_active, incident_ticks_remaining

    if not incident_active and random.random() < 0.05:  # ~5% chance per tick
        incident_active = True
        incident_ticks_remaining = random.randint(3, 6)
        send_log("error", "Render node render01 reporting elevated job failures")

    if incident_active:
        state["cpu_percent"] = min(98, state["cpu_percent"] + random.uniform(5, 12))
        state["queue_depth"] += random.randint(1, 3)
        state["encode_fps"] = max(10, state["encode_fps"] - random.uniform(3, 8))
        if random.random() < 0.6:
            state["jobs_failed_total"] += 1
            send_log("error", "Job render_job_%d failed: GPU memory allocation error" % random.randint(1000, 9999))

        incident_ticks_remaining -= 1
        if incident_ticks_remaining <= 0:
            incident_active = False
            send_log("info", "Render farm metrics returning to normal range")
    else:
        state["cpu_percent"] += random.uniform(-3, 3)
        state["cpu_percent"] = max(20, min(70, state["cpu_percent"]))
        state["queue_depth"] = max(0, state["queue_depth"] + random.randint(-1, 1))
        state["encode_fps"] += random.uniform(-2, 2)
        state["encode_fps"] = max(45, min(65, state["encode_fps"]))

        if random.random() < 0.7:
            state["jobs_completed_total"] += 1
            send_log("info", "Job render_job_%d completed successfully" % random.randint(1000, 9999))

    state["active_jobs"] = max(0, state["queue_depth"] // 2 + random.randint(3, 6))

    send_metrics()


if __name__ == "__main__":
    print("Backlot Ops - synthetic render farm starting up.")
    print(f"Pushing metrics to: {METRICS_PUSH_URL}")
    print(f"Pushing logs to:    {LOGS_URL}")
    print("Press Ctrl+C to stop.\n")

    while True:
        print(f"--- tick @ {datetime.now(timezone.utc).isoformat()} ---")
        try:
            tick()
        except Exception as e:
            print(f"  [error] something went wrong this tick: {e}")
        time.sleep(15)
