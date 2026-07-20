"""Create a run room and explicitly dispatch Gauntlet's two workers."""

import json
import os

from dotenv import load_dotenv
from livekit import api

load_dotenv("my-agent/.env.local")

TARGET_AGENT_NAME = "my-agent"
CALLER_AGENT_NAME = "gauntlet-caller-v2"


async def dispatch_run(
    room_name: str, caller_payload: dict, target_payload: dict | None = None
) -> None:
    """Create an isolated room and dispatch its configured target and caller."""
    lkapi = api.LiveKitAPI()
    try:
        await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=TARGET_AGENT_NAME,
                room=room_name,
                metadata=json.dumps(target_payload or {}),
            )
        )
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=CALLER_AGENT_NAME,
                room=room_name,
                metadata=json.dumps(caller_payload),
            )
        )
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    import asyncio

    room = os.getenv("GAUNTLET_ROOM_NAME", "gauntlet-manual-test")
    asyncio.run(
        dispatch_run(
            room,
            {
                "run_id": "manual",
                "persona": {
                    "name": "interrupter",
                    "system_prompt": "Interrupt naturally while remaining on topic.",
                },
                "max_turns": 4,
                "target_identity": "my-agent",
                "controller_identity": "manual-controller",
            },
            {"agent_prompt": "You are a helpful voice assistant."},
        )
    )
