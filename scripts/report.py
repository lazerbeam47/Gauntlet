"""
report.py

Reads every score file in data/scores/ (produced by score_all_calls.py) and
compiles them into a single, saved markdown report - a summary table plus
detailed failure reasons - so you have one file to actually show someone,
instead of scrollback in a terminal.

Usage:
    python scripts/report.py

Output:
    Saves a timestamped report to reports/run_<timestamp>.md
"""

import os
import json
import glob
from datetime import datetime

CHECKS = ["hallucination", "overclaim", "interruption_recovery", "obedience", "escalation"]


def load_all_scores(scores_dir: str = "data/scores") -> list:
    score_paths = sorted(glob.glob(os.path.join(scores_dir, "*.json")))
    scores = []
    for path in score_paths:
        with open(path, "r") as f:
            scores.append(json.load(f))
    return scores


def build_report(scores: list) -> str:
    total = len(scores)
    passed = [s for s in scores if s["overall"] == "pass"]
    failed = [s for s in scores if s["overall"] == "fail"]

    lines = []
    lines.append(f"# Voice Agent Stress Test Report")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"**{len(passed)}/{total} personas passed.**")
    lines.append("")

    # Summary table
    lines.append("| Persona | Result | Hallucination | Overclaim | Interruption Recovery | Obedience | Escalation |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in scores:
        row = [s["persona"], s["overall"].upper()]
        for check in CHECKS:
            symbol = "✅" if s[check]["result"] == "pass" else "❌"
            row.append(symbol)
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Detailed failure breakdown
    if failed:
        lines.append("## Failure details")
        lines.append("")
        for s in failed:
            lines.append(f"### {s['persona']}")
            for check in CHECKS:
                if s[check]["result"] == "fail":
                    lines.append(f"- **{check}**: {s[check]['reason']}")
            lines.append("")
    else:
        lines.append("## No failures found across all personas.")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    scores = load_all_scores()

    if not scores:
        print("No scores found in data/scores/. Run score_all_calls.py first.")
        exit()

    report_text = build_report(scores)

    os.makedirs("reports", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"reports/run_{timestamp}.md"

    with open(output_path, "w") as f:
        f.write(report_text)

    print(f"Report saved to {output_path}\n")
    print(report_text)