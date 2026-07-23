<!-- # Gauntlet — Voice Agent Stress Testing Tool

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
- Real concurrency on a paid tier — the worker-pool code already exists in the dashboard, disabled by default; it becomes genuinely useful once the token budget isn't the bottleneck. -->
# Gauntlet - Voice Agent Stress Testing Tool

Gauntlet is a stress-testing framework for conversational AI agents. It automatically generates adversarial callers tailored to an agent's domain, runs realistic conversations against the agent, evaluates failures such as hallucinations, overclaiming, and guardrail violations, and generates detailed reports before those failures reach production.

Unlike traditional evaluation frameworks that operate purely at the prompt level, Gauntlet evaluates both prompt logic and the complete production voice stack, including Speech-to-Text (STT), turn detection, latency, interruptions, LLM reasoning, and Text-to-Speech (TTS).

Gauntlet currently supports two execution modes:

- **Text Simulation** — Fast prompt-level evaluation using LLMs.
- **Live Voice** — End-to-end testing against real LiveKit voice agents.

Both execution modes produce the same transcript format and share the same evaluation and reporting pipeline.

---

# Why Gauntlet?

Voice AI agents rarely fail during demos.

They fail when real users:

- interrupt constantly
- change topics mid-conversation
- ask multiple questions at once
- exploit weak guardrails
- pressure the assistant into making promises
- ask ambiguous questions
- speak unclearly
- intentionally confuse the model

Most evaluation frameworks focus only on prompt quality.

Production failures usually occur across the entire voice pipeline:

- Speech-to-Text
- Endpoint Detection
- Turn Taking
- LLM Reasoning
- Text-to-Speech
- Conversation Flow

Gauntlet automatically discovers these failures before your customers do.

---

# Features

- Automatic adversarial persona generation
- Universal and domain-specific personas
- Prompt-level text simulation
- End-to-end Live Voice testing
- Hallucination detection
- Overclaim detection
- Guardrail evaluation
- Automated transcript collection
- LLM-based scoring
- Markdown report generation
- Interactive dashboard
- Batch evaluations

---

# High-Level Architecture

```text
                           Agent Prompt
                                 │
                                 ▼
                    Persona Generation Engine
                                 │
               Universal + Domain Personas
                                 │
                                 ▼
                      Evaluation Orchestrator
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
                ▼                                 ▼
         Text Simulation                 Live Voice (LiveKit)
                │                                 │
                └────────────────┬────────────────┘
                                 ▼
                       Conversation Transcript
                                 │
                                 ▼
                        Evaluation Engine
                                 │
                                 ▼
                      Findings & Markdown Report
```

Regardless of whether conversations are generated through text simulation or real audio, both execution modes produce the same transcript format and share the same evaluation pipeline.

---

# Why Two Modes?

Gauntlet supports two complementary execution modes.

| Text Simulation | Live Voice |
|-----------------|------------|
| Fast | Production-realistic |
| Low cost | Exercises the complete voice stack |
| Prompt reasoning | STT + LLM + TTS |
| Ideal for regression testing | Ideal for end-to-end validation |
| No voice infrastructure required | Requires LiveKit |

Text Simulation is useful for quickly iterating on prompts, while Live Voice validates how a deployed voice agent behaves during realistic customer conversations. Since both modes produce the same transcript format, the evaluation pipeline remains identical.

---

# Text Simulation

Text Simulation evaluates only the reasoning capabilities of an AI agent.

```text
Persona
     │
     ▼
LLM (Tester)
     │
Conversation
     │
     ▼
LLM (Target Agent)
     │
     ▼
Transcript
     │
     ▼
Evaluation
```

This mode is inexpensive and ideal for:

- regression testing
- prompt improvements
- guardrail validation
- hallucination detection
- reasoning evaluation

Since no speech layer exists, evaluations complete very quickly.

---

# Live Voice Pipeline

Prompt-level simulations cannot detect failures introduced by the production voice stack.

