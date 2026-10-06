# Phase 3 test results

Validation completed against the local snapshot of `atrijopal/boot` at `ea58fa4b77f4cd6eb39b23966e421f529dcd5a59`.

- Full Python suite: **146 passed** (118 existing tests + 28 Phase 3 tests).
- Phase 3 LLM extraction/verification/reconciliation tests use deterministic mocks; no live Groq calls were made.
- The spaCy vector backend also ran on the synthetic budget-return fixture and correctly kept the returning budget topic distinct from testing.
- Synthetic labelled evaluation: 36 topic pairs; precision 1.0, recall 1.0; all 4 required fixture event checks matched. These numbers describe this scripted fixture only, not real-meeting quality.
- JavaScript syntax: `node --check web/phase3.js` passed.
- Browser interaction testing was unavailable: the Chromium download failed in the execution environment. API routes, downloads, and state transitions were tested through FastAPI TestClient.
- One existing dependency deprecation warning was emitted by FastAPI/Starlette TestClient; there were no test failures.

Commands:

```bash
python -m pytest -q
node --check web/phase3.js
```

## Tested dependency versions

| Package | Version |
| --- | --- |
| pydantic | 2.13.5 |
| pydantic-settings | 2.15.0 |
| fastapi | 0.142.2 |
| httpx | 0.28.1 |
| numpy | 2.3.5 |
| scikit-learn | 1.8.0 |
| spacy | 3.8.16 |
| en_core_web_md | 3.8.0 |
| pytest | 9.1.1 |

These versions record the verification environment; the original repository requirements remain unchanged. Provider/model availability and live output quality still need validation with the configured account.
