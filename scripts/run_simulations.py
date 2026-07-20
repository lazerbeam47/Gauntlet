"""
run_simulations.py

Automates a full back-and-forth conversation between Tester (with a specific
persona injected) and Ravi, using Groq's free API to play both roles with
their real prompts. Switched from Gemini to Groq because Gemini's free tier
caps out at 20 requests/day for gemini-2.5-flash - nowhere near enough for
a full persona batch. Groq's free tier gives 14,400 requests/day, 30/minute,
which comfortably fits this workload.

Usage:
    python scripts/run_simulations.py --persona logic_prober --turns 8

Output:
    Prints the full conversation as it happens, and saves a transcript to
    data/transcripts/<persona>.txt in the same "Agent: / Caller:" format
    used by score_calls.py, so it can be scored immediately afterward.
"""

import os
import time
import json
import argparse
import asyncio
import sys
from pathlib import Path
from groq import Groq, RateLimitError, APIConnectionError
from dotenv import load_dotenv
import rate_limiter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()
client = Groq(api_key=os.environ["GROQ_API_KEY"])

MODEL_NAME = "llama-3.1-8b-instant"


def send_with_retry(messages: list, max_retries: int = 5) -> str:
    for attempt in range(max_retries):
        rate_limiter.acquire(MODEL_NAME, messages)
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                max_tokens=110,
            )
            return response.choices[0].message.content.strip()
        except RateLimitError:
            wait_time = 60  # TPM/RPM windows are 60s - wait the full window, not a fraction
            print(f"  Rate limit hit, waiting {wait_time}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(wait_time)
        except APIConnectionError:
            wait_time = 5  # likely a brief network blip, not a real rate limit - short retry
            print(f"  Connection error, waiting {wait_time}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(wait_time)
    raise RuntimeError("Exceeded max retries due to rate limiting. Try again in a few minutes.")


def load_text(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def load_persona(persona_name: str, personas_path: str = "config/generated_personas.json") -> dict:
    with open(personas_path, "r") as f:
        personas = json.load(f)
    for p in personas:
        if p["name"] == persona_name:
            return p
    raise ValueError(f"Persona '{persona_name}' not found in {personas_path}")


def build_tester_prompt(persona_system_prompt: str, template_path: str = "config/tester_prompt_template.txt") -> str:
    template = load_text(template_path)
    return template.replace("{persona_instructions}", persona_system_prompt)


CLOSING_SIGNALS = [
    "have a good day",
    "reaching out to you",
    "thank you for your time",
    "आपका दिन अच्छा रहे",
    "contact करेगी",
]


def is_closing_statement(agent_reply: str) -> bool:
    lowered = agent_reply.lower()
    return any(signal.lower() in lowered for signal in CLOSING_SIGNALS)


def trim_history(messages: list, keep_last: int = 8) -> list:
    """Keeps the system prompt plus only the most recent turns, so token
    volume per call stays roughly constant instead of growing every turn -
    this is what was actually blowing past Groq's tokens-per-minute limit
    in longer conversations."""
    system = messages[0]
    recent = messages[1:][-keep_last:]
    return [system] + recent


def run_simulation(persona_name: str, turns: int = 8, agent_prompt_text: str = None) -> list:
    persona = load_persona(persona_name)
    tester_system_prompt = build_tester_prompt(persona["system_prompt"])
    ravi_system_prompt = agent_prompt_text if agent_prompt_text else load_text("config/ravi_prompt.txt")

    tester_messages = [{"role": "system", "content": tester_system_prompt}]
    ravi_messages = [{"role": "system", "content": ravi_system_prompt}]

    transcript = []

    tester_messages.append({
        "role": "user",
        "content": "Begin the call now. Deliver your opening line as this persona, calling Ravi."
    })
    tester_reply = send_with_retry(tester_messages)
    tester_messages.append({"role": "assistant", "content": tester_reply})
    transcript.append(("Caller", tester_reply))
    print(f"Caller: {tester_reply}\n")

    last_message = tester_reply

    for turn in range(turns):
        ravi_messages.append({"role": "user", "content": last_message})
        ravi_messages = trim_history(ravi_messages)
        ravi_reply = send_with_retry(ravi_messages)
        ravi_messages.append({"role": "assistant", "content": ravi_reply})
        transcript.append(("Agent", ravi_reply))
        print(f"Agent: {ravi_reply}\n")

        if is_closing_statement(ravi_reply):
            print(f"  [Call ended naturally - Ravi delivered a closing statement at turn {turn + 1}]\n")
            break

        tester_messages.append({"role": "user", "content": ravi_reply})
        tester_messages = trim_history(tester_messages)
        tester_reply = send_with_retry(tester_messages)
        tester_messages.append({"role": "assistant", "content": tester_reply})
        transcript.append(("Caller", tester_reply))
        print(f"Caller: {tester_reply}\n")

        last_message = tester_reply
    else:
        print(f"  [Reached max turn limit ({turns}) without a natural closing - stopping here]\n")

    return transcript


def run_livekit_simulation(
    persona_name: str, turns: int = 8, agent_prompt_text: str = None
) -> list:
    """Run a persona against the deployed voice target over LiveKit."""
    from runtime import LiveKitRuntime

    persona = load_persona(persona_name)
    result = asyncio.run(
        LiveKitRuntime().run(
            persona=persona,
            agent_prompt=agent_prompt_text or load_text("config/ravi_prompt.txt"),
            turns=turns,
        )
    )
    return result["transcript"]


def save_transcript(transcript: list, persona_name: str, output_dir: str = "data/transcripts") -> str:
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{persona_name}.txt")
    with open(output_path, "w") as f:
        for speaker, line in transcript:
            f.write(f"{speaker}: {line}\n")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True, help="Persona name from config/generated_personas.json")
    parser.add_argument("--turns", type=int, default=8, help="Number of back-and-forth turns to simulate")
    parser.add_argument(
        "--runtime", choices=("text", "livekit"), default="text", help="Simulation transport"
    )
    args = parser.parse_args()

    print(f"Simulating call: Tester (as {args.persona}) vs Ravi\n{'='*60}\n")
    runner = run_livekit_simulation if args.runtime == "livekit" else run_simulation
    transcript = runner(args.persona, args.turns)

    output_path = save_transcript(transcript, args.persona)
    print(f"{'='*60}\nSaved transcript to {output_path}")
