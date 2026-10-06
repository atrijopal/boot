# Meeting AI (Phase 1 + Phase 2)

Upload an English meeting recording and get:

1. **Phase 1:** a timestamped transcript plus everything extracted from it before any LLM rewrites anything: numbers, dates and times, names, technical terms, negations, and candidate questions / proposals / commitments / decisions / tasks.
2. **Phase 2:** a **refined transcript** where mis-heard technical terms, acronyms and names are fixed, with a side-by-side view of **exactly what changed and why**, plus **who is speaking** and (when people say it) **their names**.
3. **Phase 3:** **minutes**: decisions, action items (owner and deadline only when stated), open questions, proposals, disagreements, risks and key figures, topic minutes and an executive summary. Every item carries a quote from the transcript and was checked by a second model. The interface shows **every model step** (what it was asked, what it answered, what our checks kept or dropped) and then the final report.

All heavy models run on APIs. Nothing needs a GPU. Nothing is tied to one meeting topic: terms and names are discovered from each recording.

---

## Quick start

```bash
# 1. get your keys (next section), then:
cp .env.example .env          # paste your keys into .env
# 2. one command: installs everything on first run, then starts the app
bash run.sh
# 3. open http://localhost:8000
```

That is all. `bash run.sh` installs a private Python 3.12 environment and all dependencies the first time (a few minutes), then just starts the app on later runs.
Works on Linux, macOS, and Windows through WSL or Git Bash. It needs `bash`, internet access, and either `curl` or `pip`.

| Command | What it does |
|---|---|
| `bash run.sh` | install if needed, start the web app |
| `bash run.sh --check` | check your API keys work (prints OK/INVALID, never shows the keys) |
| `bash run.sh --test` | run the 199 tests (no keys needed; the APIs are mocked) |
| `bash run.sh --cli samples/ami_ES2008a_120s-420s.flac` | process a file from the terminal |
| `PORT=8001 bash run.sh` | use a different port |

---

## Get your API keys

You need **one required key (Groq)** and **one optional key (Deepgram)**. Both have free tiers. Paste them into `.env`.

### Groq (required): transcription and the language models
1. Go to <https://console.groq.com> and sign up (Google login works).
2. Open **API Keys** in the left menu, click **Create API Key**, copy it. It starts with `gsk_` and is shown only once.
3. In `.env`: `GROQ_API_KEY=gsk_...`

Groq is used for speech-to-text (Whisper large-v3) and for all language-model steps (labelling, refinement, verification).
Free-tier limits: roughly 2 hours of audio per hour, plus per-minute token limits. The app retries automatically when it is rate limited.

### Deepgram (optional, strongly recommended): speaker labels
1. Go to <https://console.deepgram.com> and sign up. Free credit is added automatically.
2. Open your project, then **Settings → API Keys → Create a New API Key**, copy it.
3. In `.env`: `DEEPGRAM_API_KEY=...`

Without it everything works except **who is speaking**: all lines show `SPEAKER_UNKNOWN`, and Phase 2 loses its "second opinion" signal.

Then run `bash run.sh --check`. Expected output:
```
Groq      OK       key works, all needed models available (...)
Deepgram  OK       key works
```
Never commit `.env` (it is already in `.gitignore`) and never paste keys into chat or tickets. If a key leaks, delete it in the console and create a new one.

---

## Using the web app

1. Choose an audio file (`.wav .mp3 .m4a .flac .ogg .opus .webm .aac`, up to 500 MB / 4 hours) and click **Process**. A status line shows each stage.
2. **Phase 1 results** appear in this order: the transcript, then the numbers, then names, then terms/acronyms, dates and times, negations and the candidate lists. Everything shows its timestamp.
3. Click **Refine transcript**. You get:
   - **Speakers**: who speaks, with names only where the recording states them (quote shown). Accept, reject, or type a name yourself.
   - **What changed**: raw and refined text side by side; red = removed, green = added.
   - **All edits**: every fix with its evidence, the model's reason, the verifier's verdict, and Accept / Reject / Reset buttons. Blocked edits show why.
   - **Refined transcript with speakers**, and downloads (transcript, diff, CSV, JSON).

