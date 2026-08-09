"""
Prints the real input schema for each Grafana MCP tool this project uses.
Handy when a tool call fails and it's unclear which parameters are
actually required - saves guessing.

Usage: python debug_tool_schemas.py
"""

import asyncio
import json
from backlot_ops_agent.agent import grafana_mcp


async def main():
    tools = await grafana_mcp.get_tools()
    for tool in tools:
        print("=" * 60)
        print("TOOL NAME:", tool.name)
        raw = tool.raw_mcp_tool
        print("DESCRIPTION:", raw.description)
        print("INPUT SCHEMA:")
        print(json.dumps(raw.inputSchema, indent=2, default=str))
        print()


if __name__ == "__main__":
    asyncio.run(main())
