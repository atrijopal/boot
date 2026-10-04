# Phase 2 Plan — Transcript Refinement (LLM) + "What changed" interface

## Context
Phase 1 (done, 52 tests) produces: raw + normalized transcript (timestamped segments), protected facts with provenance
(numbers, dates, times, durations, names, terms/acronyms with ASR-suspect flags, negations, LLM-labelled candidates) and meeting context.
Phase 2 = **LLM refinement stage #1** of the PS: fix plausible ASR errors in technical terms / acronyms / names while
preserving meaning, numbers, negation and commitments, and **show exactly what changed**.
Everything Phase 1 built is the input; nothing in Phase 1 moves or is rewritten.
Minutes/decisions/tasks are Phase 3 (separate LLM stage), not in this plan.

## Questions answered
- **Do we need OpenRouter?** No. Your Groq key already lists `openai/gpt-oss-20b`, `openai/gpt-oss-120b` and `qwen/qwen3.8-27b`. No new key.
  The LLM client stays OpenAI-compatible with a configurable base URL, so OpenRouter can be added later (only if Groq rate limits bite) by adding a key + `LLM_BASE_URL`.
- **Models (your choice: small refiner, big verifier):**
  **Refiner:** `openai/gpt-oss-20b` on Groq. It reads the whole transcript (many input tokens) but outputs only a few edits, so the cheap model goes where the tokens are.
  **Verifier:** `openai/gpt-oss-120b`. It sees only the edited segments (before/after, a few hundred tokens), so the strong model costs little and guards precision (hallucinations that slip past the deterministic checks).
  **Phase 3 minutes model (later):** also `openai/gpt-oss-120b` (or Qwen), a separate stage from refinement as the PS requires.
  Trade-off accepted: a small refiner may *miss* some fixes (recall), but cannot introduce unchecked ones (precision is protected by the gatekeeper + big verifier). Refiner and verifier are the same family (different size), so they are less independent than two different families; `VERIFY_MODEL=qwen/qwen3.8-27b` is the one-line swap if the benchmark shows correlated mistakes.
  These are **not yet measured**. The benchmark compares 20b vs 120b as refiner on identical inputs; `REFINE_MODEL` / `VERIFY_MODEL` switch with an env var.

## Anti-hallucination design (from 02_approaches + additions)
1. **Edit-only output.** The LLM returns `{segment_id, find, replace, kind, reason}`; it never returns rewritten text. Everything not listed stays byte-identical. Segment ids, timestamps, speakers are never touched (no merge/split/drop).
2. **Deterministic gatekeeper (no LLM) per edit**, from Phase 1 facts:
   - `find` must be an exact substring (unique) of the normalized segment; `kind` ∈ {term, acronym, name, spelling, format} else rejected.
   - Edit may not overlap a protected span (number, date, time, duration, negation cue).
   - **Invariant re-check:** apply the edit, re-run Phase 1 extractors (`extract_numbers`, `extract_dates/times/durations`, `extract_negations`) on the new text; values/cues must equal the original. Replacement may contain no new digits. `format` edits must keep lowercase-alphanumerics identical.
   - Size limits: ≤8 words each side, ≤30% of a segment's characters changed, ≤6 edits per segment.
3. **Evidence requirement (anti-invention).** A replacement is only auto-applied if it has *support*:
   (a) the same token already appears elsewhere in the transcript, or (b) a second ASR heard it at that time, or (c) it matches an acronym/term/entity Phase 1 discovered;
   AND it is phonetically/character-similar to the original (`jellyfish` metaphone / ratio) unless (b) holds. No support = **suggested only** (human decides in the UI), never silently applied.
4. **Second-ASR disagreement signal (optional, on by default when both keys exist).** Run the other provider (Groq↔Deepgram) on the same audio, align word-by-word with `difflib`, and pass disagreement spans + their confidence to the prompt. Live evidence: the two ASRs spelled the same name "Jessie" vs "Jesse".
5. **Second-model verification.** For edits that pass 1–3, `gpt-oss-120b` (larger than the refiner) answers per edit: same meaning? new information introduced? Failure → downgrade to suggested. Verifier down → only edits with support (a/b/c) auto-apply, flagged "unverified".
6. **Prompt-injection firewall** (transcript = delimited data), temperature 0, JSON mode, prompts stored in `prompts/` and versioned, results cached by (model, prompt, batch).
7. **Three outcomes per edit:** `applied` (auto), `suggested` (valid but unsupported/unverified, user can accept), `rejected` (violated a rule; reason shown). Nothing is applied that fails invariants, even if the user clicks accept (server re-validates).

## Pipeline (new package `app/refinement/`)
| File | Role |
|---|---|
| `context.py` | Build prompt context from Phase 1: terms/acronyms/names (+ASR-suspect flags), locked spans, per-segment hints |
| `second_asr.py` | Optional second provider run (reuses `get_provider`, `cache`), `difflib` word alignment → disagreement spans |
| `llm_edits.py` | Batched LLM call (≈25 segments + 2 context), parse edits (reuses `app/llm/client.chat_json`, `cache.load_key/store_key`) |
| `validate.py` | Gatekeeper + support scoring + invariant re-check (reuses `extract_numbers`, `extract_dates`, `extract_times`, `extract_durations`, `extract_negations`, `parse_segments`, `common.span_times`) |
| `verify.py` | Second-model semantic check |
| `apply.py` | `render_refined(normalized, edits, decisions)`: right-to-left apply, overlap resolution, recompute after user accept/reject |
| `pipeline.py` | `run_refinement(job_id)`: loads `outputs/<job>/phase1_result.json`, writes `refined_transcript.json/.txt`, `edits.json/.csv`, `diff.txt`, `refinement.json` |
Schemas: `app/schemas/refinement.py` (`Edit`, `RefinementResult`, invariant report). Prompts: `prompts/refine_v1.md`, `prompts/verify_v1.md`.
Settings added: `REFINE_MODEL`, `VERIFY_MODEL`, `REFINE_BATCH_SEGMENTS`, `REFINE_USE_SECOND_ASR`, `VERIFY_ENABLED`, `LLM_BASE_URL`.

