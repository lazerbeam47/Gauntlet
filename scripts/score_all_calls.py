"""
score_all_calls.py

Loops through every transcript file in data/transcripts/ and scores each one
automatically, using the exact same scoring logic as score_calls.py. Saves
one score file per persona, same as running score_calls.py by hand for each
- just automated across the whole batch.

Usage:
    python scripts/score_all_calls.py

Output:
    One score JSON per persona in data/scores/, plus a summary printed at
    the end showing overall pass/fail counts across all personas.
"""

import os
import json
import glob
from score_calls import score_transcript


def load_transcript(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


if __name__ == "__main__":
    transcript_paths = sorted(glob.glob("data/transcripts/*.txt"))

    if not transcript_paths:
        print("No transcripts found in data/transcripts/. Run run_all_simulations.py first.")
        exit()

    print(f"Found {len(transcript_paths)} transcripts. Scoring each...\n")

    os.makedirs("data/scores", exist_ok=True)
    results = []

    for path in transcript_paths:
        persona_name = os.path.splitext(os.path.basename(path))[0]
        print(f"Scoring: {persona_name}...")

        transcript_text = load_transcript(path)
        try:
            score = score_transcript(transcript_text)
        except Exception as e:
            print(f"  FAILED to score {persona_name}: {e}\n")
            continue

        output_path = os.path.join("data/scores", f"{persona_name}.json")
        with open(output_path, "w") as f:
            json.dump({"persona": persona_name, **score}, f, indent=2)

        results.append({"persona": persona_name, **score})
        print(f"  Overall: {score['overall']}\n")

    print(f"{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = [r["persona"] for r in results if r["overall"] == "pass"]
    failed = [r["persona"] for r in results if r["overall"] == "fail"]

    print(f"Passed ({len(passed)}): {', '.join(passed) if passed else 'none'}")
    print(f"Failed ({len(failed)}): {', '.join(failed) if failed else 'none'}")

    if failed:
        print(f"\nFailure reasons:")
        for r in results:
            if r["overall"] == "fail":
                failed_checks = [
                    f"{check}: {r[check]['reason']}"
                    for check in ["hallucination", "overclaim", "interruption_recovery", "obedience", "escalation"]
                    if r[check]["result"] == "fail"
                ]
                print(f"\n  {r['persona']}:")
                for fc in failed_checks:
                    print(f"    - {fc}")