4. Click **Generate minutes**. You see a live step list, then **A. every model step in order** (extraction windows with kept/dropped records and reasons, the verifier's verdicts, links between items, topics, bullets and their checks, all model calls) and **B. the final report** (executive summary, topic timeline, registers with evidence, items needing review, notes, run trace), plus downloads (Markdown, JSON, CSV of action items, step log, trace).

"Open an earlier result" (top right) reloads any previous job from disk, including after a restart.

Try the audio in `samples/` first (see `samples/README.md`). Bad inputs in `samples/bad/` demonstrate the error messages.

---

## How it works

```
audio
 │  Phase 1 (app/preprocessing, app/audio, app/asr)
 ├─ validate  → decode to 16 kHz mono → split at pauses (<10 min chunks)
 ├─ speech-to-text (API) → RAW transcript (timestamped segments, word timings)
 ├─ normalize (whitespace/duplicates only; wording untouched) → NORMALIZED transcript
 └─ extract protected facts + meeting context (every item points back to its segment)
 │
 │  Phase 2 (app/refinement)
 ├─ second opinion from the other ASR + speaker labels
 ├─ small LLM proposes exact edits → code checks every edit → larger LLM verifies
 ├─ REFINED transcript + change log + speaker names
 │
 │  Phase 3 (app/minutes, LangGraph)
 ├─ extract records per window → verify each against the transcript → link decisions/proposals/questions
 ├─ topics → topic minutes → executive summary (every sentence checked)
 ▼
 MINUTES (Markdown + JSON + CSV of action items) and the full step-by-step model log
```

### Phase 1: what runs where

| Step | Tool / model | Notes |
|---|---|---|
| Validation, decoding, chunking | PyAV, numpy, soundfile (local) | Empty, corrupt, silent, too-short or wrong-format files give a clear message. Your original file is never modified. |
| Speech-to-text | **Groq `whisper-large-v3`** (default) or **Deepgram `nova-3`** (adds speakers) | Switch in the dropdown or `ASR_PROVIDER`. Results are cached, so re-running a file costs no API call. |
| Normalization | pure Python | Whitespace, exact duplicates and Whisper's "no speech" hallucinations only. The raw transcript is kept untouched. |
| Numbers, dates, times, durations | regex + a spoken-number parser | Exact original text kept (`81.73%`, `2x`, `$200`, `v2.1`, "twenty five percent"). Relative dates are never resolved. |
| Names | spaCy `en_core_web_md` | Plus a low-confidence capitalisation check. |
| Technical terms, acronyms | acronym patterns, `wordfreq` word rarity, TF-IDF noun phrases | Discovered per meeting, no domain word lists. Words in shaky ASR regions are flagged "ASR-suspect". |
| Negations | spaCy dependency parse | Cue plus its scope ("don't" → "need 81.73 percent accuracy"). |
| Questions, proposals, commitments, decisions, tasks, rejections, hedges | **LLM** (`LLM_MODEL`, default `openai/gpt-oss-120b`) | Only *candidates*. Every quote is checked to be verbatim; no owner or deadline is ever inferred. Prompt: `prompts/candidates_v1.md`. |

### Phase 2: how hallucination is kept down

| Step | Tool / model | Notes |
|---|---|---|
| Second opinion | the *other* ASR provider | Spots where two recognisers disagree, e.g. "Jessie" vs "Jesse". |
| **Who speaks** | Deepgram diarization (voices) copied onto the text by timestamp | Acoustic, not an LLM guess. Mixed or uncovered lines are flagged, never split. |
| Proposing fixes | **`openai/gpt-oss-20b`** (small, cheap) | Returns only `{find, replace, kind}` edits, never rewritten text. Prompt: `prompts/refine_v1.md`. |
| Checking fixes | code, no LLM | Must be verbatim and unique; no overlap with numbers / dates / times / negations; no new digits; no deletions; size limits; then the Phase 1 extractors are **re-run** and must find the same facts. |
| Evidence rule | code | Auto-applied only if the new text appears elsewhere, the second ASR heard it, or it matches a Phase 1 term, **and** it sounds/looks like the original. Otherwise it is only *suggested*. |
| Verification | **`openai/gpt-oss-120b`** (larger) | Must confirm same meaning and no new information. Prompt: `prompts/verify_v1.md`. |
| **Who they are** | LLM proposal + code check | Names only from what people say ("my name is…"), with a quote that must be verbatim and spoken by that voice. Weaker evidence ("Rose, can you…") is only suggested. No evidence = stays `SPEAKER_01`. Prompt: `prompts/speakers_v1.md`. |

Every edit ends as **applied**, **suggested** (you decide) or **blocked** (a rule failed, with the reason; it cannot be forced in).
The whole transcript is re-checked at the end: segment count, ids, timestamps, speakers, numbers, dates/times and negations must be unchanged, or the stage refuses to finish.
Details: `docs/phase1/CONTRACT.md`, `docs/phase2/CONTRACT.md`.

### Outputs
Everything is written to `outputs/<job_id>/`:
`raw_transcript.txt|json`, `normalized_transcript.json`, `protected_facts.json`, `meeting_context.json`, `phase1_result.json`, `asr_raw.json` (provider response, verbatim) and, after refining, `refined_transcript.txt|json`, `edits.csv|json`, `diff.txt`, `speakers.json`, `refinement.json`.
`outputs/cache/` stores API results so repeat runs are free. `samples/example_outputs/` has one saved Phase 1 result.

---

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY` | – | required |
| `DEEPGRAM_API_KEY` | – | optional (speaker labels) |
| `ASR_PROVIDER` | `groq` | `groq` or `deepgram` |
| `ASR_MODEL` | provider default | override the speech model |
| `CANDIDATE_DETECTOR` / `LLM_MODEL` | `llm` / `openai/gpt-oss-120b` | candidate labelling in Phase 1 (`off` to disable) |
| `REFINE_MODEL` / `REFINE_REASONING_EFFORT` | `openai/gpt-oss-20b` / `medium` | the small refiner |
| `VERIFY_MODEL` / `VERIFY_ENABLED` | `openai/gpt-oss-120b` / `true` | the checker |
| `REFINE_USE_SECOND_ASR` | `true` | use the other ASR as evidence and for speaker labels |
| `LLM_BASE_URL` | Groq | any OpenAI-compatible endpoint (e.g. OpenRouter; also change the key) |
| `MINUTES_EXTRACT_MODEL` / `MINUTES_VERIFY_MODEL` / `MINUTES_TOPICS_MODEL` / `MINUTES_WRITE_MODEL` | 120b / 120b / 20b / 20b | Phase 3 models per stage |
| `MINUTES_MAX_SECONDS` / `MINUTES_CONCURRENCY` | `900` / `2` | hard time limit for one minutes run, and simultaneous model calls |
| `CACHE_ENABLED`, `CHUNK_SECONDS` | `true`, `600` | caching, audio chunk size |

Models are swappable here without touching code. Prompts live in `prompts/` and can be edited directly (an edit automatically bypasses old cached answers).

---

## Project structure

```
.
├── run.sh                  one command: install + start (see Quick start)
├── README.md               this file
├── requirements.txt        all dependencies (incl. the spaCy model)
├── .env.example            copy to .env and add your keys
├── app/
│   ├── api/                FastAPI server (upload, jobs, refine, decisions, downloads)
│   ├── asr/                Groq + Deepgram providers, retry/rate-limit handling
│   ├── audio/              validation, decoding, silence-aware chunking
│   ├── preprocessing/      Phase 1: normalization, extractors, LLM candidate labelling, pipeline
│   ├── refinement/         Phase 2: edit proposals, gatekeeper, verifier, speakers, second ASR
│   ├── minutes/            Phase 3: LangGraph workflow, extraction, grounding checks, verifier, reconcile, topics, summaries, step log
│   ├── llm/                small OpenAI-compatible chat client
│   ├── schemas/            typed data models (Pydantic)
│   ├── config/             settings (.env)
│   └── utils/              errors, logging, cache
├── prompts/                every LLM prompt, versioned
├── web/                    the interface: index.html + static/app.js, app.css
├── scripts/                check_keys, run_phase1 (CLI), fetch_sample, benchmark_asr, eval_refinement (+ eval_data/)
├── tests/                  199 tests (APIs mocked, no keys needed)
├── samples/                test audio (AMI meetings, CC BY 4.0), bad inputs, an example output
├── docs/
│   ├── brief/              the original problem statement and project notes
│   ├── phase1/             PLAN.md, CONTRACT.md
│   └── phase2/             PLAN.md, CONTRACT.md (includes measured results)
└── outputs/                generated at run time (git-ignored)
```

---

## Tests and measurements

- `bash run.sh --test`: 199 tests covering validation errors, extraction invariants, the edit gatekeeper, speaker logic, the API and failure paths. LLM and ASR calls are mocked, so they need no keys. The real Groq and Deepgram calls were checked separately (see below).
- `python scripts/eval_refinement.py --refine-model openai/gpt-oss-20b --list` benchmarks the refiner on 12 hand-written passages with 13 injected mishearings (and 3 clean controls). Measured with the default settings: **11 of 13 fixed, 0 false edits, 0 changes to numbers/negations**. A larger refiner (`gpt-oss-120b`) fixed 13 of 13 but is slower and costs more tokens: set `REFINE_MODEL=openai/gpt-oss-120b` to use it. The fixture is small and written by the pipeline's author, so it is indicative, not a general accuracy figure. Full table in `docs/phase2/PLAN.md`.
- Live checks done: both providers on real audio; a 4-person AMI meeting where all four speakers were named from their own introductions, with five of 64 lines unassigned or flagged mixed; the interface was rendered in a real browser (not click-tested).
- `python scripts/benchmark_asr.py FILE --provider groq --provider deepgram` measures ASR latency.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "GROQ_API_KEY is not set" | Paste the key into `.env` (not `.env.example`), then `bash run.sh --check`. |
| "…rate limited" / HTTP 429 | Free-tier limit. The app retries; wait a minute and try again, or use a shorter clip. |
| Port already in use | `PORT=8001 bash run.sh` |
| Install failed / broken environment | `rm -rf .venv .venv_dir` then `bash run.sh` again (needs internet). |
| `Permission denied: run.sh` | Always start it with `bash run.sh`. |
| All speakers show `SPEAKER_UNKNOWN` | Add a Deepgram key, or select the `deepgram` provider. |
| A refinement you don't want | Click Reject on that edit. Rejected edits are saved in the outputs. |
| Server restarted and the page is blank | Results are on disk in `outputs/`; re-upload the same file (the speech result is cached, so it is quick and makes no new ASR call). |

---

## Known limits and weaknesses

- English only. 4 hours / 500 MB maximum per recording.
- Speaker voices come from Deepgram; a segment where two people talk can only be flagged, not split.
- Name detection is imperfect: it can mislabel an acronym or a rare word as an organisation, and the capitalisation heuristic can list ordinary sentence-initial words ("It", "Maybe"). Both are marked low confidence.
- Negations come only from the spaCy parser, so some forms ("no budget") can be missed.
- Number, date and time detection uses fixed format patterns by design, because exact numbers must be preserved. Everything about *meaning* (proposals, decisions, tasks, names of speakers) is model-judged and verified, not keyword-matched.
- Models can be wrong. Candidates are not confirmed decisions or tasks; suggested edits and names are only proposals until you accept them.
- Job status lives in memory; results persist in `outputs/`.

## Credits
Sample meetings are excerpts from the [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/) (CC BY 4.0). `jfk.flac` is US public domain.
