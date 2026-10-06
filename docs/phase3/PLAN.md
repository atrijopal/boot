# Phase 3: minutes (extract, verify, write)

Input: the refined transcript from Phase 2 (with speaker names) and the Phase 1 facts. Output: a verified record of the meeting
(decisions, action items, open questions, proposals, disagreements, risks, key figures), topic minutes, an executive summary,
and a step-by-step log of every model call.

## Workflow (LangGraph, `app/minutes/graph.py`)
```
chunk -> extract (one call per window, all kinds)  \
topics (alongside)                                   -> verify -> reconcile -> summarize -> executive
```
| Step | Model (default) | What it does | Code checks (no model) |
|---|---|---|---|
| extract | `gpt-oss-120b`, medium effort | Proposes records with quotes, per 60-segment window plus 6 segments of context each side | every quote must be in the cited segment (a neighbouring segment is accepted only if the quote is verbatim there); one quote from the target window; numbers must exist in the cited text; owner only from a first-person commitment by a labelled speaker, or a name/role that appears in the quote; deadline only if it appears in the quote |
| merge | none | joins duplicates from overlapping windows | text similarity plus shared evidence |
| verify | `gpt-oss-120b` | judges each record against the transcript around its quote; may correct a wrong status | every status has a plain-language meaning (`app/minutes/lenses.py`); a record the verifier does not confirm goes to "needs review" and is not used downstream |
| reconcile | `gpt-oss-120b` | links decisions, proposals and questions (replaces, adopts, rejects, answers) | the quote must be in the evidence of one of the two items; an earlier decision cannot replace a later one |
| topics | `gpt-oss-20b` | proposes contiguous topics | ranges are made complete, non-overlapping and capped |
| summarize | `gpt-oss-20b` | writes 2-5 bullets per topic from the topic's segments and its verified records | no new numbers; cited ids must exist; then an independent checker confirms each bullet |
| executive | `gpt-oss-20b` | writes the executive summary from topic bullets and verified records | same, plus a deterministic restatement of verified items if nothing passes |

Prompts: `prompts/minutes/*.md`. A rule-based fallback is never used for meaning: if a model call fails the affected part is reported as missing.

## Why these model choices (measured on a 10-minute AMI excerpt)
- The long combined extraction prompt returned an empty list on `gpt-oss-20b` even at high effort; `gpt-oss-120b` at medium effort worked, low effort under-extracted.
- Four per-kind prompts used about 53,000 tokens for 10 minutes and hit the 8,000 tokens-per-minute free-tier limit (36 retries, 6 failed calls). One combined call per window and moving topic work to the small model cut it to about 39,000 tokens.
- An earlier "token pacer" (waiting client-side for per-minute budget) was removed: a run on a user's recording sat for 14+ minutes with no network activity and no failed call. Simpler rules replaced it (below).
- Groq free tier (checked on their rate-limit page): 8,000 tokens per minute and 200,000 per day, per model. A 60-minute meeting would need roughly 240,000 tokens, so long meetings are not yet supported on the free tier alone.

## Bugs found by live runs that mocked tests could not catch (kept as regression tests)
1. The verifier rejected valid facts/risks/questions because it was never told what the labels `stated`, `raised`, `open` mean.
2. A quote cited under a neighbouring segment id was dropped; it is now re-anchored if (and only if) it is verbatim there.
3. The token pacer could leave a run waiting with no visible progress (removed, see above).

## Reliability rules (kept deliberately simple)
- Every model call has a 90 s timeout and at most 3 tries; a rate-limit wait is capped at 45 s. After that the call fails and is reported.
- A failed extraction window gets one more try at the end; otherwise it is listed under notes and the run is `partial`.
- One run has a hard limit (`MINUTES_MAX_SECONDS`, default 900 s). When it passes, remaining steps are skipped with a clear note instead of waiting.
- At most 2 simultaneous model calls.

## Limits
- Results vary from run to run on borderline items; recall of action items on short excerpts is incomplete (a live check missed a second task assignment before the prompt was widened to include roles).
- Not tested beyond 10 minutes. No provider fallback yet.
- The verifier is the same model family as the extractor; a different family would be more independent.
