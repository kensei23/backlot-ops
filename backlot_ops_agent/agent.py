"""
Backlot Ops - an on-call technical producer agent for a media studio's
render farm and encode pipeline, built on Google ADK and Grafana Cloud.
"""

import os
import shutil
import asyncio
import requests
from dotenv import load_dotenv

from mcp import StdioServerParameters

from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import MCPToolset, StdioConnectionParams
from google.genai import types as genai_types

load_dotenv()

GRAFANA_STACK_URL = os.environ["GRAFANA_STACK_URL"]
GRAFANA_SERVICE_ACCOUNT_TOKEN = os.environ["GRAFANA_SERVICE_ACCOUNT_TOKEN"]

# Real datasource UIDs for this stack, found via the Grafana API. Note
# these are short ("grafanacloud-prom"), not the longer display name
# ("grafanacloud-<stack>-prom") - easy to mix up.
PROMETHEUS_DATASOURCE_UID = "grafanacloud-prom"
LOKI_DATASOURCE_UID = "grafanacloud-logs"


def _find_uvx() -> str:
    """Locate the uvx executable. shutil.which() isn't always reliable
    across shells on Windows, so fall back to uv's default install paths.
    """
    found = shutil.which("uvx")
    if found:
        return found
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", "uvx.exe"),
        os.path.join(home, ".local", "bin", "uvx"),
        os.path.join(home, ".cargo", "bin", "uvx.exe"),
        os.path.join(home, ".cargo", "bin", "uvx"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return "uvx"


UVX_PATH = _find_uvx()

# Connects to mcp-grafana via uvx using a service account token.
# We wrap the raw MCP tools in standard Python functions below to give Gemini 
# a simpler schema and prevent it from hallucinating arguments.
grafana_mcp = MCPToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=UVX_PATH,
            args=["mcp-grafana"],
            env={
                "GRAFANA_URL": GRAFANA_STACK_URL,
                "GRAFANA_SERVICE_ACCOUNT_TOKEN": GRAFANA_SERVICE_ACCOUNT_TOKEN,
            },
        ),
        timeout=60.0,  # first run downloads mcp-grafana, so default is too short
    ),
    tool_filter=["query_prometheus", "query_loki_logs"],
)


async def _call_mcp_tool(name: str, args: dict) -> str:
    """Look up a Grafana MCP tool by name and invoke it, returning a
    plain string the model can read.
    """
    tools = await grafana_mcp.get_tools()
    match = next((t for t in tools if t.name == name), None)
    if match is None:
        return f"Error: Grafana tool '{name}' is not available."

    result = await match.run_async(args=args, tool_context=None)

    # MCP results are usually an object with a .content list of parts,
    # each carrying a .text field - flatten that into plain text.
    try:
        content = getattr(result, "content", None)
        if content:
            texts = [getattr(part, "text", "") for part in content]
            joined = "\n".join(t for t in texts if t)
            if joined:
                return joined
        return str(result)
    except Exception as e:
        return f"Error reading tool result: {e}"


async def query_render_farm_metric(promql_expression: str) -> str:
    """Query a live metric value from the render farm's Prometheus data
    source using a PromQL expression.

    Args:
        promql_expression: e.g. render_farm_cpu_percent,
            render_farm_queue_depth, encode_pipeline_fps, or
            job_stats_jobs_failed_total.
    """
    return await _call_mcp_tool(
        "query_prometheus",
        {
            "datasourceUid": PROMETHEUS_DATASOURCE_UID,
            "expr": promql_expression,
            "queryType": "instant",
            "endTime": "now",
        },
    )


async def query_render_farm_logs(logql_expression: str) -> str:
    """Query recent logs from the render farm using a LogQL expression,
    e.g. {job="render-farm"}.
    """
    return await _call_mcp_tool(
        "query_loki_logs",
        {
            "datasourceUid": LOKI_DATASOURCE_UID,
            "logql": logql_expression,
            # Without this, the tool defaults to searching the last hour,
            # so a resolved incident's error logs stay visible and get
            # reported as still-ongoing long after it actually cleared.
            "startRfc3339": "now-3m",
            "endRfc3339": "now",
        },
    )

async def create_grafana_annotation(message: str) -> str:
    """Create an annotation on the Grafana dashboard to mark an ongoing incident.
    
    Args:
        message: A short, plain English description of the incident to display on the graph.
    """
    def _post_annotation():
        url = f"{GRAFANA_STACK_URL.rstrip('/')}/api/annotations"
        headers = {
            "Authorization": f"Bearer {GRAFANA_SERVICE_ACCOUNT_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": f"🤖 Agent Alert: {message}",
            "tags": ["render-farm", "incident"]
        }
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    try:
        # Run the blocking requests call in a background thread so it doesn't freeze the async agent loop
        await asyncio.to_thread(_post_annotation)
        return "Successfully created Grafana annotation."
    except Exception as e:
        return f"Failed to create annotation: {e}"

line_producer_agent = Agent(
    model="gemini-2.5-flash",
    name="line_producer_agent",
    instruction=(
        "You are a line producer at a media studio. You'll be given a "
        "technical incident report about the render farm or encode "
        "pipeline. Your job is business judgment, not more technical "
        "diagnosis - you're deciding what this means for the production "
        "schedule and who needs to know.\n\n"
        "Respond with exactly two parts:\n"
        "SEVERITY: one word - Low, Medium, High, or Critical.\n"
        "MESSAGE: a short, professional 2-3 sentence message suitable "
        "for sending to a production team, explaining the impact and "
        "what you'd recommend (e.g. proceed as planned, monitor "
        "closely, or delay delivery). Do not use technical jargon like "
        "GPU, CPU, or API - write for a non-technical audience."
    ),
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.2,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    ),
)

root_agent = Agent(
    model="gemini-2.5-flash",
    name="backlot_ops_agent",
    instruction=(
        "You are an on-call technical producer for a media studio's "
        "render farm and encode pipeline. Always check real Grafana data "
        "before answering any question about system state - never guess "
        "or make up numbers.\n\n"
        "To check metrics: call query_render_farm_metric with a PromQL "
        "expression.\n\n"
        "To check logs: call query_render_farm_logs with a LogQL "
        "expression using only simple label matching in curly braces - "
        "never use pipe filters like |~ or regex, they can cause errors. "
        "Use {job=\"render-farm\"} for all logs, "
        "{job=\"render-farm\", level=\"error\"} for just errors, or "
        "{job=\"render-farm\", level=\"info\"} for just info messages. "
        "This only searches the last few minutes, so an error you see "
        "reflects the current situation, not old history.\n\n"
        "If you find a genuine, current incident (not just a past one "
        "that's already cleared), you must do TWO things:\n"
        "1. Call create_grafana_annotation with a short summary of the issue to mark the graph.\n"
        "2. Call the line_producer_agent tool with a one-sentence summary to get a severity rating and stakeholder message.\n\n"
        "Include the line producer's severity and message in your final answer alongside your own "
        "technical explanation. Don't call line_producer_agent or create_grafana_annotation if "
        "everything is healthy.\n\n"
        "Explain results in plain, non-technical language."
    ),
    tools=[
        FunctionTool(query_render_farm_metric),
        FunctionTool(query_render_farm_logs),
        FunctionTool(create_grafana_annotation),
        AgentTool(agent=line_producer_agent),
    ],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.1,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    ),
)
