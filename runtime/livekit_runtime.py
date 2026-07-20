"""LiveKit-backed Gauntlet conversation runtime.

The controller is deliberately an observer. It joins before workers are
dispatched so it can receive their real-time transcript events, then returns
the same transcript shape consumed by the pre-existing evaluator.
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field

from dispatch_room import dispatch_run
from dotenv import load_dotenv
from livekit import api, rtc

from .base import ConversationRuntime

load_dotenv("my-agent/.env.local")


@dataclass
class _RunTranscript:
    events: list[tuple[str, str]] = field(default_factory=list)
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    all_utterances_received: asyncio.Event = field(default_factory=asyncio.Event)
    expected_utterances: int | None = None
    error: str | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)


class LiveKitRuntime(ConversationRuntime):
    def __init__(self, timeout_seconds: float = 600.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(self, persona: dict, agent_prompt: str, turns: int = 6) -> dict:
        run_id = uuid.uuid4().hex
        room_name = f"gauntlet-{run_id[:12]}"
        controller_identity = f"gauntlet-controller-{run_id[:12]}"
        transcript = _RunTranscript()
        room = rtc.Room()

        room.register_text_stream_handler(
            "gauntlet.event",
            lambda reader, identity: self._schedule_event(
                transcript, reader, identity, controller_identity, run_id
            ),
        )

        await room.connect(
            os.environ["LIVEKIT_URL"],
            self._observer_token(room_name, controller_identity),
        )
        try:
            await dispatch_run(
                room_name,
                {
                    "run_id": run_id,
                    "persona": persona,
                    "max_turns": turns,
                    "target_identity": "my-agent",
                    "controller_identity": controller_identity,
                },
                {"agent_prompt": agent_prompt},
            )
            await asyncio.wait_for(transcript.complete.wait(), self.timeout_seconds)
            if transcript.error:
                raise RuntimeError(f"Caller failed: {transcript.error}")
            if transcript.expected_utterances is not None:
                await asyncio.wait_for(
                    transcript.all_utterances_received.wait(), timeout=15.0
                )
            if not transcript.events:
                raise RuntimeError(
                    "Caller completed without publishing transcript events"
                )
            print(
                "[gauntlet] controller received "
                f"{len(transcript.events)} final transcript entries"
            )
            return {
                "transcript": transcript.events,
                "metadata": {"run_id": run_id, "room": room_name, "runtime": "livekit"},
            }
        finally:
            for task in transcript.tasks:
                task.cancel()
            await room.disconnect()

    def _observer_token(self, room_name: str, identity: str) -> str:
        token = api.AccessToken(
            os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
        )
        return (
            token.with_identity(identity)
            .with_name("Gauntlet transcript collector")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=False,
                    can_subscribe=True,
                    can_publish_data=False,
                )
            )
            .to_jwt()
        )

    def _schedule_event(
        self,
        transcript: _RunTranscript,
        reader: rtc.TextStreamReader,
        participant_identity: str,
        controller_identity: str,
        run_id: str,
    ) -> None:
        task = asyncio.create_task(
            self._consume_event(
                transcript, reader, participant_identity, controller_identity, run_id
            )
        )
        transcript.tasks.add(task)
        task.add_done_callback(transcript.tasks.discard)

    async def _consume_event(
        self,
        transcript: _RunTranscript,
        reader: rtc.TextStreamReader,
        participant_identity: str,
        controller_identity: str,
        run_id: str,
    ) -> None:
        if participant_identity == controller_identity:
            return
        event = json.loads(await reader.read_all())
        if event.get("run_id") != run_id:
            return
        print(
            "[gauntlet] event received: "
            f"kind={event.get('kind')}, keys={sorted(event.keys())}"
        )
        if event.get("kind") == "utterance":
            transcript.events.append((str(event["speaker"]), str(event["text"])))
            self._mark_if_complete(transcript)
        elif event.get("kind") == "failed":
            transcript.error = str(event.get("error") or "unknown caller error")
            transcript.complete.set()
        elif event.get("kind") == "complete":
            if event.get("protocol_version") != 2:
                transcript.error = (
                    "The gauntlet-caller worker is running stale code. Stop every "
                    "caller dev process, then start it again from tester-agent/src/agent.py."
                )
                transcript.complete.set()
                return
            final_transcript = event.get("transcript")
            if isinstance(final_transcript, list):
                transcript.events = [
                    (str(item["speaker"]), str(item["text"]))
                    for item in final_transcript
                    if isinstance(item, dict) and "speaker" in item and "text" in item
                ]
            print(
                "[gauntlet] completion payload: "
                f"protocol={event.get('protocol_version')}, "
                f"expected={event.get('expected_utterances')}, "
                f"entries={len(transcript.events)}"
            )
            transcript.expected_utterances = int(event.get("expected_utterances", 0))
            self._mark_if_complete(transcript)
            transcript.complete.set()

    @staticmethod
    def _mark_if_complete(transcript: _RunTranscript) -> None:
        if (
            transcript.expected_utterances is not None
            and len(transcript.events) >= transcript.expected_utterances
        ):
            transcript.all_utterances_received.set()
