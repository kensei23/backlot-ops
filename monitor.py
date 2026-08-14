"""
Backlot Ops - proactive monitor.

Periodically asks the agent to check the render farm's health. Stays
quiet when everything's fine, and prints a clear alert only when it
detects a problem.

Run alongside simulator.py and adk web. Ctrl+C to stop.
"""

import asyncio
import os
import re
from datetime import datetime, timezone

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from backlot_ops_agent.agent import root_agent
from tts import text_to_speech

CHECK_INTERVAL_SECONDS = 30

CHECK_PROMPT = (
    "Check the render farm's current CPU usage, queue depth, and recent "
    "logs for errors. If everything looks normal, reply with only the "
    "single word HEALTHY and nothing else. If you find a real problem, "
    "start your reply with INCIDENT: followed by a 2-3 sentence plain "
    "English explanation of what's wrong and likely why."
)


async def check_once(runner: InMemoryRunner) -> str:
    """Run one health check in a brand new session, and return the
    agent's final text response. A fresh session each time means each
    check is judged purely on current data - not colored by what was
    said in previous checks.
    """
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id="monitor"
    )
    message = genai_types.Content(
        role="user", parts=[genai_types.Part(text=CHECK_PROMPT)]
    )
    final_text = ""
    async for event in runner.run_async(
        user_id="monitor", session_id=session.id, new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text or ""
    return final_text.strip()


def extract_stakeholder_message(text: str) -> str:
    """Pull out just the Line Producer's MESSAGE: line, since that's the
    part meant to be spoken aloud - not the full technical explanation.
    Falls back to the full text if the expected format isn't found.
    """
    match = re.search(r"MESSAGE:\s*(.+)", text, re.DOTALL)
    return match.group(1).strip() if match else text


async def main():
    runner = InMemoryRunner(agent=root_agent)

    print("Backlot Ops monitor started.")
    print(f"Checking every {CHECK_INTERVAL_SECONDS} seconds.")
    print("Press Ctrl+C to stop.\n")

    while True:
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = await check_once(runner)
        except Exception as e:
            print(f"[{now}] check failed: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            continue

        if result.upper().startswith("INCIDENT"):
            print(f"\n{'=' * 60}")
            print(f"🚨 PROACTIVE ALERT - {now}")
            print(result)
            print(f"{'=' * 60}\n")

            try:
                message = extract_stakeholder_message(result)
                audio_path = text_to_speech(message, output_path="alert.wav")
                print(f"🔊 Playing spoken alert...")
                os.startfile(audio_path)  # Windows-only: opens with default player
            except Exception as e:
                print(f"  [tts] could not generate/play audio: {e}")
        else:
            print(f"[{now}] status: healthy")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
