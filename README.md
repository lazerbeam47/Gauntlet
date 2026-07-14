# Gauntlet — Voice Agent Stress Testing Tool

Gauntlet is a stress-testing tool for voice AI agents. It generates adversarial callers tailored to a specific agent's domain, simulates conversations between them and the agent, grades the results for hallucination, overclaiming, and guardrail resilience, and reports what broke — before a real customer finds it.

It exists in two forms that share the same underlying engine: a **CLI pipeline** (batch-run, scriptable, good for repeatable regression testing) and an **interactive dashboard** (paste any agent's prompt, watch it get tested live, in the browser).

---

## The problem this solves

Voice AI agents demo well and fail unpredictably in production. A script that sounds flawless against a calm test caller often breaks against a real one — someone who interrupts, mixes languages mid-sentence, pushes for a guaranteed outcome the agent shouldn't promise, or tries to extract information it shouldn't share.

Ringg AI — an Indian voice AI startup — named this exact gap as the core problem in the industry when announcing their $5.5M Series A in January 2026: enterprise voice today is plagued by brittle IVRs, disconnected contact-center tooling, and "AI pilots" that never survive production reality. Gauntlet is a direct attempt to catch that failure mode before deployment, automatically and repeatably, rather than after a real customer hits it.

Source: Ringg AI's Series A announcement — https://www.ringg.ai/blog/ringg-ai-announcing-our-5-5-millon-usd-series-a

---

## The build, honestly — what broke and what we learned

This section exists because the real engineering value of this project is as much in the problems solved along the way as in the final tool. Each of these was a genuine dead end or wrong assumption, corrected in order:

1. **Assumed Bolna AI's chat feature had a public API.** It doesn't — confirmed against their full published endpoint list (agent management, phone calls, call logs only, no text-chat endpoint). This forced a pivot: instead of depending on any one platform, Gauntlet simulates conversations directly from an agent's *prompt text*, using its own LLM to play both sides. This turned out to be the right architecture anyway — it works on any agent, from any platform, without needing platform API access at all.

2. **Started on Gemini, hit a wall at 20 requests/day.** Gemini's free tier for `gemini-2.5-flash` caps at 20 requests/day per project — nowhere near enough for an 11-persona batch. Simulation and scoring moved to Groq (`llama-3.1-8b-instant` / `llama-3.3-70b-versatile`), which offers a much larger free daily budget. Gemini is still used for `generate_personas.py`, since that step uses far fewer requests per run.

3. **The scorer initially both under- and over-flagged failures.** Early versions passed obviously bad transcripts and failed obviously good ones. Root causes, fixed one at a time: the scorer had no visibility into the agent's actual approved knowledge base (so it couldn't tell a correct scripted answer from a fabrication); the rubric didn't require grounded evidence (so it invented plausible-sounding but false justifications); and the model needed a worked example, not just a rule, to correctly distinguish a hedged statement ("eligibility looks positive, but...") from a genuine overclaim ("guaranteed 8.5%, locked in"). Fixed by grounding the rubric in the agent's real prompt, requiring an exact quote for every failure, and adding calibration examples.

4. **Rate limiting looked solved, then wasn't.** An initial limiter tracked only requests-per-minute. Failures kept happening mid-conversation anyway — turned out Groq also enforces a separate, tighter tokens-per-minute limit, and since full conversation history gets resent every turn, later turns in a conversation cost far more tokens than earlier ones. Fixed with a token-aware shared rate limiter and by trimming conversation history sent per call instead of letting it grow unbounded.

5. **Concurrency was added for speed, then removed.** Running multiple personas in parallel seemed like an obvious speedup. It wasn't: the free tier's token-per-minute budget (~5,500 TPM) is shared across everything regardless of how many workers are running, so parallel workers just queue for the same fixed budget, adding retry overhead without adding real throughput. The dashboard now runs personas sequentially; the only real speed lever on a free-tier budget is shorter conversations (a "Quick" mode with 4 turns vs. an "In-Depth" mode with 8). This would genuinely benefit from concurrency on a paid tier with a higher token budget — the code path for it still exists, just disabled by default.

6. **Dashboard CORS/origin issues, twice.** First, opening `dashboard.html` directly as a `file://` page couldn't safely call `localhost:5000` due to browser cross-origin restrictions. Fixed by having the Flask server serve the dashboard itself, so both live on the same origin. Second bug: the frontend still hardcoded `http://localhost:5000` while the page was sometimes loaded via `127.0.0.1:5000` — two different origins to a browser even though they're the same machine. Fixed by making the frontend call relative paths, so it always matches whatever host actually served the page.

---

## Architecture

```
Agent's system prompt (text)
        |
        v
generate_personas.py -- domain-specific personas + universal_personas.json
        |                        -> generated_personas.json
        v
run_simulations.py -- Tester (persona) <-> Target agent, turn-by-turn
        |                        -> data/transcripts/*.txt
        v
score_calls.py -- grades transcript against the agent's own approved knowledge
        |                        -> data/scores/*.json
        v
report.py -- compiles all scores into one report
                             -> reports/run_<timestamp>.md
```

Both the CLI and the dashboard call this exact same chain of functions — nothing is duplicated between them.

---

## File-by-file breakdown

