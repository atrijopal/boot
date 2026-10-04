# Phase 1 Plan (API-first) — Audio → Raw Transcript + Preprocessing + Minimal Web UI

## Goal
Everything **before** the refinement LLM: validate → prepare audio → speech-to-text (via **API**) → raw transcript → normalize → extract facts/context. No LLM is called in Phase 1.
Meetings can be about **anything**: no fixed vocabulary, no fixed names or term lists. Everything is discovered from the uploaded recording.

## What changed vs. the first plan
- **No local model, no GPU, no torch, no faster-whisper, no pyannote, no HF token.** All heavy inference runs on provider APIs.
- Local CPU work is limited to cheap things: decode/resample/chunk audio, regex, spaCy `en_core_web_sm` (12 MB), TF-IDF, `wordfreq`.
- Install size drops from ~6 GB to a few hundred MB.

## Keys needed
| Key | Used for | Required |
|---|---|---|
| `GROQ_API_KEY` (free, console.groq.com) | Speech-to-text, `whisper-large-v3` | **Yes** (default provider) |
| `DEEPGRAM_API_KEY` (free credit, console.deepgram.com) | Speech-to-text **with speaker diarization**, `nova-3` | Optional |
Stored in `.env` (git-ignored), never hardcoded. Everything except the live ASR call can be built and tested without keys.

## Pipeline, step by step
| # | Step | Where it runs | Tool / model | Exactly what it does |
|---|---|---|---|---|
| 1 | File validation | local, CPU | PyAV, own checks | File exists; size > 0; extension and magic bytes in allowlist (wav, mp3, m4a, flac, ogg, webm, aac); decodes without error; duration between 1 s and 4 h; has signal (RMS/peak not ~0). Fail → `PipelineError(code, user_message)` shown in the UI (unsupported / empty / corrupt / too short / silent). No stack traces to the user. |
| 2 | Audio preparation | local, CPU | PyAV + numpy | Decode → mono (channel mean) → 16 kHz float32. Original file is never modified. Saves metadata: original sample rate, channels, duration, processed rate, RMS, peak, clipped %. |
| 3 | Silence-aware chunking | local, CPU | numpy energy VAD | Groq free tier limits upload size (25 MB) and rate. Audio is split into chunks of at most ~10 min, cutting only at the quietest point near the boundary so words are not cut. Each chunk is saved as FLAC (lossless, via soundfile). Chunk offsets are kept so timestamps can be made global. Short audio = a single chunk. |
| 4 | Speech-to-text | **API** | **Groq `whisper-large-v3`** (fallback `whisper-large-v3-turbo`), `response_format=verbose_json`, `timestamp_granularities=[word, segment]`, `language=en`, `temperature=0` | One request per chunk. Returns segments (+ `avg_logprob`, `no_speech_prob`) and word timestamps. Retries with backoff on 429/5xx (honours `retry-after`). Raw API responses are stored verbatim (`asr_raw.json`). Chunk timestamps + offset = global timestamps. |
| 4b | Optional alt provider | **API** | **Deepgram `nova-3`** (`diarize=true`, `utterances=true`, `punctuate=true`) | Whole file in one request (no chunking). Gives word timestamps, word confidence and **speaker labels**. Selected with `ASR_PROVIDER=deepgram`. |
| 5 | Speaker labels | from API | Deepgram diarization (only if provider supports it) | `SPEAKER_00`, `SPEAKER_01`, …, `speaker_known=true`. With Groq: `SPEAKER_UNKNOWN`, `speaker_known=false`. Names are never invented. |
| 6 | Raw transcript | local | Pydantic schemas | Stored as timestamped segments `{segment_id, speaker, start, end, text, words[], avg_logprob, no_speech_prob, language}`. Kept exactly as ASR produced it. |
| 7 | Normalization | local | pure Python | Separate copy: whitespace/punctuation spacing, drop identical adjacent duplicates (same text and overlapping time), drop empty segments, drop Whisper no-speech hallucinations only if `no_speech_prob` is high. Fillers, numbers, negations and hedges untouched. **This is not refinement.** |
| 8 | Extraction | local, CPU | regex, spaCy `en_core_web_sm`, `wordfreq`, TF-IDF | See next section. |