Live Voice Mode evaluates the exact pipeline a customer interacts with.

```text
                      Agent Prompt
                            │
                            ▼
                  Persona Generator
                            │
                            ▼
                 Select Adversarial Persona
                            │
                            ▼
                 Programmable Caller (Tester)
                            │
                Generate Next Utterance (LLM)
                            │
                            ▼
              Local TTS (Kokoro / Piper)
                            │
                  Raw PCM Audio Frames
                            │
                            ▼
              LiveKit AudioSource Track
                            │
                            ▼
                    LiveKit Room
        ┌───────────────────┴───────────────────┐
        │                                       │
        ▼                                       ▼
 Programmable Caller                 Target Voice Agent
                                     (LiveKit AgentSession)
                                              │
                                              ▼
                                   Deepgram Speech-to-Text
                                              │
                                              ▼
                                         LLM Reasoning
                                              │
                                              ▼
                                   Cartesia Text-to-Speech
                                              │
                                              ▼
                                  Synthesized Audio Response
        ▲                                       │
        │                                       ▼
        └──────── Receive Audio Reply ◄─────────┘
                            │
                            ▼
                Speech-to-Text Transcription
                            │
                            ▼
                 Conversation History Updated
                            │
                            ▼
               Generate Next User Utterance
                            │
                            ▼
                   Continue Conversation
                            │
                            ▼
                     End Conversation
                            │
                            ▼
                  Transcript Collection
                            │
                            ▼
                  LLM Evaluation Pipeline
                            │
                            ▼
                 Findings & Markdown Report
```

Unlike another autonomous voice agent, Gauntlet uses a programmable caller.

The caller:

1. Generates its next utterance with an LLM.
2. Converts it into speech locally using Kokoro or Piper.
3. Streams raw PCM audio into LiveKit.
4. Receives the target agent's spoken response.
5. Transcribes that response.
6. Uses the updated conversation history to generate the next utterance.

This closely simulates how a real customer interacts with a deployed voice agent while remaining completely automated.

---

# What Live Voice Evaluates

Live Voice Mode exercises the complete production voice pipeline, allowing failures to surface that prompt-only evaluations cannot detect.

It evaluates:

- Speech-to-Text accuracy
- Endpoint detection
- Turn-taking behaviour
- Interruption handling
- Response latency
- Conversation flow
- LLM reasoning
- Text-to-Speech quality
- Voice-specific hallucinations

---

# Evaluation Pipeline

Regardless of how conversations are generated, every transcript passes through the same evaluation engine.

```text
Conversation
      │
      ▼
Transcript
      │
      ▼
Transcript Processing
      │
      ▼
LLM Evaluation
      │
      ├── Hallucination Detection
      ├── Overclaim Detection
      ├── Instruction Following
      ├── Guardrail Compliance
      ├── Conversation Quality
      ├── Safety
      └── Helpfulness
      │
      ▼
Structured Scores
      │
      ▼
Markdown Report
```

Because both execution modes produce the same transcript structure, the evaluator does not need to know whether a conversation originated from text simulation or Live Voice.

---

# Project Structure

```text
Gauntlet
│
├── Dashboard
│   ├── main.py
│   └── dashboard.html
│
├── Evaluation Engine
│   ├── generate_personas.py
│   ├── run_simulations.py
│   ├── score_calls.py
│   ├── report.py
│   └── rate_limiter.py
│
├── Live Voice
│   ├── dispatch_room.py
│   ├── runtime/
│   ├── my-agent/
│   └── tester-agent/
│
├── config/
├── reports/
└── data/
```

## Dashboard

The dashboard provides a single interface for running evaluations.

It allows you to:

- Paste an agent prompt
- Generate adversarial personas
- Choose Text Simulation or Live Voice
- Monitor evaluation progress
- Review failures
- Export Markdown reports

---

# Core Components

### Persona Generator

Generates domain-specific adversarial personas using Gemini and combines them with a reusable set of universal personas.

