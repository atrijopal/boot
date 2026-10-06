# Meeting AI — Phases 1–3

Turn an English meeting recording into an evidence-backed transcript and meeting minutes.

- **Phase 1:** transcription, timestamps, protected facts and candidate meeting events.
- **Phase 2:** verified transcript corrections, speaker labels/names and an editable change log.
- **Phase 3:** recurring topics, duration-weighted topic memberships, a JSON knowledge graph, topic summaries, decisions and action items with source evidence.

Runs locally on CPU; transcription and language-model calls use APIs. Topic memberships are uncalibrated weights, not trained Bayesian probabilities. Review important decisions against their evidence.

## Setup and run

Use **Git Bash** in VS Code on Windows (or a Linux/macOS terminal), from the repository folder:

```bash
git switch akansha
git pull --ff-only origin akansha
bash run.sh --setup
```

Setup creates `.env` only if it does not already exist. Open it locally and set:

```dotenv
GROQ_API_KEY=your_new_groq_key
DEEPGRAM_API_KEY=your_new_deepgram_key
```

Get keys from [Groq](https://console.groq.com) and [Deepgram](https://console.deepgram.com). Deepgram is optional with the default Groq transcription provider; it supplies speaker labels and a second ASR opinion. If selecting `ASR_PROVIDER=deepgram`, its key is required.

**Never put real keys in source files or `.env.example`.** Previously committed credentials must be revoked and replaced; removing them from the current branch does not erase Git history. `.env`, `.env.local` and other local environment files are ignored.

```bash
bash run.sh --check
bash run.sh
```

Open **http://localhost:8000**. Upload audio → process Phase 1 → refine Phase 2 → review edits/names → **Generate minutes — Phase 3**. Inspect the topic summaries and graph relations, then download the minutes/JSON. Stop with `Ctrl+C`.

## Tests and CLI

```bash
bash run.sh --test                         # entire offline regression suite
bash run.sh --test tests/phase3            # Phase 3 only
bash run.sh --cli samples/jfk.flac          # Phase 1; prints a job_id
bash run.sh --phase2 YOUR_JOB_ID
bash run.sh --phase3 YOUR_JOB_ID
```

Tests mock paid APIs and block outbound HTTP. Initial dependency installation needs internet. `--check` and normal processing use live APIs; model calls consume provider quota.

For **PowerShell setup**, full manual tests, expected outputs, API examples, and troubleshooting, see [Run and test guide](docs/RUN_AND_TEST.md).

## Configuration

All settings come from `.env` or environment variables. `.env.example` contains blank credentials and available overrides. This branch uses `PHASE3_*` settings for its Phase 3 implementation.

| Setting | Purpose |
| --- | --- |
| `ASR_PROVIDER` | `groq` (default) or `deepgram` |
| `CANDIDATE_DETECTOR` | `llm` (default) or `off` for Phase 1 candidate labelling |
| `REFINE_USE_SECOND_ASR` | Request another ASR when its key is available |
| `LLM_MODEL`, `REFINE_MODEL`, `VERIFY_MODEL` | Chat models for detection/refinement/verification |
| `LLM_BASE_URL`, `LLM_API_KEY` | Optional alternate chat endpoint and its own key |
| `PHASE3_MODEL`, `PHASE3_VERIFY_MODEL` | Empty means inherit `LLM_MODEL` / `VERIFY_MODEL` |
| `PHASE3_EMBEDDING_BACKEND` | `spacy` (default) or a lexical `tfidf` baseline |
| `PHASE3_SUMMARY_WORDS` | Topic narrative budget; full registers can exceed it |
| `CACHE_ENABLED` | Reuse cached results (default `true`) |

With the default Groq chat endpoint, an empty `LLM_API_KEY` reuses `GROQ_API_KEY`. Other chat endpoints require their own `LLM_API_KEY`; choose models that provider supports.

## Outputs and structure

Per-job artifacts are under `outputs/<job_id>/`. Phase 3 writes versioned runs under `phase3/runs/<run_id>/`, including `minutes.md`, `minutes.json`, `knowledge_graph.json`, `topics.json`, `action_items.json` and `validation_report.json`. Changing the accepted transcript invalidates old minutes; generate them again.

| Directory | Purpose |
| --- | --- |
| `app/audio`, `app/asr`, `app/preprocessing` | Phase 1 |
| `app/refinement` | Phase 2 |
| `app/summarization` | Phase 3 topic model, graph and minutes |
| `web`, `prompts` | Browser interface and versioned model prompts |
| `scripts`, `tests` | CLI, evaluation and regression tests |
| `docs/phase1`, `docs/phase2`, `docs/phase3` | Contracts and technical plans |

Phase 3 is integrated at the repository root; no manual copying from `phase3_code/` is needed. Use the single-process server launched by `run.sh`; multi-worker deployments need shared job storage and locks. Offline tests do not establish live model accuracy or API availability.
