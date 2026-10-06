# Meeting AI

Convert English meeting recordings into refined transcripts, topic-wise minutes, decisions, and action items—with source evidence.

## Features

| Phase | Function |
| --- | --- |
| **1 — Transcription** | Timestamped transcription, normalization, and extraction of numbers, names, dates, terms, negations, and candidate events |
| **2 — Refinement** | Evidence-based corrections, speaker identification, edit verification, and manual review |
| **3 — Summarization** | Persistent topic grouping, knowledge graph, topic summaries, decisions, actions, questions, and risks |

The application runs without a GPU. Speech recognition and language-model inference use APIs; audio processing, topic embeddings, and graph construction run locally.

## Setup

Requires Bash and internet access. On Windows, use **Git Bash or WSL**.

```bash
git clone --branch akansha https://github.com/atrijopal/boot.git
cd boot
cp .env.example .env
```

Add your API keys to `.env`:

```dotenv
GROQ_API_KEY=your_groq_key
DEEPGRAM_API_KEY=your_deepgram_key
```

- **Groq:** Required for the default transcription and language-model workflow.
- **Deepgram:** Optional; provides speaker diarization and a second transcription opinion.

If `.env` already exists, edit it instead of replacing it. Never commit API keys.

Start the application:

```bash
bash run.sh
```

Open **http://localhost:8000**. The launcher prepares Python 3.12 and installs dependencies.

## Usage

1. Upload a recording and click **Process**.
2. Review the Phase 1 transcript and extracted facts.
3. Click **Refine transcript (Phase 2)**.
4. Review corrections and speaker names; accept or reject suggested changes.
5. Click **Generate minutes — Phase 3**.
6. Inspect summaries, decisions, actions, and their source evidence.
7. Download Markdown minutes or JSON results.

Accepted transcript changes make existing minutes stale. Regenerate them to use the updated text.

## How It Works

### Phase 1 — Transcribe and Extract

Audio is validated, prepared, and transcribed using Groq or Deepgram. The raw transcript is preserved while a normalized version supports extraction of important facts and candidate meeting events.

### Phase 2 — Refine and Verify

A language model proposes targeted corrections. Code checks protected information and supporting evidence, while a verifier assesses meaning preservation. The interface shows each change and allows manual review.

Speaker labels come from acoustic diarization. Names use accepted transcript evidence or manual mappings.

### Phase 3 — Organize and Summarize

**Persistent topics:** Normalized spaCy word vectors and similarity thresholds group related utterances. Revisiting a topic later can return the discussion to the same topic.

**Soft memberships:** A unit can contribute to multiple topics. Its duration is allocated proportionally:

$$
T_k = \sum_i d_i p_{ik}
$$

Here, \(d_i\) is the unit duration and \(p_{ik}\) is its membership weight for topic \(k\). These weights are uncalibrated estimates. Decisions and commitments influence summary priority independently of discussion length.

**Knowledge graph:** Typed records connect topics, speakers, claims, proposals, decisions, actions, deadlines, and evidence. Supported relations preserve revisions, conflicts, and dependencies.

**Grounded minutes:** Events and generated topic claims undergo evidence checks and semantic verification. Complete decision and action registers remain available alongside the concise narrative.

## Evidence Rules

- Phase 3 does not modify the accepted transcript.
- Evidence quotes must match the source text.
- Proposals are distinguished from decisions.
- Requests and assignments are distinguished from commitments.
- Owners and deadlines require explicit supporting evidence.
- Unknown information remains unknown.
- Relative deadlines remain as spoken.
- Superseded decisions remain available in the history.

Model verification reduces errors but does not guarantee factual accuracy.

## Commands

| Command | Purpose |
| --- | --- |
| `bash run.sh` | Start the application |
| `bash run.sh --setup` | Install dependencies only |
| `bash run.sh --check` | Check API configuration |
| `bash run.sh --test` | Run the test suite |
| `bash run.sh --cli AUDIO_FILE` | Run Phase 1 from the terminal |
| `python scripts/run_phase3.py JOB_ID` | Summarize a saved Phase 2 job |