## API (extend `app/api/main.py`)
- `POST /jobs/{id}/refine` → background job; status in `GET /jobs/{id}` under `refine` (stages: context, second_asr, proposing, validating, verifying, done/failed). Optional `auto_refine` flag on `/process-audio`.
- `POST /jobs/{id}/edits/{edit_id}` `{decision: accept|reject|reset}` → recompute refined text (server re-checks invariants).
- `GET /jobs/{id}/refined?format=json|txt|diff|csv` downloads.

## Change interface (extend `web/index.html`, same minimal vanilla-JS style)
"Phase 2 — Refinement" section under the Phase 1 results:
- Button **Refine transcript** (+ status line).
- Summary chips: applied / suggested / rejected counts; **invariant badges** (numbers, dates/times, negations, speakers, segment count: ✓ unchanged).
- **Raw vs refined, side by side**, changed segments only (toggle "show all"), with `<del>` / `<ins>` highlighting at the edit span, timestamp and speaker.
- **Edit table:** time · `original → replacement` · kind · status chip · evidence ("also appears at 3:12", "Deepgram heard: …", "matches acronym found in Phase 1") · LLM reason · verifier verdict · reject reason. **Accept / Reject / Reset** buttons per edit; refined text updates live.
- Downloads: refined transcript (.txt/.json), edits (.json/.csv), diff.

## Build order
1. Write `docs/phase2/PLAN.md` (this plan) + schemas + settings + prompts.
2. `validate.py` + `apply.py` first (pure code, fully unit-tested before any LLM).
3. `context.py`, `llm_edits.py`, `verify.py`, `second_asr.py`.
4. `pipeline.py`, API endpoints, UI.
5. Evaluation (below), then docs (`docs/phase2/CONTRACT.md`, README) and final report.

## Verification
- **Unit tests (no network):** number/date/time changed → rejected; negation dropped → rejected; overlap with protected span → rejected; non-verbatim/ambiguous `find` → rejected; unsupported name/term → suggested not applied; new digits → rejected; size limits; overlapping edits; render/recompute after accept/reject; accepting an invariant-breaking edit returns an error; LLM, verifier and second ASR all mocked; injection text stays data; segment count/ids/timestamps identical after refinement.
- **Measured benchmark** (`scripts/eval_refinement.py`): a small hand-written test set (~12 short passages across unrelated domains with injected phonetic ASR errors, gold fixes, and numbers/negations that must not change; a test fixture, not pipeline logic). Reports per model (20b vs 120b): fixes recovered, false edits, invariant violations (target 0), latency. Plus runs on the real AMI excerpts, listing every edit for manual review. Only measured numbers are reported; no accuracy claims otherwise.
- **End to end:** start server, upload `samples/ami_*.flac`, click Refine, check side-by-side view, accept/reject, download files.
- Not decided by me: nothing new needs keys. Deepgram key is reused for the second-ASR signal.

---
## As built: deviations and measured results
- **Similarity** uses sequence ratio + consonant-skeleton (metaphone) only. Jaro-Winkler was dropped: it scored `whale`/`turtle` at 0.58.
- **Size cap** is cumulative 50% of the segment with a 30-character floor (first try: 30%/20 chars blocked two valid fixes in one short sentence).
- **Reasoning effort** is configurable (`REFINE_REASONING_EFFORT`); default `medium`. An empty reply (`json_validate_failed`) is retried once at low effort.
- **Benchmark** (`scripts/eval_refinement.py`; 12 hand-written passages, 13 injected errors, 3 control passages; verifier `gpt-oss-120b`; Groq free tier, latencies include 429 retries):

| Refiner | Fixed (applied + suggested) | Missed | False edits | Invariant violations | Notes |
|---|---|---|---|---|---|
| gpt-oss-20b, low | 9/13 | 4 | 0 | 0 | ~17 s |
| gpt-oss-20b, medium (default) | 11/13 | 2 | 0 | 0 | 40-63 s across two runs |
| gpt-oss-20b, high | 11/11 completed | 0 | 0 | 0 | 99 s, one empty reply (before the retry was added) |
| gpt-oss-120b, low | 13/13 | 0 | 0 | 0 | ~54 s |

  Small fixture written by the same author as the pipeline, so treat it as indicative only. Runs at temperature 0 can still vary.
- **Real meeting** (AMI IS1000a excerpt, 39 segments): 2 edits proposed; `shirt folder` -> `shared folder` applied (3 kinds of evidence), `his face` -> `this phase` left as suggested.
- The verifier is the same model family as the refiner (different size); swap with `VERIFY_MODEL=qwen/qwen3.8-27b` if correlated mistakes show up.

## As built: speakers (added after the first Phase 2 build)
- Decision: *who speaks* = acoustic (Deepgram diarization copied onto Groq's text by timestamp overlap); *what they are called* = LLM proposal verified in code. An LLM is not used to guess voices from text.
- Live check on the 4-person AMI ES2008a excerpt (Groq text + Deepgram labels): 4 speakers found, all 4 named through their own self-introductions with verbatim quotes (Rose, Alima Bucintini, Ian, Jessie); 5 of 64 segments had no diarized coverage (`SPEAKER_UNKNOWN`) and 5 were flagged as mixed. One meeting only, so not a general accuracy figure.
- UI checked in a headless Firefox render of the real result (speaker panel, raw-vs-refined view, edit table, refined transcript with names).
