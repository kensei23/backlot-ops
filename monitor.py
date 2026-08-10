"""
Backlot Ops - proactive monitor.

Periodically asks the agent to check the render farm's health. Stays
quiet when everything's fine, and prints a clear alert only when it
detects a real problem - this is the "catches it before you ask" piece
of the demo, separate from the interactive chat in adk web.

Run alongside simulator.py and adk web. Ctrl+C to stop.
"""

import asyncio
from datetime import datetime, timezone

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from backlot_ops_agent.agent import root_agent

CHECK_INTERVAL_SECONDS = 30

CHECK_PROMPT = (
    "Check the render farm's current CPU usage, queue depth, and recent "
    "logs for errors. If everything looks normal, reply with only the "
    "single word HEALTHY and nothing else. If you find a real problem, "
    "start your reply with INCIDENT: followed by a 2-3 sentence plain "
    "English explanation of what's wrong and likely why."
)


async def check_once(runner: InMemoryRunner, session_id: str) -> str:
    """Run one health check and return the agent's final text response."""
    message = genai_types.Content(
        role="user", parts=[genai_types.Part(text=CHECK_PROMPT)]
    )
    final_text = ""
    async for event in runner.run_async(
        user_id="monitor", session_id=session_id, new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text or ""
    return final_text.strip()


async def main():
    runner = InMemoryRunner(agent=root_agent)
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id="monitor"
    )

    print("Backlot Ops monitor started.")
    print(f"Checking every {CHECK_INTERVAL_SECONDS} seconds.")
    print("Press Ctrl+C to stop.\n")

    while True:
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = await check_once(runner, session.id)
        except Exception as e:
            print(f"[{now}] check failed: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            continue

        if result.upper().startswith("INCIDENT"):
            print(f"\n{'=' * 60}")
            print(f"🚨 PROACTIVE ALERT - {now}")
            print(result)
            print(f"{'=' * 60}\n")
        else:
            print(f"[{now}] status: healthy")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