Use the project’s Python environment for direct Python commands.

## Configuration

Settings are loaded from `.env`. Model names below are repository defaults.

| Variable | Default |
| --- | --- |
| `ASR_PROVIDER` | `groq` |
| `LLM_MODEL` | `openai/gpt-oss-120b` |
| `REFINE_MODEL` | `openai/gpt-oss-20b` |
| `VERIFY_MODEL` | `openai/gpt-oss-120b` |
| `REFINE_USE_SECOND_ASR` | `true` |
| `CACHE_ENABLED` | `true` |
| `PHASE3_EMBEDDING_BACKEND` | `spacy` |
| `PHASE3_TOPIC_THRESHOLD` | `0.62` |
| `PHASE3_MERGE_THRESHOLD` | `0.92` |
| `PHASE3_TEMPERATURE` | `0.12` |
| `PHASE3_SUMMARY_WORDS` | `900` |

`PHASE3_MODEL` and `PHASE3_VERIFY_MODEL` can override the inherited chat models. Phase 3 verification remains mandatory even if Phase 2 verification is disabled.

## Project Structure

| Path | Responsibility |
| --- | --- |
| `app/audio/`, `app/asr/` | Audio preparation and transcription |
| `app/preprocessing/` | Phase 1 extraction |
| `app/refinement/` | Phase 2 corrections and speaker processing |
| `app/summarization/` | Phase 3 topics, events, graph, summaries, and exports |
| `app/schemas/` | Typed data contracts |
| `app/api/` | FastAPI application |
| `web/` | Browser interface |
| `prompts/` | Versioned model prompts |
| `scripts/` | CLI and evaluation tools |
| `tests/` | Regression and integration tests |
| `docs/` | Plans, contracts, and validation notes |

## Outputs

Phases 1–2 save transcripts, extracted facts, edits, and speaker information under:

```text
outputs/<job_id>/
```

Phase 3 saves each run under:

```text
outputs/<job_id>/phase3/runs/<run_id>/
```

Main artifacts include:

- `minutes.md` and `minutes.json`
- `topics.json` and `topic_assignments.json`
- `knowledge_graph.json`
- `events.json` and `relations.json`
- `decisions.json` and `action_items.json`
- `open_questions.json` and `risks.json`
- `manifest.json` and `validation_report.json`

Successful extraction batches are checkpointed. Partial runs can be submitted again to resume unfinished work.

## API

Interactive documentation: **http://localhost:8000/docs**

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/process-audio` | Start Phase 1 |
| `POST` | `/jobs/{job_id}/refine` | Start Phase 2 |
| `POST` | `/jobs/{job_id}/summarize` | Start Phase 3 |
| `GET` | `/jobs/{job_id}/summary` | Read Phase 3 status/results |
| `GET` | `/jobs/{job_id}/topics` | Inspect topics |
| `GET` | `/jobs/{job_id}/graph` | Inspect the knowledge graph |
| `GET` | `/jobs/{job_id}/phase3/download/{artifact}` | Download results |

## Testing and Limitations

The package validation run passed **146 tests**, including **28 Phase 3 tests**. API calls were mocked; selected topic tests used the actual spaCy model.

- Live Phase 3 Groq extraction and real-meeting accuracy remain untested.
- Topic weights are uncalibrated; grouping and model outputs require review.
- The graph interface currently uses a relation table.
- Default recording limits are **500 MB / four hours**.
- The application is designed for a local, single-process server.
- Audio and transcript excerpts are sent to the configured API providers.

See [PHASE3_SETUP.md](PHASE3_SETUP.md) and [docs/phase3/](docs/phase3/) for implementation details.

## Credits

Sample recordings include excerpts from the [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/) under CC BY 4.0. See [samples/README.md](samples/README.md).