### main.py
Flask backend. Serves dashboard.html at `/`, and exposes three endpoints the dashboard calls: `/api/generate_personas`, `/api/run_persona`, `/api/generate_report`. Each endpoint calls straight into the real functions in scripts/, so the dashboard is never running separate logic from the CLI. Run with `python3 main.py`; visit `http://127.0.0.1:5000`.

### dashboard.html
The interactive frontend. Single self-contained file (HTML/CSS/JS, no build step). Lets you paste or upload an agent's prompt, generate personas for it, choose Quick or In-Depth mode, run the stress test, watch a live pass-rate readout and per-persona breakdown, and download the final report as markdown. Styled as a warm analog-console theme — deliberately chosen to feel like real audio testing equipment rather than a generic dark SaaS dashboard, since the subject matter is voice.

### scripts/generate_personas.py
Takes a domain description (text) and asks Gemini for 4-5 adversarial personas tailored to it, then merges them with the fixed universal set. Callable standalone (CLI) or imported directly by main.py (dashboard).

### scripts/run_simulations.py
The simulation engine. Builds Tester's prompt (universal template + a specific persona's instructions) and the target agent's prompt, then runs a turn-by-turn conversation between them using Groq, trimming history each turn to control token usage, and stopping early if the agent delivers a natural closing line. Contains `send_with_retry`, which handles both rate-limit and connection-error retries via the shared rate_limiter.

### scripts/run_all_simulations.py
CLI-only batch wrapper — loops run_simulations.py's core function over every persona in generated_personas.json, one after another, saving a transcript per persona. The dashboard does its own equivalent looping in JavaScript instead of using this file directly.

### scripts/score_calls.py
Grades a single transcript against a five-point rubric (hallucination, overclaim, interruption recovery, obedience, escalation), grounded in the agent's actual approved knowledge base so it can tell a correct scripted answer apart from a fabrication. Requires quoted evidence for any failure. Uses Groq's larger 70B model, since judging needs more reasoning care than roleplay.

### scripts/score_all_calls.py
CLI-only batch wrapper for scoring — loops score_calls.py over every transcript file found in data/transcripts/.

### scripts/report.py
Reads every score file in data/scores/, compiles a summary table plus detailed failure reasons, and saves it as a timestamped markdown file in reports/. Used by both the CLI flow and the dashboard's report endpoint.

### scripts/rate_limiter.py
A thread-safe limiter shared across every Groq call, tracking both request count and estimated token volume per model, since token-per-minute turned out to be the real binding constraint, not request count alone.

### config/universal_personas.json
Six fixed, hand-written personas (interrupter, code-switcher, confused/repeater, mumbler, off-script tangent, rapid-fire) that apply to any voice agent regardless of domain. Never regenerated.

### config/generated_personas.json
The current domain-specific personas, regenerated each time generate_personas.py runs against a new agent prompt. Combined with the universal set at generation time.

### config/ravi_prompt.txt and config/tester_prompt_template.txt
The reference test agent's full prompt (a fictional loan agent, "Ravi," used to validate the whole pipeline) and the template Tester's persona gets injected into. These are what the CLI scripts default to if no agent prompt is passed explicitly; the dashboard always passes its own prompt text instead.

### sample_domains/ and sample_transcripts/
Fixtures used only for dry-testing the persona generator and the scorer on fake data, without spending any real API calls on a live agent — this is what let early development happen for free, before touching a real target agent.

### data/transcripts/, data/scores/, reports/
Real output from actual runs. Transcripts and scores are one file per persona; reports are timestamped so a before/after comparison after a prompt fix is always possible.

---

## Running it

CLI (batch mode):
```
pip3 install -r requirements.txt
python3 scripts/generate_personas.py --domain sample_domains/loan_bot.txt
python3 scripts/run_all_simulations.py --turns 8
python3 scripts/score_all_calls.py
python3 scripts/report.py
```

Dashboard (interactive mode):
```
python3 main.py
```
Then open http://127.0.0.1:5000.

---

## Current scope and honest limitations

- Tests agent reasoning and guardrails at the text/logic layer only — not the voice-specific layer (TTS/ASR, latency, accent handling). A prompt's guardrail logic behaves the same whether spoken or typed; that's the layer this catches.
- Simulation runs on a different LLM (Groq) than whatever engine the target platform actually uses in production, since there's no API access to the real deployed system. The prompt and guardrails are identical either way, but this is an approximation, not a perfect substitute.
- Free-tier token budgets are the binding constraint on speed, not engineering effort — a paid tier would make the already-built concurrency path genuinely useful.

---

## Future improvements

- Real platform integration, if/when a platform exposes a genuine chat API for its agents — testing the real deployed system instead of a prompt-level simulation.
- Voice-layer testing — extending into real TTS/ASR behavior, not just prompt logic.
- Chat and multi-channel agents — the same pipeline applies almost unchanged to text/WhatsApp-style bots, not just voice.
- Self-hosted engine testing — Bolna's core orchestration engine is open source; self-hosting it would allow real STT/TTS/telephony testing without a KYC-verified phone number.
- Continuous/scheduled testing — running the same persona suite on a schedule against a live agent, tracking pass rates over time instead of one-off reports.
- Suggested-fix generation — drafting a proposed prompt patch per failure, for human review, never auto-applied.
- Regression comparison — automatically diffing a new report against the previous run to highlight what changed after a prompt edit.
- Real concurrency on a paid tier — the worker-pool code already exists in the dashboard, disabled by default; it becomes genuinely useful once the token budget isn't the bottleneck.