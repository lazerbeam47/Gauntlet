import json

from agent import CallSpec, chunk_pcm, parse_call_spec


def test_parse_call_spec_reads_dispatch_metadata() -> None:
    metadata = json.dumps(
        {
            "run_id": "run-123",
            "persona": {"name": "interrupter", "system_prompt": "Interrupt."},
            "max_turns": 4,
            "target_identity": "my-agent",
            "controller_identity": "gauntlet-controller-run-123",
        }
    )

    assert parse_call_spec(metadata) == CallSpec(
        run_id="run-123",
        persona_name="interrupter",
        persona_prompt="Interrupt.",
        max_turns=4,
        target_identity="my-agent",
        controller_identity="gauntlet-controller-run-123",
    )


def test_chunk_pcm_uses_exact_20ms_frames_and_drops_no_samples() -> None:
    # 1,001 samples at 24 kHz must become 50 x 480-sample frames, with the
    # final frame padded with silence rather than sending a malformed frame.
    pcm = list(range(1_001))
    frames = list(chunk_pcm(pcm, sample_rate=24_000, frame_ms=20))

    assert len(frames) == 3
    assert all(len(frame) == 480 for frame in frames)
    assert frames[0][0] == 0
    assert frames[2][40] == 1_000
    assert frames[2][41:] == [0] * 439
