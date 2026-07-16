"""
app.py

Local backend server that connects the HTML dashboard to your real Python
pipeline. Run this, then open the dashboard in your browser - every action
in the UI calls your actual generate_personas.py / run_simulations.py /
score_calls.py / report.py functions, using your own Groq key, and writes
real files into data/transcripts/, data/scores/, and reports/.

Usage:
    pip3 install flask flask-cors
    python3 app.py

Then open dashboard.html directly in your browser (double-click it, or
visit http://localhost:5000 if you serve it - see bottom of this file).
"""

import os
import sys
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

sys.path.insert(0, "scripts")

from generate_personas import generate_personas, merge_with_universal
from run_simulations import run_simulation, save_transcript
from score_calls import score_transcript
from report import load_all_scores, build_report
from generate_findings import generate_findings, build_findings_markdown

app = Flask(__name__)
CORS(app)


@app.route("/")
def serve_dashboard():
    try:
        with open("dashboard.html", "r") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "dashboard.html not found. Make sure it's in the same folder as "
            "main.py (your project root), not inside scripts/.",
            404,
        )


@app.route("/api/generate_personas", methods=["POST"])
def api_generate_personas():
    data = request.json
    agent_prompt = data.get("agent_prompt", "")
    if not agent_prompt:
        return jsonify({"error": "agent_prompt is required"}), 400

    generated = generate_personas(agent_prompt)
    merged = merge_with_universal(generated, "config/universal_personas.json")

    os.makedirs("config", exist_ok=True)
    with open("config/generated_personas.json", "w") as f:
        json.dump(merged, f, indent=2)

    return jsonify(merged)


@app.route("/api/run_persona", methods=["POST"])
def api_run_persona():
    data = request.json
    persona_name = data.get("persona_name")
    agent_prompt = data.get("agent_prompt", "")
    turns = data.get("turns", 6)

    if not persona_name or not agent_prompt:
        return jsonify({"error": "persona_name and agent_prompt are required"}), 400

    # Need the persona's own system_prompt to run it - load from what
    # generate_personas already saved to config/generated_personas.json
    with open("config/generated_personas.json", "r") as f:
        all_personas = json.load(f)
    persona_obj = next((p for p in all_personas if p["name"] == persona_name), None)
    if not persona_obj:
        return jsonify({"error": f"persona '{persona_name}' not found"}), 404

    transcript_pairs = run_simulation(persona_name, turns, agent_prompt_text=agent_prompt)
    save_transcript(transcript_pairs, persona_name)

    transcript_text = "\n".join(f"{speaker}: {line}" for speaker, line in transcript_pairs)
    scores = score_transcript(transcript_text, approved_knowledge_text=agent_prompt)

    os.makedirs("data/scores", exist_ok=True)
    with open(f"data/scores/{persona_name}.json", "w") as f:
        json.dump({"persona": persona_name, **scores}, f, indent=2)

    return jsonify({
        "persona": persona_name,
        "transcript": [{"speaker": s, "text": t} for s, t in transcript_pairs],
        "scores": scores,
    })


@app.route("/api/generate_report", methods=["POST"])
def api_generate_report():
    scores = load_all_scores()
    if not scores:
        return jsonify({"error": "no scores found in data/scores/"}), 400

    report_text = build_report(scores)

    os.makedirs("reports", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"reports/run_{timestamp}.md"
    with open(output_path, "w") as f:
        f.write(report_text)

    return jsonify({"report": report_text, "path": output_path})


@app.route("/api/generate_findings", methods=["POST"])
def api_generate_findings():
    scores = load_all_scores()
    if not scores:
        return jsonify({"error": "no scores found in data/scores/"}), 400

    findings = generate_findings(scores)
    findings_md = build_findings_markdown(findings, len(scores))

    os.makedirs("reports", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"reports/findings_{timestamp}.md"
    with open(output_path, "w") as f:
        f.write(findings_md)

    return jsonify({"findings": findings, "markdown": findings_md, "path": output_path})


if __name__ == "__main__":
    print("Gauntlet running at http://localhost:5000")
    print("Open that URL in your browser (do not open dashboard.html directly).")
    app.run(port=5000, debug=True, threaded=True)