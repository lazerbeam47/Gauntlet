# Gauntlet — Voice Agent Stress Testing Tool

Gauntlet is a stress-testing tool for voice AI agents. It automatically generates adversarial callers tailored to a specific agent's domain, runs simulated conversations against that agent, and grades the results for hallucination, overclaiming, guardrail resilience, and correct escalation — catching failures before a real customer does.

---

## The problem this solves

Voice AI agents are easy to demo and hard to trust in production. A script that sounds perfect against a quiet, cooperative test caller often breaks down against a real one: someone who interrupts, mixes languages mid-sentence, pushes for a guaranteed outcome the agent shouldn't promise, or tries to bait it into revealing information it shouldn't share.

This gap is exactly what Ringg AI — an Indian voice AI startup — named as the core problem in the industry when announcing their $5.5M Series A in January 2026. In their own words, enterprise voice today is <cite index="10-1">plagued by brittle IVRs, disconnected contact-center tooling, and "AI pilots" that never survive production reality</cite>. Their stated mission is building toward voice AI that operates with reliability, governance, and measurable outcomes — not just an impressive demo.

Gauntlet is a direct response to that gap: a way to catch the "doesn't survive production reality" failures *before* deployment, automatically and repeatably, rather than discovering them after a real customer hits them.

*Source: [Ringg AI's Series A announcement](https://www.ringg.ai/blog/ringg-ai-announcing-our-5-5-millon-usd-series-a)*

---

## What it actually does

1. **Takes any voice agent's system prompt** — its guardrails, knowledge base, and conversation flow — as plain text.
2. **Generates adversarial personas** tailored to that specific agent's domain (a loan agent gets rate-baiters and compliance-testers; a healthcare agent would get different, domain-appropriate attackers), plus a fixed set of universal personas that apply to any voice agent regardless of domain (interrupters, code-switchers, confused callers, and more).
3. **Simulates full conversations** between each persona and the target agent, using an LLM to play both roles based on their real prompts.
4. **Scores every transcript** against a rubric grounded in the agent's own approved knowledge base, so it can tell the difference between a genuine fabrication and a correctly scripted answer.
5. **Produces a report** — a pass/fail summary per persona, with quoted evidence for every failure — that can be rerun after a fix to prove the fix worked.

---

## System architecture / workflow

```
                     ┌─────────────────────┐
                     │  Agent's system      │
                     │  prompt (text)        │
                     └──────────┬───────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │  generate_personas.py          │
                 │  Domain-specific personas       │
                 │  + universal_personas.json      │
                 │  → generated_personas.json      │
                 └──────────────┬───────────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │  run_simulations.py            │
                 │  Tester (persona) ↔ Target agent│
                 │  Turn-by-turn simulated call     │
                 │  → data/transcripts/*.txt       │
                 └──────────────┬───────────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │  score_calls.py                │
                 │  Grades transcript against      │
                 │  agent's own approved knowledge │
                 │  → data/scores/*.json           │
                 └──────────────┬───────────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │  report.py                     │
                 │  Compiles all scores into one   │
                 │  → reports/run_<timestamp>.md   │
                 └──────────────────────────────┘
```

**Two ways to run this pipeline:**

- **CLI** (`run_all_simulations.py`, `score_all_calls.py`, `report.py`) — batch-runs the full persona set against a fixed target agent, useful for repeatable regression testing during development.
- **Dashboard** (`main.py` + `dashboard.html`) — a local web interface where you paste or upload any agent's prompt, generate personas for it live, run the stress test, and watch results render in real time. Built for demos and one-off testing of a new agent.

Both interfaces call the exact same underlying functions — no duplicated logic.

---

## Tech stack and key build decisions

- **LLM for simulation and scoring:** Groq (`llama-3.1-8b-instant` for roleplay, `llama-3.3-70b-versatile` for scoring). Originally built on Gemini, switched after hitting Gemini's free-tier cap of 20 requests/day per model — far too low for a multi-persona batch. Groq's free tier (14,400 requests/day) comfortably fits the workload.
- **Persona generation:** Gemini (`gemini-2.5-flash`), kept on the original engine since this step uses far fewer requests per run than simulation/scoring.
- **Backend:** Flask, serving both the dashboard's static page and its API endpoints from the same origin, avoiding cross-origin/CORS issues entirely.
- **No platform dependency:** the target agent is only ever represented as plain prompt text, not a live connection to any specific voice AI platform. This was a deliberate design choice, made after confirming that Bolna AI (used to build the reference test agent, "Ravi") does not expose a public API for its text-chat feature — only phone-call-based and agent-management endpoints. Rather than depend on any one platform's API surface, Gauntlet works from a prompt alone, so it can be pointed at an agent built on Bolna, Ringg, or any other platform, as long as the prompt is available.

---

## Current scope and honest limitations

- **Tests agent reasoning and guardrails at the text/logic layer only.** It does not test the voice-specific layer of a real deployment — TTS/ASR behavior, latency, accent handling, or audio artifacts. A prompt and its guardrail logic behave identically whether spoken or typed; the failure modes this catches (hallucination, overclaiming, scope violations, misclassified escalation) live in that logic layer, not in the audio pipeline.
- **Simulation uses a different LLM than the one the target agent actually runs on in production**, since there's no API access to run the real platform's engine directly. The prompt and guardrails are identical either way, but a different underlying model could in principle behave slightly differently. This is a reasonable approximation, not a perfect substitute for testing the live system.
- **LLM-as-judge scoring required real calibration work** to be trustworthy — early versions both under- and over-flagged failures until the rubric was grounded in the agent's actual approved knowledge base and required quoted evidence for every failure. Always re-validate against known-good/known-bad test transcripts after changing the scoring model or rubric.

---

## Future improvements

- **Real platform integration.** If a platform exposes a text or API-based chat endpoint for its agents (unlike Bolna's current public surface), Gauntlet could call the actual deployed agent directly instead of simulating its prompt — testing the real production system, not an approximation.
- **Voice-layer testing.** Extending beyond prompt-logic testing into the audio layer: real TTS/ASR pipelines, testing how an agent handles genuine interruptions, accents, background noise, and transcription errors — the layer intentionally out of scope for this version.
- **Chat and multi-channel agents.** The same core pipeline (persona generation → simulation → scoring → report) applies almost unchanged to text-based chat agents or WhatsApp-style bots, not just voice — a natural extension given many platforms, including Ringg, now operate across voice, chat, and web channels.
- **Self-hosted engine testing.** Bolna's core orchestration engine is open source. Self-hosting it would allow testing against real STT/TTS/telephony behavior locally, without requiring a KYC-verified phone number, closing the gap between prompt-level and full production-level testing.
- **Continuous/scheduled testing.** Running the same persona suite on a recurring schedule against a live agent, tracking pass rates and failure patterns over time rather than as a single point-in-time report — turning this from a one-off test into an ongoing reliability dashboard, especially useful for tracking consistency across multiple languages.
- **Suggested-fix generation.** Having the tool draft a proposed prompt patch for each failure found, for a human to review and approve — assisting the fix without ever auto-applying changes to a live agent's guardrails unsupervised.
- **Regression comparison built into the report.** Automatically diffing a new report against the previous run to highlight exactly what got better or worse after a prompt change, rather than requiring manual comparison between two files.

---

## Project structure

```
Gauntlet/
├── main.py                       # Flask backend + dashboard server
├── dashboard.html                # Interactive web UI
├── requirements.txt
├── config/
│   ├── universal_personas.json   # Fixed, reusable personas
│   ├── generated_personas.json   # Domain-specific, regenerated per agent
│   ├── ravi_prompt.txt           # Reference test agent's full prompt
│   └── tester_prompt_template.txt
├── scripts/
│   ├── generate_personas.py
│   ├── run_simulations.py
│   ├── run_all_simulations.py
│   ├── score_calls.py
│   ├── score_all_calls.py
│   └── report.py
├── sample_domains/                # Test fixtures for dry-testing persona generation
├── sample_transcripts/            # Known-good/known-bad fixtures for scorer calibration
├── data/
│   ├── transcripts/               # Real simulation outputs
│   └── scores/                    # Real scoring outputs
└── reports/                       # Final compiled reports, timestamped
```

---

## Running it

**CLI (batch mode):**
```bash
pip3 install -r requirements.txt
python3 scripts/generate_personas.py --domain sample_domains/loan_bot.txt
python3 scripts/run_all_simulations.py --turns 8
python3 scripts/score_all_calls.py
python3 scripts/report.py
```

**Dashboard (interactive mode):**
```bash
python3 main.py
```
Then open `http://127.0.0.1:5000` in a browser.