# Phase 1 output contract

`POST /process-audio` (multipart: `file`, optional `provider`) → `{"job_id"}`.
`GET /jobs/{id}` → `{stage, message, error?, result?}`; stage ∈ queued, validating, preparing, transcribing,
normalizing, extracting, done, failed. `GET /jobs/{id}/download` → `phase1_result.json`.

`result` (= `ProcessingResult`):
| Field | Meaning |
|---|---|
| `raw_transcript` | ASR output exactly as returned: `segments[]` of `{segment_id, speaker, speaker_known, start, end, text, words[], avg_logprob, no_speech_prob, confidence, language}`. Timestamps are global seconds. |
| `normalized_transcript` | Same segments (same ids) after whitespace/duplicate/no-speech cleanup; `notes[]` lists every dropped segment and why. Wording is never changed. |
| `protected_facts` | Lists of facts, each with provenance (below). |
| `meeting_context` | duration, speaker stats, entity summary, discovered terms, topic hints per time window. |
| `audio_metadata`, `timings_seconds`, `warnings`, `cache_hit`, `provider`, `model` | run info |

## Fact item (provenance)
`{type, value, surface, segment_id, start, end, char_start, char_end, confidence, source, extra}`
- `surface` is the exact text; `value` a normalized form (e.g. `81.73` for `81.73%`). Never rewritten.
- `char_start/char_end` index the **normalized** text of segment `segment_id`; `start/end` come from word timestamps when available, else the segment.
- `confidence` ∈ high | medium | low | unknown: it reflects the extraction method (regex = high, parser/heuristic lower, NER = unknown). It is never invented.

## protected_facts buckets
`numbers` (kind: number/percent/money/multiplier/range/version/spoken_*; `unit_candidate`), `dates`/`times`/`durations`
(`resolved:false` — relative expressions are not resolved), `people`/`organizations`/`locations`/`other_entities` (spaCy NER),
`proper_noun_candidates` (capitalization heuristic), `technical_terms`/`acronyms` (`TermFact`: kind, sources, count, confidence,
`asr_suspect` + reason, mentions[]), `negations` (spaCy dependency-parse `neg` cues + `extra.scope`, `extra.emphasis`; no word list),
`candidate_questions|commitments|proposals|decisions|tasks|rejections`, `uncertainty`.

Candidates are judged by an LLM (`prompts/candidates_v1.md`, model `LLM_MODEL`), not by keyword rules. Each carries
`extra.status = "candidate"`, `extra.label`, `extra.said_by` (speaker id only if the provider labelled speakers),
`source = "llm:<model>"` and `confidence = "unknown"` (an LLM has no calibrated score). `value` is a quote that was
verified to be an exact substring of the segment; anything else the model returns is discarded and counted in `warnings`.
They never carry an owner or deadline. If the LLM is off or unreachable the buckets are empty and a warning says so (no rule-based fallback).

## Errors
`{error: {code, message}}`, message is user-safe. Codes: EMPTY_FILE, FILE_TOO_LARGE, UNSUPPORTED_FORMAT, UNREADABLE_AUDIO,
NO_AUDIO_STREAM, TOO_SHORT, TOO_LONG, SILENT_AUDIO, NO_SPEECH, API_KEY_MISSING, API_KEY_INVALID, API_RATE_LIMITED,
API_UNAVAILABLE, API_PAYLOAD_TOO_LARGE, API_ERROR, UNKNOWN_PROVIDER, NLP_MODEL_MISSING, INTERNAL_ERROR.
Stack traces go to `outputs/logs/phase1.log` only.
