# Phase 3 implementation map

The expanded design in `PHASE_3_PLAN.md` is implemented here as a conservative MVP. The code uses the actual Phase 2 schemas in this repository.

| Module | Responsibility |
| --- | --- |
| `app/schemas/phase3.py` | Typed units, evidence, events, relations, graph, manifest and output |
| `app/summarization/units.py` | Accepted-source revision, exact spans and timing |
| `embeddings.py` | Existing spaCy vectors; explicitly selectable TF-IDF baseline |
| `topics.py` | Persistent registry, novelty gates, soft assignments and returns |
| `importance.py` | Duration shares and event-aware narrative budgets |
| `events.py`, `validation.py` | Target/context extraction, field gates, semantic checks, checkpoints |
| `relations.py` | Explicit revisions, conflicts, adoption/rejection, answers and dependencies |
| `entities.py`, `graph.py` | Accepted speaker identity and typed graph construction |
| `evidence.py`, `summaries.py` | Evidence packets, verified topic claims and complete registers |
| `pipeline.py`, `export.py` | Run identity, orchestration, publication and Markdown/JSON |
| `api.py`, `web/phase3.js` | Background job, polling, evidence inspection and downloads |
| `scripts/run_phase3.py` | Saved-job CLI |
| `scripts/eval_phase3.py` | Metrics against explicit labels |
| `tests/phase3/` | Synthetic contract and integration regressions |

## Topic model

The MVP uses normalized mean content-word vectors from the already installed `en_core_web_md` model. Cosine similarity to a centroid or exemplar supplies the score. Credible candidates pass an absolute threshold; stable softmax with a configurable temperature gives membership weights. New coherent units seed topics. A conservative centroid pass merges near-duplicates and retains aliases. Reassignment considers the complete registry.

Duration is allocated as `T_k = sum_i duration_i * membership_ik`. Unknown durations remain unknown; shares use the known-duration denominator. Decisions, commitments, open questions and risks contribute separately to narrative priority. No probability calibration or Bayesian training is claimed.

## Deliberate MVP reductions

- Mean word vectors replace a new embedding API dependency. A future sentence-embedding adapter can replace this backend after evaluation.
- Entity-overlap features, learned uncertainty mass, topic splitting, general alias resolution, and cross-batch pronoun resolution remain future work.
- Final executive synthesis selects verified canonical event statements with status labels. This avoids another unconstrained generation step.
- Character-bounded packets approximate token budgets. Provider-specific token counters and usage/cost accounting can be added to the shared LLM client.
- JSON and a relation table support graph inspection. A richer visual graph and graph QA can build on the exported contract.
- Relation candidate generation is bounded and can be quadratic; larger-scale retrieval is not yet implemented.

## Next evaluation step

Run actual meetings through Phases 1–3, annotate topic memberships and events, and split by whole meeting. Use `scripts/eval_phase3.py` for topic-pair and required-event comparisons; separately label unsupported assertions, wrong owner/deadline associations, modality errors and omissions. Compare direct summarization, topics alone, graph without duration weighting, and the full pipeline before claiming an improvement.

Example evaluation command:

```bash
python scripts/eval_phase3.py outputs/JOB/phase3/runs/RUN/phase3_result.json --labels labels.json --output evaluation.json
```

`samples/phase3/expected.json` demonstrates the label format for the synthetic fixture in the tests. Do not apply those unit IDs to unrelated meetings.
