# Backlot Ops 🎬

**An Agentic On-Call Technical Producer for Media Studios**

Backlot Ops is a multi-agent system built with Google ADK, Gemini 2.5 Flash, and Grafana Cloud. It bridges the communication gap between technical engineering data (like CPU spikes and GPU memory errors) and production schedules. 

Instead of forcing producers to decipher complex metrics, Backlot Ops actively monitors a live render farm and automatically translates technical incidents into business-focused stakeholder updates.

## 🏗️ Architecture

*   **The Simulator:** A background FastAPI task generating synthetic metrics (CPU usage, queue depth) and Loki logs, pushing them live to Grafana.
*   **Technical Agent (`root_agent`):** Proactively monitors the live Grafana dashboard using the Prometheus and Loki MCP tools. 
*   **Line Producer Agent:** When the technical agent detects a spike, it consults the line producer agent to assess the business severity and generate a plain-English message for the production team.
*   **Event-Driven Alerting:** Grafana alert rules (CPU threshold and Loki error-log rate) call a `/api/grafana-webhook` endpoint the moment a real incident is detected, triggering an immediate agent investigation instead of waiting on the next scheduled poll - and clearing the incident automatically once Grafana confirms it's resolved.
*   **Closed-Loop Feedback:** The agent automatically writes its findings back to the Grafana dashboard via the Annotations API, dropping a marker exactly where the incident occurred.
*   **Production Security Hardening:** Implemented dynamic CORS middleware and IP-based rate limiting to secure backend endpoints, restrict unauthorized cross-origin requests, and protect cloud resources from API abuse.

### 🔊 Experimental Features
- **Gemini TTS Audio Generation (`tts.py`)**: Explored using Gemini's native audio generation capabilities (`gemini-3.1-flash-tts-preview`) to convert Line Producer stakeholder alerts into spoken `.wav` files for accessibility. (Note: Kept as an experimental module for future integration).

## 🚀 Quickstart

**1. Prerequisites**
*   Python 3.10+
*   `uv` installed (for `uvx` MCP server execution)
*   Google Cloud SDK authenticated (`gcloud auth application-default login`)
*   A Grafana Cloud stack

**2. Setup**
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