## Extraction (domain-agnostic, deterministic-first, with provenance)
Each item: `{value, type, surface, segment_id, start, end, char_start, char_end, confidence: high|medium|low|unknown, source}`.

1. **Numbers** — regex for integers, decimals, percentages, money, multipliers (`2x`), versions (`v2.1`), ranges (`10–15`, `10 to 15`), number + unit (any following word kept as `unit_candidate`, no fixed unit list). Spoken numbers (“twenty five percent”, “eighty one point seven three”) via a small number-word parser. The original surface string is always kept.
2. **Dates / times** — regex plus `dateparser` search for detection only (Friday, 7 October, October 7, next Monday, 2026-10-07, 3 PM, 15:30, around noon). **Relative dates are not resolved** unless a reference date is passed in.
3. **Names (people / organizations / places)** — spaCy NER (PERSON, ORG, GPE, PRODUCT) plus a mid-sentence-capitalization heuristic for names NER misses (lower confidence). Original spelling kept. No name list.
4. **Technical terms (discovered per meeting)** — union of: acronyms / mixed-case / alphanumeric tokens (`PSNR`, `NAFNet`, `GPT-4`); TF-IDF keyphrases from this meeting; **rarity** in general English (`wordfreq`, a language statistic, not a domain list); **ASR-suspect** words = low API word confidence **and** rare. Each term stores sources, frequency, confidence. Not every unusual token is assumed to be a term.
5. **Negations** — spaCy dependency `neg` + cue list (not, n't, no, never, none, without, cannot, no longer, instead of). Stored as `{segment_id, cue, scope}`.
6. **Candidates (labelled `candidate`, never confirmed)** — questions, commitments (“I’ll / we will / let me”), proposals (“maybe / we could / how about”), agreement/decision cues (“let’s go with / we agreed / sounds good”), task cues (“need to / action item”), uncertainty (“maybe / not sure”). The speaker is recorded as `said_by` only — **owner and deadline are never inferred**.
7. **MeetingContext** — duration, speakers + talk time, term and entity lists, TF-IDF topic hints per time window, stats.
8. **ProtectedFacts** — all of the above in one object with provenance, ready for Phase 2 validation.

Optional (off by default): an `LLMVerifier` interface slot (API LLM) to double-check term/entity candidates. Not part of Phase 1.

## Interface (very minimal)
One page: choose file → **Process** → status line. Result blocks in this order:
1. **Transcript** (time · speaker · text per segment)
2. **Numbers** (value, surface, time)
3. **Names** (people / orgs / places)
4. Technical terms (with “ASR-suspect” flag), dates/times, negations, candidate commitments/decisions/questions
5. Download JSON. Errors in a red box with a clear message.

Backend: FastAPI. `POST /process-audio` → `{job_id}`; `GET /jobs/{id}` → stage status (validating / preparing / transcribing / extracting / done / failed) and the result; `GET /health`.

## Cost and quota control
- Cache by `sha256(audio) + provider + model + config` → repeat runs make **zero** API calls.
- Groq free tier limits (~7,200 audio-seconds/hour, ~20 requests/min): handled by retry/backoff and a clear “rate limited, retrying” status.
- Chunk size is configurable. `scripts/benchmark_asr.py` reports measured latency and real-time factor per provider.

## Layout (in BOOTCAMP/)
```
app/api/main.py
app/asr/{base,groq_provider,deepgram_provider,registry}.py
app/audio/{validate,prepare,chunk}.py
app/preprocessing/{normalize,numbers,datetimes,entities,terms,negation,candidates,context,pipeline}.py
app/schemas/{audio,transcript,facts,result}.py
app/config/settings.py     configs/default.yaml   .env.example
app/utils/{cache,logging,errors}.py
web/index.html   tests/   scripts/{run_phase1,benchmark_asr,fetch_sample}.py
docs/phase1/PLAN.md  docs/phase1/CONTRACT.md   outputs/  samples/  requirements.txt  README.md
```
Per-run artifacts in `outputs/<job_id>/`: audio_metadata, asr_raw, raw_transcript (.json/.txt), normalized_transcript, protected_facts, meeting_context, phase1_result.

## Config (swappable)
`ASR_PROVIDER` (groq | deepgram), `ASR_MODEL`, `CHUNK_SECONDS`, `CACHE_ENABLED`, `GROQ_API_KEY`, `DEEPGRAM_API_KEY`. Provider interface: `ASRProvider.transcribe(chunks) -> Transcript`. Parakeet is a placeholder name only, not implemented.

## Build order
1. Venv (done: `~/.venvs/bootcamp`, Python 3.12) + light deps: av, numpy, soundfile, pydantic, fastapi, uvicorn, python-multipart, httpx, pyyaml, python-dotenv, spacy + `en_core_web_sm`, dateparser, wordfreq, scikit-learn, pytest.
2. Schemas → config/errors/logging → validate/prepare/chunk → Groq + Deepgram providers + cache → normalize → extractors → pipeline → API → UI.
3. Tests, public sample audio, live end-to-end run once keys are in `.env`.
4. README + contract doc + final report (what works / what could not be tested).

## Testing
- Unit tests, no keys: empty / 0-byte / corrupt / unsupported / silent / valid audio; chunk boundaries and timestamp offsets; ASR providers against **mocked** API responses; segment ordering and speaker labels; invariant sentences (`81.73%`, `2x`, `Friday`, `Rahul`, `I don't agree`, `We should NOT deploy this`, `I'll prepare it`, spoken numbers, acronyms, unfinished sentences); a domain-swap test (cooking / legal / medical text) showing terms are found without any code change.
- Live (needs your key): upload a public-domain sample via the UI, check transcript → numbers → names, check error box on a bad upload. Only measured numbers are reported.

---
## As built — deviations from the plan above (kept honest)
- **spaCy `en_core_web_md`** is the default (sm mislabelled names such as "Rahul" as places). Still CPU-only, no torch.
- **`dateparser` is not used**: dates/times/durations are regex-only surface detection (relative dates are never resolved anyway).
- **Groq returns no per-word confidence.** The ASR-suspect flag therefore uses segment `avg_logprob` for Groq and word confidence for Deepgram.
- NER over-labels unknown jargon as ORG, so only people/places/capitalized names are excluded from term discovery; ORG-labelled rare words stay candidates (this keeps mis-heard words visible to refinement).
- `configs/default.yaml` was dropped: configuration is `.env` + `app/config/settings.py` only.
- Added fact buckets `durations`, `candidate_tasks`, `candidate_rejections`, `uncertainty`.
- Tests: 45 pass with a mocked ASR API. Live API calls are only exercised once keys are in `.env`.

## Update: no hand-written semantic rules
- Removed all cue-phrase lists for questions, proposals, commitments, decisions, tasks, rejections and uncertainty (and the negation word list, weekday list for names, and the AM/PM/OK token list).
- Candidates now come from one LLM call per batch of segments (`app/preprocessing/llm_candidates.py`, prompt in `prompts/candidates_v1.md`, Groq `openai/gpt-oss-120b`). Safeguards: transcript sent as delimited data with an injection rule; each quote must be an exact substring of its segment; fixed label set; no owner/deadline fields; confidence "unknown"; results cached.
- Negations come only from spaCy's dependency parse; this misses some cues a word list would catch (e.g. "no budget").
- Still rule-based by design: number/date/time/duration grammar, spoken-number words, weekday/month names (exactness of protected facts).
