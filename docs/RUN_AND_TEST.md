# Run and test the akansha branch

## 1. Get the updated code

In the existing repository, commit or stash your own local changes first if needed:

```bash
git switch akansha
git pull --ff-only origin akansha
```

For a new checkout:

```bash
git clone --branch akansha https://github.com/atrijopal/boot.git
cd boot
```

These commands operate on `akansha`; they do not merge into `main`.

## 2. Install and configure

### VS Code with Git Bash / Linux / macOS

```bash
bash run.sh --setup
```

Setup installs Python 3.12 and dependencies, including the local spaCy English vector model, and creates `.env` if absent. Open `.env` in VS Code, enter a **new** Groq key and optionally a **new** Deepgram key. Existing `.env` files are preserved. Restart the server after changes.

### Native Windows PowerShell alternative

Install Python 3.12 first, then run from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env`. You do not need to activate the virtual environment, so PowerShell execution-policy changes are unnecessary. Use the explicit Python path below.

### Credentials

- Default: `GROQ_API_KEY` supplies Whisper ASR and the chat models.
- Optional: `DEEPGRAM_API_KEY` enables diarization and a second ASR opinion. It is required if `ASR_PROVIDER=deepgram`.
- Alternate chat service: set `LLM_BASE_URL`, `LLM_API_KEY` and provider-supported chat model names. The app does not send your Groq credential to an alternate endpoint.
- Keep `.env.example` blank. Never commit `.env` or paste credentials in logs/issues.
- Revoke the credentials previously committed to this branch and replace them in your local `.env`. This fix does not rewrite repository history.

## 3. Offline automated tests

Git Bash:

```bash
bash run.sh --test
bash run.sh --test tests/phase3
```

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/phase3
```

Expected: all tests pass with exit code 0. No API key is needed; HTTP calls are mocked and outbound HTTP is blocked by the test fixtures. Initial installation still requires internet. Tests cover audio validation, ASR request construction, protected facts, refinement gates, speaker handling, topic returns, duration accounting, graph/summary evidence, decision revisions, stale outputs, failed-batch resume and API downloads. Configuration regression tests cover blank credentials, environment precedence and safe job paths.

## 4. Live configuration check and server

Git Bash:

```bash
bash run.sh --check
bash run.sh
```

PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/check_keys.py
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000. Health information is at http://localhost:8000/health and API documentation at http://localhost:8000/docs. Health shows whether keys are configured, not whether they are valid. `--check` contacts provider APIs, lists unavailable configured models and exits nonzero for failures. Deepgram's check also depends on the key's project-list permissions; successful checks do not guarantee remaining transcription quota.

## 5. Manual end-to-end acceptance test

1. Upload `samples/jfk.flac` for a short smoke test. For speaker labels and meaningful topic minutes, use one of the longer `samples/ami_*.flac` recordings and select Deepgram.
2. Wait for Phase 1. Confirm timestamps, transcript and protected-fact lists appear. Download the Phase 1 JSON.
3. Click **Refine transcript (Phase 2)**. Confirm the change log and invariant checks. Review proposed edits; accept/reject a permissible edit and check that the transcript updates. Blocked edits should remain blocked.
4. If speaker labels are available, review or edit a speaker name.
5. Click **Generate minutes — Phase 3**. Wait for `complete` or inspect a reported `partial` result. View topic summaries, decisions, actions, source-evidence buttons and graph relations. Some recordings will have no decisions/actions; empty registers are valid.
6. Download minutes, JSON, graph and validation artifacts. Confirm evidence matches the accepted transcript, owners/deadlines are not invented, and changed decisions retain their statuses.
7. Change an accepted edit or speaker name. Old minutes should be invalidated; regenerate them for the revised transcript.
8. Upload files from `samples/bad/`. Empty, invalid, silent and too-short inputs should produce a clear error without crashing the server.

Live tests consume API quota. A passing offline suite is not a measurement of transcription accuracy, topic quality, or real-meeting hallucination rates.

