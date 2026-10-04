# Meeting AI — Proposed Approaches and Experiment Space

> **Status: Working design space.** This document is intentionally **not a fixed architecture**. Models, algorithms, providers, prompts, UI features, and individual stages can be swapped after benchmarking.

## 1. Core objective

Build a generic meeting assistant that satisfies the official PS while making the pipeline reliable, auditable, and useful across different kinds of English-language meetings.

The mandatory backbone is:

```text
Audio → Speech-to-Text → Raw Transcript
     → Domain-Aware Refinement → Refined Transcript
     → Meeting Documentation → Summary + Minutes + Decisions + Tasks
     → Human-readable + Machine-readable output
```

Everything below is an experiment/design option around that backbone.

## 2. Phase 1 — Audio → reliable raw transcript

### Candidate ASR systems

- Whisper / faster-whisper
- WhisperX
- NVIDIA Parakeet
- Parakeet TDT variants
- Other open ASR models available locally or through APIs

### Potential preprocessing

- Audio format validation
- Resampling to a standard format
- Channel normalization
- Voice activity detection
- Silence trimming
- Optional loudness normalization
- Chunking for long recordings
- Timestamp preservation
- Speaker diarization where useful

### Desired raw transcript representation

Keep more than plain text internally:

```json
{
  "segment_id": "seg_001",
  "speaker": "SPEAKER_00",
  "start": 12.40,
  "end": 17.82,
  "text": "..."
}
```

This preserves provenance for later stages.

## 3. Raw transcript preprocessing

Before sending the transcript to an LLM, construct a clean but conservative intermediate representation.

Possible steps:

1. Segment normalization
2. Speaker-label cleanup
3. Timestamp normalization
4. Sentence/utterance segmentation
5. Duplicate/overlap detection
6. Number extraction
7. Date/time extraction
8. Named-entity extraction
9. Organization/entity extraction
10. Technical-term discovery
11. Negation detection
12. Commitment detection
13. Question detection
14. Candidate decision detection
15. Confidence metadata
16. Evidence/provenance mapping

### Principle

Do **not** ask an LLM to be the sole source of truth for invariants.

For example:

```text
RAW:
"We don't need 81.73 percent accuracy."

PROTECTED:
number = 81.73
negation = don't
meaning = rejection/absence of requirement
```

The LLM may interpret the sentence, but deterministic checks should verify that critical facts survive refinement.

## 4. Dynamic meeting vocabulary

The system should not depend on a hardcoded vocabulary for one domain.

Instead:

```text
Current meeting
      ↓
Discover recurring / unusual terms
      ↓
Technical terms + names + acronyms + organizations
      ↓
Context supplied to refinement stage
```

Possible methods:

- NER
- GLiNER
- KeyBERT
- Sentence-transformer similarity
- frequency/statistical extraction
- LLM-assisted candidate verification

The vocabulary can be regenerated for every meeting.

## 5. Protected-facts layer

A candidate intermediate object:

```json
{
  "numbers": [],
  "dates": [],
  "times": [],
  "people": [],
  "organizations": [],
  "technical_terms": [],
  "negations": [],
  "commitments": [],
  "candidate_decisions": [],
  "candidate_tasks": []
}
```

This is deliberately a **design option**, not a final schema.

### Split of responsibilities

**Prefer deterministic/NLP extraction for:**

- numbers
- dates
- times
- timestamps
- exact strings
- obvious names/entities

**Use semantic models for:**

- commitments
- proposals
- decisions
- task intent
- ambiguous entities
- semantic relationships

## 6. Transcript refinement

### Goal

Correct plausible ASR errors in:

- technical terms
- acronyms
- names
- domain vocabulary
- punctuation/segmentation

while preserving meaning.

### Candidate model families

- Qwen
- GPT-OSS
- Llama
- Gemma
- DeepSeek
- Mistral
- other open/open-weight models

Models can be accessed through local inference, Groq, NVIDIA API/NIM, OpenRouter, or other providers.

### Refinement principle

Do not perform unconstrained rewriting.

```text
Raw transcript
      ↓
Context + protected facts
      ↓
Refinement LLM
      ↓
Candidate refined transcript
      ↓
Semantic/invariant validation
      ↓
Accept / reject / repair
```

## 7. Refinement validation

### Exact invariant checks

- numeric values preserved
- dates preserved
- times preserved
- named entities preserved where confidence is high
- explicit negation not reversed
- speaker attribution not silently changed

### Semantic checks

- commitment preserved
- proposal does not become decision
- uncertainty preserved
- modality preserved
- task intent preserved

### Failure policy

```text
Candidate refinement
       ↓
Validation
       ↓
FAIL
       ↓
fallback:
  - retain raw segment
  - retry with stricter prompt
  - use alternate model
  - flag for review
```

The exact fallback can be decided after experiments.

## 8. Meeting understanding

### Topic segmentation

Possible approaches:

- embedding similarity
- change-point detection
- HDBSCAN clustering
- TreeSeg or similar segmentation methods
- LLM-assisted segmentation