### Simulation Engine

Runs conversations between generated personas and the target agent.

Depending on the selected mode, conversations are produced through either:

- Text Simulation
- Live Voice (LiveKit)

Both produce the same transcript format.

### Evaluation Engine

Scores every transcript against multiple quality dimensions and produces structured findings.

### Report Generator

Aggregates all persona evaluations into a single Markdown report highlighting failures, recurring patterns, and recommendations.

---

# File-by-File Breakdown

## `main.py`

The Flask backend powering the dashboard.

Responsibilities include:

- serving the web interface
- generating personas
- starting evaluations
- streaming progress
- exporting reports

Both the dashboard and CLI reuse the same evaluation pipeline, ensuring identical behaviour regardless of how Gauntlet is used.

---

## `dashboard.html`

A lightweight interface for configuring and running evaluations.

Features include:

- Prompt editor
- Persona generation
- Text Simulation / Live Voice selection
- Quick and In-Depth evaluations
- Live progress tracking
- Transcript viewer
- Markdown report export

---

## `generate_personas.py`

Creates adversarial callers from an agent prompt.

The generator combines:

- Universal personas
- Domain-specific personas generated by Gemini

This produces a balanced mix of realistic and edge-case conversations.

---

## `run_simulations.py`

The orchestration layer for every evaluation.

Responsibilities:

- loading personas
- constructing prompts
- executing conversations
- dispatching Live Voice runs
- collecting transcripts
- retrying failed runs
- saving evaluation artifacts

This is the central coordinator of Gauntlet.

---

## `runtime/livekit_runtime.py`

Coordinates Live Voice evaluations.

Responsibilities include:

- creating LiveKit rooms
- launching participants
- monitoring conversations
- collecting transcripts
- forwarding completed conversations into the evaluation engine

The runtime intentionally contains very little business logic. It simply orchestrates the voice infrastructure while reusing the existing evaluation pipeline.

---

## `dispatch_room.py`

Creates a dedicated LiveKit room for each evaluation and dispatches all required participants.

Each evaluation launches:

- Target Voice Agent
- Programmable Caller

Both participants receive configuration through dispatch metadata, allowing completely isolated evaluations.

---

## `my-agent/`

Contains the production voice agent being evaluated.

The target agent is an unmodified LiveKit `AgentSession` using its normal production stack:

```text
Deepgram STT
      │
      ▼
LLM Reasoning
      │
      ▼
Cartesia TTS
```

Gauntlet intentionally does **not** modify the production agent.

Instead, it evaluates the deployed agent exactly as a customer would interact with it.

---

## `tester-agent/`

Implements Gauntlet's programmable caller.

Unlike another autonomous AI assistant, the programmable caller behaves like a realistic customer.

Conversation loop:

```text
Persona
    │
    ▼
LLM generates user utterance
    │
    ▼
Local TTS
(Kokoro / Piper)
    │
    ▼
Publish Audio
    │
    ▼
Target Agent
    │
    ▼
Receive Audio
    │
    ▼
Speech-to-Text
    │
    ▼
Conversation History
    │
    ▼
Generate Next User Message
```

This architecture provides complete control over the simulated customer while keeping the production agent completely unchanged.

---

## `score_calls.py`

Evaluates completed transcripts using LLM-based judges.

Current evaluation dimensions include:

- Hallucination
- Overclaiming
- Instruction Following
- Guardrail Compliance
- Safety
- Helpfulness
- Conversation Quality

Since evaluation operates on transcripts rather than audio, both execution modes reuse exactly the same scoring pipeline.

---

## `report.py`

Aggregates all evaluation results into a single Markdown report.

Each report includes:

- Overall score
- Persona breakdown
- Failure explanations
- Common failure patterns
- Recommendations

The goal is to make failures immediately actionable instead of requiring manual transcript review.

---

# Running Gauntlet

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 2. Launch the Dashboard

