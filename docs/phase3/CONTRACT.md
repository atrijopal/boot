# Phase 3 output contract

## API
- `POST /jobs/{id}/minutes` starts a run (needs Phase 2). Progress: `GET /jobs/{id}` -> `minutes.{stage,message,done[],running[],result?,error?}` (`stage` is `running`, `done` or `failed`; `done`/`running` list graph nodes).
- `GET /jobs/{id}/minutes` -> minutes JSON plus `stale` (true if the refined transcript or speaker names changed afterwards).
- `GET /jobs/{id}/minutes/stages` -> the step log (below). `GET /jobs/{id}/minutes/download?format=md|json|csv|trace|stages`.
- `GET /jobs` lists earlier jobs on disk; `GET /jobs/{id}` rebuilds a job (Phases 1-3) from disk after a restart.

## minutes.json (`schema_version: "minutes/1"`)
`meta`, `status` (`complete` | `partial`), `participants`, `executive_summary[]` (claims with `item_ids`), `topics[]` (with `claims[]`), `items[]`, `needs_review[]`, `relations[]`, `warnings[]`, `stats`, `trace_summary`, and `registers`.
`registers` holds the same item objects grouped as `decisions`, `action_items`, `open_questions`, `proposals`, `disagreements`, `risks`, `key_figures`; the Markdown report is rendered from the same objects.

Item: `{item_id, lens, text, status, attrs, evidence[], start, speakers[], verification{status,notes,model}, superseded_by, resolved_by, related[]}`.
- `evidence[]`: `{segment_id, quote, start, end, speaker, speaker_name, char_start, char_end}`; `quote` is verbatim from the refined transcript.
- Action `attrs`: `owner {text, basis: first_person | named_in_quote | unspecified}` and `deadline` (text or null). Missing owner or deadline is shown as "unspecified"; nothing is guessed.
- Fact `attrs`: `label`, `value` (as written), `unit`, `flags[]` (for example a possible misplaced decimal).
- Statuses per kind are listed in `app/minutes/lenses.py`; `superseded` is set only by reconciliation.

## stages.json (the step log shown in the interface)
`{meta: {models, efforts, extract_mode, prompt_hashes, chunks[]}, calls[], outcomes{}}`
- `calls[]`: every model call: `{call_id, node, ref, prompt, model, effort, input, output, cache_hit, secs, prompt_tokens, completion_tokens, retries, error}`.
- `outcomes`: keep/drop decisions our code made: `extraction` (kept/dropped + reason + evidence), `merge`, `verification` (verdict fields + outcome), `reconcile`, `topics`, `topics_final`, `topic_claims`, `executive_claims`.
- Contains the transcript text that was sent to the models; never API keys.

## Files in `outputs/<job>/minutes/runs/<run_id>/`
`minutes.json`, `minutes.md`, `action_items.csv`, `stages.json`, `trace.jsonl`; `outputs/<job>/minutes/latest.json` points at the newest run.