Potential output:

```text
00:00–04:12  Introductions
04:12–11:30  Project architecture
11:30–18:42  Model selection
18:42–24:10  Deployment
```

### Question tracker

Detect questions and attempt to connect them to later answers.

```text
Question → Candidate answer span → Resolved / partially resolved / unresolved
```

### Open-topic tracker

Identify issues discussed but not resolved.

### Conversation state

Represent the meeting as evolving state:

```text
Topic
 ├── questions
 ├── proposals
 ├── decisions
 ├── commitments
 ├── tasks
 └── unresolved issues
```

## 9. Decision integrity

Explicitly distinguish:

```text
Information
Question
Suggestion
Proposal
Discussion
Agreement
Decision
Rejection
Commitment
Task
```

Example failure to avoid:

```text
"Maybe we should use Qwen."
        ↓ WRONG
Decision: Use Qwen.
```

The system should require stronger evidence before classifying something as an agreed decision.

## 10. Evidence / provenance

A useful optional feature is to connect every important extracted item to its source:

```json
{
  "decision": "Use model X",
  "evidence": {
    "segment_id": "seg_031",
    "speaker": "SPEAKER_02",
    "start": 842.2,
    "end": 850.7
  }
}
```

This enables transcript highlighting, click-to-jump, audio playback, reviewer verification, and debugging.

## 11. Safety / robustness

### Prompt-injection firewall

Treat transcript content as **data**, not instructions. Spoken text such as “ignore previous instructions” should not become an instruction to the system.

### PII detection

Potentially detect phone numbers, email addresses, addresses, and sensitive identifiers. Possible tool: Microsoft Presidio.

Whether to redact, mask, or merely flag should remain configurable.

### Abstention

If evidence is insufficient:

```text
owner = unspecified
deadline = unspecified
decision = uncertain
```

Do not force an answer.

## 12. Meeting Map / interactive interface

Optional UI enhancement:

```text
Meeting timeline
─────────────────────────────────────
Intro | Architecture | Models | Tasks
       ↑
    selected topic
```

Clicking a topic could show relevant transcript, summary, decisions, tasks, open questions, and audio timestamp.

## 13. Model strategy

Do not assume one model must do everything.

Potential routing:

```text
                 ┌─ Small model → classification/extraction
Transcript ──────┼─ Medium model → refinement
                 └─ Strong model → final minutes/reasoning
```

Or use one strong model for all semantic stages. Or combine a local small model, free API model, and fallback provider.

Benchmark before locking this down.

## 14. Free API / local strategy

Potential providers:

- Groq
- NVIDIA API/NIM
- OpenRouter
- Google AI Studio
- Hugging Face inference
- local Ollama

Potential architecture:

```text
                ┌── Provider A
                ├── Provider B
Request ────────┼── Provider C
                └── Local fallback
```

A provider abstraction keeps the architecture independent of any one API.

## 15. Caching

Repeated experiments should not repeatedly consume API quota.

Possible key:

```text
hash(transcript + prompt_version + model + generation_parameters)
```

Then:

```text
cache hit  → reuse result
cache miss → call model
```

## 16. Experiment plan

### ASR

- Whisper
- WhisperX
- Parakeet

### LLM

- Qwen
- GPT-OSS
- Llama
- Gemma
- DeepSeek
- Mistral

### Extraction

- rules
- NLP models
- LLM
- hybrid

### Segmentation

- embeddings
- clustering
- change-point detection
- LLM

### Verification

- rules
- second model
- hybrid

No model or technique is final until it is tested on representative meeting audio.

## 17. Recommended initial architecture

A reasonable **starting point**, not a permanent commitment:

```text
                    AUDIO
                      │
                      ▼
             Audio preprocessing
                      │
                      ▼
              WhisperX / Parakeet
                      │
                      ▼
              RAW TRANSCRIPT
          + speaker + timestamps
                      │
                      ▼
           Transcript preprocessing
          ┌───────────┴────────────┐
          │                        │
    deterministic             semantic
      extraction              extraction
          │                        │
          └───────────┬────────────┘
                      ▼
               Meeting context
                      │
                      ▼
             Refinement LLM
                      │
                      ▼
             REFINED TRANSCRIPT
                      │
                      ▼
          Refinement validation
                      │
                      ▼
           Meeting-documentation
                  LLM stage
                      │
                      ▼
        Summary / Minutes / Decisions
                    / Tasks
                      │
                      ▼
              Evidence validation
                      │
              ┌───────┴────────┐
              ▼                ▼
          Human format     JSON output
```

## 18. Things we should NOT lock yet

Keep these configurable:

- ASR model
- diarization approach
- LLM provider
- LLM model
- local vs API inference
- prompt wording
- protected-fact schema
- topic segmentation algorithm
- number of validation stages
- UI framework
- database/storage
- caching strategy
- fallback strategy
- optional safety layers

The implementation should use interfaces/configuration so these can be changed without rewriting the pipeline.
