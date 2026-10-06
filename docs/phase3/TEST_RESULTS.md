# Phase 3 test results

Validation on 2026-10-06 after integrating Phase 3 and fixing the `akansha` branch based on `e3d490d2e7bde3a3820a0a11486c37a44db57910`.

- Full Python suite: **161 passed**, including 28 Phase 3 tests and configuration/client regressions. Verified via `bash run.sh --test` in a freshly installed Python 3.12 environment.
- Phase 3 LLM extraction/verification/reconciliation tests use deterministic mocks; no live Groq calls were made.
- The spaCy vector backend also ran on the synthetic budget-return fixture and correctly kept the returning budget topic distinct from testing.
- JavaScript syntax: `node --check web/phase3.js` passed.
- API routes, downloads and state transitions were tested through FastAPI TestClient. Browser interaction and live provider calls were not performed in this review.
- One existing dependency deprecation warning was emitted by FastAPI/Starlette TestClient; there were no test failures.

Commands:

```bash
bash run.sh --test
python -m compileall -q app scripts
bash -n run.sh
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