## 6. Run all phases from the terminal

Git Bash:

```bash
bash run.sh --cli samples/jfk.flac --provider groq
# Replace YOUR_JOB_ID with the job_id printed above.
bash run.sh --phase2 YOUR_JOB_ID
bash run.sh --phase3 YOUR_JOB_ID
```

PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/run_phase1.py samples/jfk.flac --provider groq
.\.venv\Scripts\python.exe scripts/run_phase2.py YOUR_JOB_ID
.\.venv\Scripts\python.exe scripts/run_phase3.py YOUR_JOB_ID
```

Phase 1 saves `outputs/<job_id>/phase1_result.json`. Phase 2 saves `refinement.json`, `refined_transcript.txt`, edit logs and speaker information beside it. Phase 3 saves `outputs/<job_id>/phase3/runs/<run_id>/`; `phase3/latest.json` identifies the published run. Phase 3 CLI exit codes: 0 complete, 2 partial, 1 pipeline error. Rerun a partial job to reuse successful extraction checkpoints.

The server is not required for CLI processing. With it running, saved job status is available at `/jobs/<job_id>`, and summaries/downloads are available through the API. The browser does not yet provide a saved-job picker.

## 7. API smoke test

With the server running, use Git Bash (`curl.exe` instead of `curl` in PowerShell):

```bash
curl http://localhost:8000/health
curl -F 'file=@samples/jfk.flac' -F 'provider=groq' http://localhost:8000/process-audio
# Substitute the returned job_id and wait for each phase to finish.
curl http://localhost:8000/jobs/YOUR_JOB_ID
curl -X POST http://localhost:8000/jobs/YOUR_JOB_ID/refine
curl http://localhost:8000/jobs/YOUR_JOB_ID
curl -X POST http://localhost:8000/jobs/YOUR_JOB_ID/summarize
curl http://localhost:8000/jobs/YOUR_JOB_ID/summary
curl http://localhost:8000/jobs/YOUR_JOB_ID/graph
curl -o minutes.md http://localhost:8000/jobs/YOUR_JOB_ID/phase3/download/minutes
```

Do not start the next phase while the previous one is still running. Phase 3 summary status is `complete`, `partial`, `failed` or `stale` when no longer processing.

## 8. Evaluation and troubleshooting

- **Port busy:** use `PORT=8001 bash run.sh` in Git Bash, or `--port 8001` in the PowerShell uvicorn command.
- **Missing/invalid key:** edit local `.env`, rerun the check and restart the server. Do not edit Python files.
- **Model unavailable:** choose a supported model in `.env`; the check includes candidate, refinement and Phase 3 models.
- **Missing spaCy model:** rerun dependency installation with the same Python environment you use to launch the app.
- **No Phase 3 controls:** pull `akansha`, restart the server, refresh the browser, then finish Phase 2.
- **Rate limits/partial minutes:** wait for quota recovery, then regenerate; completed extraction checkpoints are retained.
- **No speaker names:** use Deepgram for acoustic labels; names only appear when supported or manually supplied.
- **App restart:** active work is interrupted. Saved jobs and completed outputs survive; rerun interrupted work. Use one server process.
- **Unicode problems on Windows:** use the updated code, which reads/writes transcript artifacts as UTF-8.

Optional live refinement evaluation (uses API quota):

```bash
python scripts/eval_refinement.py --refine-model openai/gpt-oss-20b
```

Use your project's Python environment (`.venv/Scripts/python.exe` on Windows, or the environment selected by `run.sh`). For a Phase 3 labelled evaluation:

```bash
python scripts/eval_phase3.py outputs/JOB_ID/phase3/runs/RUN_ID/phase3_result.json --labels YOUR_LABELS.json
```

The `samples/phase3/expected.json` file labels only the synthetic test fixture; do not use it to score an unrelated recording. Label topic units and required events for the actual meeting. This evaluation does not measure all possible hallucinations.
