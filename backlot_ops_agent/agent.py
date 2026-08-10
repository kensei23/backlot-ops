"""
Backlot Ops - an on-call technical producer agent for a media studio's
render farm and encode pipeline, built on Google ADK and Grafana Cloud.
"""

import os
import shutil
from dotenv import load_dotenv

from mcp import StdioServerParameters

from google.adk.agents import Agent
from google.adk.tools import FunctionTool
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

# Connects to the open-source mcp-grafana server via uvx, using a service
# account token rather than the hosted server's OAuth flow - simpler to
# run headless and works after deployment, where there's no browser to
# complete an OAuth login.
#
# Kept as an internal connection only, not passed directly into the
# agent's tools list. Exposing Grafana's raw MCP tool schemas to Gemini
# caused it to occasionally emit malformed, code-shaped pseudo tool calls
# instead of real ones. Wrapping each tool in a plain Python function
# below gives Gemini a simpler schema to work with instead.
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
        "Explain results in plain, non-technical language."
    ),
    tools=[
        FunctionTool(query_render_farm_metric),
        FunctionTool(query_render_farm_logs),
    ],
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.1,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    ),
)