```bash
python main.py
```

Open:

```text
http://127.0.0.1:5000
```

---

## 3. Text Simulation

1. Paste the target agent prompt.
2. Generate personas.
3. Select **Text Simulation**.
4. Start evaluation.

Gauntlet will automatically:

- generate conversations
- collect transcripts
- evaluate failures
- produce a Markdown report

---

## 4. Live Voice

Start the production voice agent.

```bash
cd my-agent

uv run python src/agent.py dev
```

Start the programmable caller.

```bash
cd tester-agent

uv run python src/agent.py dev
```

Launch the dashboard.

```bash
python main.py
```

Choose **Live Voice**, generate personas, and begin the evaluation.

Gauntlet automatically creates a LiveKit room, dispatches both participants, records the conversation, evaluates the transcript, and generates the final report.

---

# Engineering Decisions

Building Gauntlet involved several architectural decisions that shaped the system.

## Transcript-Centric Architecture

Every evaluation ultimately produces a transcript.

Whether conversations originate from:

- Text Simulation
- Live Voice

the remainder of the pipeline stays exactly the same.

This significantly reduces duplicate code while allowing new execution backends to be added in the future.

---

## Separation of Execution and Evaluation

Conversation generation and transcript evaluation are intentionally separated.

```text
Conversation Generation

↓

Transcript

↓

Evaluation

↓

Report
```

This makes the evaluation engine reusable regardless of how conversations are produced.

---

## Programmable Caller Instead of Dual Agents

Many voice evaluation systems connect two autonomous AI agents together.

Gauntlet instead simulates a customer.

Benefits include:

- deterministic personas
- realistic conversations
- repeatable evaluations
- complete control over user behaviour

This better reflects how production voice agents are actually used.

---

## Local Speech Synthesis

The programmable caller uses local speech synthesis instead of cloud APIs.

Benefits:

- lower latency
- reduced API costs
- offline execution
- unlimited evaluations

---

## Platform Agnostic Design

Gauntlet evaluates conversational behaviour rather than platform-specific implementations.

Although Live Voice currently uses LiveKit, the evaluation pipeline remains independent of the underlying voice infrastructure.

This makes it straightforward to support additional voice platforms in the future.

---

# Current Limitations

Current limitations include:

- Persona quality depends on the capabilities of the underlying LLM.
- Live Voice currently assumes a LiveKit-based deployment.
- Business-specific evaluation criteria must be customized separately.
- Voice metrics such as latency and interruption recovery are not yet included in the default scoring pipeline.

---

# Roadmap

Planned improvements include:

- Automatic prompt optimization
- Regression testing suites
- CI/CD integration
- Voice latency benchmarking
- Interruption recovery metrics
- STT accuracy evaluation
- Voice quality scoring
- Multi-agent benchmarking
- Historical evaluation tracking
- Enterprise dashboards

---

# Tech Stack

### Backend

- Python
- Flask

### Voice Infrastructure

- LiveKit

### Speech

- Deepgram
- Cartesia
- Kokoro
- Piper

### Models

- Gemini
- Groq

### Evaluation

- LLM-as-a-Judge
- Transcript Scoring
- Persona Generation
- Automated Markdown Reports

---

# Future Vision

As conversational AI systems become increasingly production-critical, testing them should become as repeatable as testing traditional software.

Gauntlet aims to provide that testing infrastructure.

Instead of manually calling a voice agent before every release, teams should be able to run hundreds of realistic customer conversations automatically, identify regressions, evaluate quality, and generate actionable reports before changes reach production.

The long-term vision is to make conversational AI testing a standard part of every deployment pipeline—bringing automated quality assurance to voice agents in the same way unit tests and integration tests transformed traditional software engineering.

---

# Contributing

Contributions are welcome.

If you're interested in improving conversational AI evaluation, feel free to open an issue, submit a pull request, or propose new evaluation metrics and personas.

---

# License

This project is licensed under the MIT License.