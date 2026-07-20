"""
run_all_simulations.py

Day 2/3 script. Loops through every persona in config/generated_personas.json
automatically, running a full Tester-vs-Ravi simulation for each one in turn,
using the same engine as run_simulations.py. This is the "batch" version -
run it once, walk away, and come back to a full folder of transcripts.

Usage:
    python scripts/run_all_simulations.py --turns 8

Output:
    One transcript file per persona in data/transcripts/, plus a summary
    printed at the end showing how many completed successfully.

Note on timing: given the free tier's rate limit, each persona's simulation
takes several minutes. Running all 11 personas back to back will take
roughly 45-60 minutes. This is expected - let it run in the background.
"""

import os
import json
import argparse
import time
from run_simulations import run_livekit_simulation, run_simulation, save_transcript


def load_all_personas(personas_path: str = "config/generated_personas.json") -> list:
    with open(personas_path, "r") as f:
        return json.load(f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=8, help="Max turns per persona simulation")
    parser.add_argument(
        "--personas_path",
        default="config/generated_personas.json",
        help="Path to the personas file to loop through",
    )
    parser.add_argument(
        "--runtime", choices=("text", "livekit"), default="text", help="Simulation transport"
    )
    args = parser.parse_args()

    personas = load_all_personas(args.personas_path)
    print(f"Found {len(personas)} personas. Running simulations one by one...\n")
    print("This will take a while due to free-tier rate limits - let it run.\n")

    completed = []
    failed = []

    for i, persona in enumerate(personas, start=1):
        name = persona["name"]
        print(f"\n{'#'*60}")
        print(f"# Persona {i}/{len(personas)}: {name}")
        print(f"{'#'*60}\n")

        try:
            runner = run_livekit_simulation if args.runtime == "livekit" else run_simulation
            transcript = runner(name, args.turns)
            output_path = save_transcript(transcript, name)
            print(f"Saved: {output_path}")
            completed.append(name)
        except Exception as e:
            print(f"FAILED on persona '{name}': {e}")
            failed.append(name)
            # Brief pause before moving to the next persona even after a failure
            time.sleep(15)

    print(f"\n{'='*60}")
    print(f"Done. {len(completed)}/{len(personas)} personas completed successfully.")
    if completed:
        print(f"Completed: {', '.join(completed)}")
    if failed:
        print(f"Failed (retry these individually with run_simulations.py): {', '.join(failed)}")
