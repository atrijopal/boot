# Phase 3 code package

This package extends `atrijopal/boot` at commit `ea58fa4b77f4cd6eb39b23966e421f529dcd5a59` (the Windows virtualenv-path fix). It implements a working Phase 3 MVP using the existing FastAPI app, Groq JSON client, spaCy medium English model, and installed numerical libraries. No additional API key, graph database, GPU, or Python package is required by the new code.

## Install into your existing repository

1. Save or commit your current local changes.
2. Extract this ZIP. Copy its `app`, `web`, `prompts`, `scripts`, `tests`, `samples`, and `docs` folders **into the existing repository root**, merging folders. Also copy this setup file. Replace the four existing files listed below when prompted. Do not replace the entire repository with this ZIP: it contains only Phase 3 additions and changed files.
3. From the repository terminal, run:

```bash
bash run.sh --test
bash run.sh
```

4. Open `http://localhost:8000`. Process audio and complete Phase 2. Review the accepted transcript and names, then click **Generate minutes — Phase 3** beneath the refinement results.

The existing `.env` and `run.sh` remain usable. The default chat models are inherited from `LLM_MODEL` and `VERIFY_MODEL`. Keep your existing `GROQ_API_KEY`. Both event extraction and semantic verification use Groq and can consume API quota.

If you already have a saved Phase 2 job, activate the project's Python environment and run:

```bash
python scripts/run_phase3.py YOUR_JOB_ID
```

With the app running, the same job can be started through `POST /jobs/YOUR_JOB_ID/summarize` and inspected through `GET /jobs/YOUR_JOB_ID/summary`. The original interface does not include a saved-job picker.

## Existing files replaced

| File | Change |
| --- | --- |
| `app/config/settings.py` | Phase 3 settings with input validation |
| `app/api/main.py` | Phase 3 router and JavaScript asset route |
| `app/refinement/pipeline.py` | Shared lock around saved Phase 2 artifacts |
| `web/index.html` | Phase 3 controls after refinement and stale-view invalidation |

All other supplied code files are additions. If your copy differs from the baseline commit, review these four replacements against your local changes first.

## What is implemented

- Sentence/length-bounded source units with exact character spans. These are processing units, not fixed-time topics.
- A persistent topic registry using normalized mean spaCy word vectors, absolute novelty checks, stable softmax memberships, conservative duplicate-topic merging, and return occurrences. Every earlier topic remains eligible.
- Fractional topic speaking-time accounting, a visible unresolved bucket, and decision/action-aware narrative budgets. These are **uncalibrated membership weights**, not learned Bayesian posterior probabilities.
- Structured claims, proposals, decisions, actions, questions and risks. Code validates exact quotes and fields; a separate model call verifies meaning and status. A verifier outage never auto-confirms records.
- A typed JSON knowledge graph with source evidence, speakers, accepted names, topics, events, deadlines, revisions, conflicts and explicit dependencies.
- Verified topic narratives, an extractive executive summary, full decision/action registers, relative deadlines as spoken, and source-evidence buttons.
- Cached model responses, successful extraction-batch checkpoints, isolated run directories, partial-result status, and stale-output rejection when the accepted transcript changes.
- Markdown/JSON downloads, CLI, regression tests, and a label-based evaluation script.

## Optional configuration

Add only the settings you want to override to `.env`:

```dotenv
# Empty means inherit LLM_MODEL / VERIFY_MODEL.
PHASE3_MODEL=
PHASE3_VERIFY_MODEL=
PHASE3_EMBEDDING_BACKEND=spacy
PHASE3_TOPIC_THRESHOLD=0.62
PHASE3_MERGE_THRESHOLD=0.92
PHASE3_TEMPERATURE=0.12
PHASE3_BATCH_CHARS=14000
PHASE3_UNIT_CHARS=1200
PHASE3_SUMMARY_WORDS=900
PHASE3_MAX_RELATION_PAIRS=5000
```

For an explicitly lexical baseline, set `PHASE3_EMBEDDING_BACKEND=tfidf` and start with `PHASE3_TOPIC_THRESHOLD=0.15`, `PHASE3_MERGE_THRESHOLD=0.85`. This baseline can miss paraphrases. Thresholds need development-set tuning for either backend.

`PHASE3_SUMMARY_WORDS` is a target for topic narrative allocation. Complete registers and evidence are exported separately and can exceed that length. `PHASE3_BATCH_CHARS` bounds serialized data approximately by characters; it is not an exact tokenizer-based token count. A record exceeding the batch limit produces an explicit incomplete/failure state. Increase the limit only if your provider's context and quota allow it.

## Outputs

Each run is stored under `outputs/<job_id>/phase3/runs/<run_id>/`. `latest.json` points to the current published run; `source_snapshot.json` records its accepted Phase 2 input. Old source revisions are retained for audit but cannot be downloaded as current minutes.

Artifacts include `minutes.md`, `minutes.json`, `phase3_result.json`, `knowledge_graph.json`, `topics.json`, `topic_assignments.json`, `events.json`, `relations.json`, `decisions.json`, `action_items.json`, `open_questions.json`, `risks.json`, `units.json`, `topic_summaries.json`, `manifest.json`, and `validation_report.json`.

Downloads use an allowlist:

```text
/jobs/<job_id>/phase3/download/minutes
/jobs/<job_id>/phase3/download/json
/jobs/<job_id>/phase3/download/graph
/jobs/<job_id>/phase3/download/actions
/jobs/<job_id>/phase3/download/decisions
/jobs/<job_id>/phase3/download/validation
```

## Validation and limits

The regression suite covers topic returns, fractional durations, quote grounding, unknown owners, exact deadlines, decision supersession, invalid verdicts, failed-batch resume, API downloads, and transcript edits during processing. LLM calls are mocked; the spaCy grouping fixture also exercises the installed real CPU embedding model. See `docs/phase3/TEST_RESULTS.md` for the final run count.

Live Groq extraction and real-meeting accuracy have not been measured in this package. Default topic thresholds are starting values, and model verification can still be wrong. Review the evidence for important outcomes. Mean word vectors are a modest baseline, not a sentence-transformer embedding service or trained probabilistic topic model.

The first release conservatively merges exact repeated records from the same speaker. It does not perform general entity alias resolution, semantic event deduplication, topic splitting, probability calibration, or cross-meeting memory. The graph interface is a topic-filtered relation table rather than a force-directed visual canvas. Unknown speakers remain unknown. Relative dates are never resolved automatically.

Use the normal single-process server launched by `run.sh`. Job locks are process-local; multi-worker/multi-machine deployment needs an external job store and distributed locks. A restart interrupts active work; submitting Phase 3 again resumes completed extraction checkpoints. No automatic background resume is claimed.

For long meetings, event reconciliation compares candidate pairs and can grow quadratically. The configured pair limit fails explicitly and marks results partial. Successful earlier batches remain reusable. Broad held-out evaluation and more scalable relation retrieval remain future work.
