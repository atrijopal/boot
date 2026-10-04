# Phase 2 output contract (transcript refinement)

Input: a finished Phase 1 job (`outputs/<job>/phase1_result.json`). Output: a refined transcript plus a change log.

## API
- `POST /jobs/{id}/refine` starts refinement (or send `auto_refine=true` with `/process-audio`). Status: `GET /jobs/{id}` → `refine.{stage,message,result?,error?}`;
  stage ∈ queued, second_asr, proposing, validating, verifying, done, failed.
- `POST /jobs/{id}/edits/{edit_id}` body `{"decision": "accept"|"reject"|"reset"}` → updated result. 409 if the edit is blocked or accepting it would change protected facts.
- `GET /jobs/{id}/refinement` → full result. `GET /jobs/{id}/refined?format=txt|diff|csv|json|edits` → downloads.

## Result (`RefinementResult`)
`refined_transcript` (same segments/ids/times/speakers as the normalized transcript, only `text` changes), `edits[]`, `invariants`, `stats`, `warnings`, `timings_seconds`, `refine_model`, `verify_model`, `second_asr`.

## Edit
`{edit_id, segment_id, start, end, kind, find, replace, char_start, char_end, reason, status, reject_reason, support[], similarity, verdict, unverified, user_decision}`
- `char_start/char_end` locate `find` in the normalized segment text. `reason` is the model's explanation and is NOT evidence.
- `status`: **applied** (auto: passed every rule, has evidence, similar to the original, verifier agreed), **suggested** (valid but unsupported or doubted by the verifier: a human decides), **rejected** (blocked by a rule; `reject_reason` says which; can never be forced in).
- `support[]` kinds: `elsewhere` (same text appears elsewhere in the transcript), `second_asr` (the other ASR heard it), `phase1_term` (matches a Phase 1 term/name), `similarity` (sounds/looks like the original).
- `unverified`: applied while the verifier was off or unreachable.
- Effective state = `user_decision` if set (but a rejected edit never applies), else `status == applied`.

## Speakers
- **Voices (who speaks)** are acoustic, never an LLM guess. `speaker_source` is `provider` (Deepgram primary), `transplanted:<provider>` (Deepgram's diarized words copied onto the primary text by timestamp overlap) or `none`.
  `segment_speakers[segment_id] = {speaker, speaker_known, share, mixed, runner_up, runner_up_share}`; `mixed` means a second voice also talks in that segment (segments are never split, so ids stay stable). Uncovered segments stay `SPEAKER_UNKNOWN`.
- **Names (who they are)** are proposed by an LLM (`prompts/speakers_v1.md`, refiner model) and verified in code. `speaker_names[]`: `{name_id, speaker, name, basis, segment_id, start, quote, status, reject_reason, note, user_decision}`.
  Rules: quote verbatim in the named segment and containing the name; plausible personal name (capitalised, <=4 words); `self_introduction` is auto-applied only if the quote is spoken by that same speaker;
  `addressed_by_others` / `introduced_by_others` are only ever **suggested** (a reply check is noted); a speaker with contradicting names, or one name claimed for two speakers, is demoted to suggestions; no evidence means the label stays `SPEAKER_xx`.
- Display name per segment is `refined_transcript.segments[].speaker_name` (a typed name wins, then accepted/applied proposals). The stable `speaker` id never changes.
- API: `POST /jobs/{id}/speaker-names/{name_id}` `{decision}`; `POST /jobs/{id}/speakers/{speaker}/name` `{name}` (empty clears); `GET /jobs/{id}/refined?format=speakers`.
- Edits are checked against the speaker-labelled baseline, so refinement still cannot change who said what.

## Rules enforced in code (no LLM)
`find` verbatim and unique; kind in term/acronym/name/spelling/format; no deletions; ≤8 words each side; no new digits; `format` edits keep letters;
no overlap with a number/date/time/duration/negation span or another edit; ≤6 edits per segment; cumulative change ≤ 50% of the segment (30-char floor);
then the Phase 1 extractors are re-run on the edited text and numbers, dates/times/durations and negation cues must be identical.
`invariants` re-checks the whole transcript (segment count, ids/times/speakers, numbers, dates/times, negations) and the stage refuses to finish if any fails.

## Files in `outputs/<job>/`
`refinement.json`, `refined_transcript.json|.txt`, `edits.json|.csv`, `diff.txt`.
