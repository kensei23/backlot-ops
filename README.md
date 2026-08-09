# Backlot Ops — Week 1 Starter

Goal for this week: get one real, successful tool call from your ADK agent
through to the Grafana Cloud MCP server. That's it. No synthetic data, no
anomaly detection yet — just prove the connection works before building
anything on top of it.

## 1. Prerequisites

- Python 3.10+ (MCP requires 3.9+, ADK generally wants a recent version)
- A Google Cloud project with billing enabled (needed for Vertex AI / Gemini
  calls, even within free credit)
- `gcloud` CLI installed and authenticated (`gcloud auth application-default login`)
- A Grafana Cloud account (free tier is enough) — you created this and
  accepted the Grafana Assistant terms per the hackathon resources page

## 2. Setup

```bash
cd backlot-ops
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy the env template and fill in your real values:

```bash
cp .env.example .env
```

Edit `.env`:
- `GOOGLE_CLOUD_PROJECT` — your GCP project ID
- `GOOGLE_CLOUD_LOCATION` — usually `us-central1` is fine
- `GRAFANA_STACK_URL` — your Grafana Cloud stack URL, e.g.
  `https://yourstackname.grafana.net` (find this in your Grafana Cloud
  account portal)

## 3. Authenticate with Google Cloud

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

This is what lets ADK call Gemini via Vertex AI using your identity, no API
key needed.

## 4. Run the agent

ADK ships a local web UI that's the fastest way to poke at an agent while
building. From the **parent** directory (the one containing
`backlot_ops_agent/`, i.e. `backlot-ops/`), run:

```bash
adk web
```

This starts a local server (usually `http://localhost:8000`) with a chat UI.
Select `backlot_ops_agent` from the dropdown and try asking it something
like:

> "What dashboards do you have access to?"

**What should happen the first time:** your default browser will pop open
asking you to log in to Grafana and authorize the connection. This is the
OAuth 2.1 handshake — expected, not a bug. Approve it, and the agent should
be able to complete the tool call. This authorization is cached and
auto-refreshes for 30 days, so you shouldn't need to repeat it often while
building.

**Alternative:** `adk run backlot_ops_agent` gives you a plain terminal chat
instead of the web UI, if you prefer that.

## 5. What "done" looks like for Week 1

- `adk web` starts without import errors
- You can select the agent and send it a message
- The OAuth popup appears and you can authorize it
- The agent successfully calls at least one Grafana tool and returns a
  real (even if boring/empty) answer — e.g. "you have no dashboards yet"
  is a **success**, because it proves the wire works end to end

Don't worry yet if the agent's answers are unimpressive — your Grafana
stack has no real data in it. That's Week 2's job (the synthetic render-farm
data generator). Week 1 is purely about proving connectivity.

## 6. If something breaks

- **Import errors on `MCPToolset` / `StreamableHTTPConnectionParams`** —
  version mismatch is the most common cause. Confirm with
  `pip show google-cloud-aiplatform` that you have `>=1.101.0`.
- **OAuth popup never appears / times out** — check that you (the account
  creator) have Editor role or higher on your Grafana stack, and that
  you already accepted the Grafana Assistant terms in the Grafana Cloud
  UI once, per the hackathon resource page.
- **`GOOGLE_CLOUD_PROJECT` errors** — double check `gcloud config list`
  shows the right project, and that the Vertex AI API is enabled for it
  (`gcloud services enable aiplatform.googleapis.com`).

## Next up (Week 2)

Once this connects reliably, next we build the synthetic render-farm /
encode-pipeline data generator so the agent actually has something
interesting to look at.
