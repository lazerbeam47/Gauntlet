"""Dispatched programmable voice caller for Gauntlet.

This worker deliberately does not create an AgentSession.  It joins a room as
an ordinary programmable LiveKit participant, synthesizes the caller's speech
locally, and publishes that speech as a microphone PCM track.
"""

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentServer, JobContext, cli

logger = logging.getLogger("gauntlet-caller")

load_dotenv(".env.local")

SAMPLE_RATE = int(os.getenv("GAUNTLET_TTS_SAMPLE_RATE", "24000"))
CHANNELS = 1
FRAME_MS = 20
CALLER_PROTOCOL_VERSION = 2
CALLER_AGENT_NAME = "gauntlet-caller-v2"


@dataclass(frozen=True)
class CallSpec:
    run_id: str
    persona_name: str
    persona_prompt: str
    max_turns: int
    target_identity: str
    controller_identity: str


def parse_call_spec(metadata: str) -> CallSpec:
    """Validate the per-run payload supplied by the agent dispatch."""
    payload = json.loads(metadata or "{}")
    persona = payload.get("persona") or {}
    required = {
        "run_id": payload.get("run_id"),
        "persona.name": persona.get("name"),
        "persona.system_prompt": persona.get("system_prompt"),
        "target_identity": payload.get("target_identity"),
        "controller_identity": payload.get("controller_identity"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Dispatch metadata is missing: {', '.join(missing)}")

    return CallSpec(
        run_id=str(payload["run_id"]),
        persona_name=str(persona["name"]),
        persona_prompt=str(persona["system_prompt"]),
        max_turns=max(1, int(payload.get("max_turns", 6))),
        target_identity=str(payload["target_identity"]),
        controller_identity=str(payload["controller_identity"]),
    )


def chunk_pcm(
    samples: Iterable[int], sample_rate: int, frame_ms: int = FRAME_MS
) -> Iterable[list[int]]:
    """Yield fixed-size PCM frames, padding only the final partial frame."""
    frame_samples = sample_rate * frame_ms // 1000
    data = list(samples)
    for offset in range(0, len(data), frame_samples):
        frame = data[offset : offset + frame_samples]
        if len(frame) < frame_samples:
            frame += [0] * (frame_samples - len(frame))
        yield frame


class GroqCaller:
    """Small LLM adapter so LiveKit transport stays independent of the LLM."""

    def __init__(self, spec: CallSpec) -> None:
        self._spec = spec
        self._history: list[tuple[str, str]] = []
        self._client = None

    async def next_utterance(self, target_reply: str | None) -> str:
        if target_reply:
            self._history.append(("Agent", target_reply))

        prompt = self._build_prompt()
        text = await asyncio.to_thread(self._generate, prompt)
        text = " ".join(text.split())
        if not text:
            raise RuntimeError("Groq returned an empty caller utterance")
        self._history.append(("Caller", text))
        return text

    def _build_prompt(self) -> str:
        history = "\n".join(f"{speaker}: {text}" for speaker, text in self._history[-10:])
        phase = "Open the call naturally." if not self._history else "Reply to the agent's latest speech."
        return f"""You are the caller in an authorized voice-agent stress test.

Persona name: {self._spec.persona_name}
Persona instructions:
{self._spec.persona_prompt}

{phase}
Stay in character. Return only one short, speakable utterance (one or two sentences), with no labels, markdown, or quotation marks. If the call is clearly complete, return exactly END_CALL.

Conversation so far:
{history or '(no conversation yet)'}"""

    def _generate(self, prompt: str) -> str:
        if self._client is None:
            from groq import Groq

            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise RuntimeError("GROQ_API_KEY is required by the caller worker")
            self._client = Groq(api_key=api_key)
        response = self._client.chat.completions.create(
            model=os.getenv("GAUNTLET_GROQ_MODEL", "llama-3.1-8b-instant"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=110,
        )
        return response.choices[0].message.content or ""


class KokoroPcmTTS:
    """Lazy Kokoro adapter returning signed-16-bit mono PCM in memory."""

    def __init__(self) -> None:
        self._pipeline = None

    def synthesize(self, text: str) -> np.ndarray:
        if self._pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise RuntimeError(
                    "Kokoro is not installed in this worker environment. Install a "
                    "Kokoro build compatible with the deployment platform, or replace "
                    "KokoroPcmTTS with a Piper adapter."
                ) from exc
            self._pipeline = KPipeline(lang_code=os.getenv("GAUNTLET_TTS_LANG", "a"))

        pieces = []
        voice = os.getenv("GAUNTLET_TTS_VOICE", "af_heart")
        for _, _, audio in self._pipeline(text, voice=voice):
            pieces.append(np.asarray(audio, dtype=np.float32))
        if not pieces:
            raise RuntimeError("Kokoro generated no audio")
        pcm = np.concatenate(pieces)
        # Kokoro emits 24 kHz. Keep that native sample rate; do not create files.
        if SAMPLE_RATE != 24_000:
            raise RuntimeError("Kokoro currently requires GAUNTLET_TTS_SAMPLE_RATE=24000")
        return np.clip(pcm * 32767, -32768, 32767).astype(np.int16)


class PiperPcmTTS:
    """Piper CLI fallback; it writes raw PCM to stdout, never a media file."""

    def synthesize(self, text: str) -> np.ndarray:
        model = os.getenv("GAUNTLET_PIPER_MODEL")
        if not model:
            raise RuntimeError("GAUNTLET_PIPER_MODEL is required when using Piper")
        try:
            result = subprocess.run(
                ["piper", "--model", model, "--output_raw"],
                input=text.encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Piper CLI is not installed or not on PATH") from exc
        if len(result.stdout) % 2:
            raise RuntimeError("Piper returned malformed 16-bit PCM")
        return np.frombuffer(result.stdout, dtype=np.int16).copy()


def create_tts() -> KokoroPcmTTS | PiperPcmTTS:
    engine = os.getenv("GAUNTLET_TTS_ENGINE", "kokoro").lower()
    if engine == "kokoro":
        return KokoroPcmTTS()
    if engine == "piper":
        return PiperPcmTTS()
    raise ValueError("GAUNTLET_TTS_ENGINE must be 'kokoro' or 'piper'")


class TargetTranscriptCollector:
    def __init__(self, room: rtc.Room, target_identity: str) -> None:
        self._room = room
        # The dispatch agent name (for example, "my-agent") is not the same
        # as the generated LiveKit participant identity ("agent-AJ_...").
        self._target_agent_name = target_identity
        self._reply_parts: list[str] = []
        self._reply_ready = asyncio.Event()
        self._target_spoke = asyncio.Event()
        self._target_finished = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        room.register_text_stream_handler("lk.transcription", self._on_transcription)

        @room.on("participant_attributes_changed")
        def on_attributes_changed(changed: dict[str, str], participant: rtc.Participant) -> None:
            logger.info(
                "attributes_changed for %s: %s (is_target=%s)",
                participant.identity, changed, self._is_target(participant)
            )
            if not self._is_target(participant):
                return
            state = changed.get("lk.agent.state")
            logger.info("target agent.state = %r", state)
            if state == "speaking":
                self._target_spoke.set()
            elif state == "listening" and self._target_spoke.is_set():
                self._target_finished.set()

    def _on_transcription(
        self, reader: rtc.TextStreamReader, participant_identity: str
    ) -> None:
        task = asyncio.create_task(self._consume_transcription(reader, participant_identity))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_transcription(
        self, reader: rtc.TextStreamReader, participant_identity: str
    ) -> None:
        participant = self._room.remote_participants.get(participant_identity)
        if participant is None or not self._is_target(participant):
            return
        text = await reader.read_all()
        if reader.info.attributes.get("lk.transcription_final") != "true":
            return
        if text.strip():
            self._reply_parts.append(text.strip())
            self._reply_ready.set()

    def _is_target(self, participant: rtc.Participant) -> bool:
        attributes = participant.attributes
        return (
            participant.identity == self._target_agent_name
            or attributes.get("lk.agent.name") == self._target_agent_name
            or attributes.get("lk.agent_name") == self._target_agent_name
        )

    def begin_turn(self) -> None:
        """Reset reply state before caller audio is published, not afterwards."""
        self._reply_parts.clear()
        self._reply_ready.clear()
        self._target_spoke.clear()
        self._target_finished.clear()

    async def wait_for_reply(self, timeout: float = 45.0) -> str:
        await asyncio.wait_for(self._target_spoke.wait(), timeout=timeout)
        await asyncio.wait_for(self._target_finished.wait(), timeout=timeout)
        # Speech-aligned transcripts can arrive just after the state transition.
        await asyncio.wait_for(self._reply_ready.wait(), timeout=20.0)
        return " ".join(self._reply_parts)


class PcmPublisher:
    def __init__(self, ctx: JobContext) -> None:
        self._source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        self._track = rtc.LocalAudioTrack.create_audio_track(
            "gauntlet-caller-mic", self._source
        )
        self._ctx = ctx

    async def start(self) -> None:
        await self._ctx.room.local_participant.publish_track(
            self._track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )

    async def publish(self, pcm: np.ndarray) -> None:
        for samples in chunk_pcm(pcm.tolist(), SAMPLE_RATE):
            frame = rtc.AudioFrame(
                data=np.asarray(samples, dtype=np.int16).tobytes(),
                sample_rate=SAMPLE_RATE,
                num_channels=CHANNELS,
                samples_per_channel=len(samples),
            )
            await self._source.capture_frame(frame)


async def emit_event(ctx: JobContext, spec: CallSpec, kind: str, **payload: object) -> None:
    event = json.dumps({"run_id": spec.run_id, "kind": kind, **payload})
    await ctx.room.local_participant.send_text(
        event,
        topic="gauntlet.event",
        destination_identities=[spec.controller_identity],
    )


server = AgentServer()


@server.rtc_session(agent_name=CALLER_AGENT_NAME)
async def gauntlet_caller(ctx: JobContext) -> None:
    spec = parse_call_spec(ctx.job.metadata)
    await ctx.connect()

    publisher = PcmPublisher(ctx)
    await publisher.start()
    collector = TargetTranscriptCollector(ctx.room, spec.target_identity)
    brain = GroqCaller(spec)
    tts = create_tts()

    try:
        target_reply: str | None = None
        emitted_utterances = 0
        transcript: list[dict[str, str]] = []
        for turn in range(spec.max_turns):
            caller_text = await brain.next_utterance(target_reply)
            if caller_text == "END_CALL":
                break
            await emit_event(ctx, spec, "utterance", speaker="Caller", text=caller_text)
            emitted_utterances += 1
            transcript.append({"speaker": "Caller", "text": caller_text})
            # Kokoro's first model load can take minutes. Keep it off the
            # LiveKit job event loop so the worker heartbeat stays responsive.
            collector.begin_turn()
            pcm = await asyncio.to_thread(tts.synthesize, caller_text)
            await publisher.publish(pcm)

            target_reply = await collector.wait_for_reply()
            await emit_event(ctx, spec, "utterance", speaker="Agent", text=target_reply)
            emitted_utterances += 1
            transcript.append({"speaker": "Agent", "text": target_reply})
            logger.info("completed caller turn %s/%s", turn + 1, spec.max_turns)
        await emit_event(
            ctx,
            spec,
            "complete",
            protocol_version=CALLER_PROTOCOL_VERSION,
            expected_utterances=emitted_utterances,
            transcript=transcript,
        )
        logger.info("published completion with %s transcript entries", len(transcript))
    except asyncio.TimeoutError:
        error = "Timed out waiting for the target agent response"
        logger.exception(error)
        await emit_event(ctx, spec, "failed", error=error)
        raise
    except Exception as exc:
        logger.exception("Gauntlet caller failed")
        await emit_event(ctx, spec, "failed", error=str(exc) or type(exc).__name__)
        raise


if __name__ == "__main__":
    cli.run_app(server)
