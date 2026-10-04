# Claude Code Prompt — Phase 1: Audio → Raw Transcript + Preprocessing

You are working on the **Inter IIT Bootcamp #Gold — Guwahati Phase 2 ML Problem Statement**.

## Your task

Implement **Phase 1 only**.

The goal of Phase 1 is:

> Take an uploaded English-language meeting recording, convert it into a high-quality raw transcript, and perform all useful preprocessing/analysis needed BEFORE any transcript-refinement LLM is called.

Do **NOT** implement the final meeting-minutes generation stage yet.

Do **NOT** lock the project into one model/provider if it can reasonably be made configurable.

The architecture must remain modular so that we can later swap WhisperX ↔ Parakeet, local inference ↔ API, diarization on/off, and different preprocessing strategies without rewriting the whole application.

---

# 1. Read the project context first

Before coding:

1. Read the official problem statement in `01_problem_statement.md`.
2. Read `02_approaches_and_experiments.md`.
3. Inspect the existing repository structure.
4. Identify what is already implemented.
5. Do not unnecessarily rewrite existing working components.
6. Create/update a Phase-1 implementation plan before making major changes.

The official PS requires:

- English meeting audio input.
- Raw transcript generation.
- Visible transcript.
- Clear handling of unsupported, empty, or unreadable files.
- A later separate LLM stage for transcript refinement.
- A later separate LLM stage for meeting documentation.
- Preservation of raw and refined transcripts.

Phase 1 should establish the foundation for these requirements.

---

# 2. Phase-1 boundary

Implement this pipeline:

```text
UPLOAD AUDIO
     ↓
FILE VALIDATION
     ↓
AUDIO PREPROCESSING
     ↓
VAD / AUDIO CLEANUP where useful
     ↓
ASR
     ↓
TIMESTAMP / ALIGNMENT
     ↓
SPEAKER DIARIZATION where practical
     ↓
RAW TRANSCRIPT
     ↓
TRANSCRIPT NORMALIZATION
     ↓
STRUCTURAL / SEMANTIC PREPROCESSING
     ↓
PROTECTED FACTS / MEETING CONTEXT
     ↓
PHASE-1 OUTPUT
```

The output of Phase 1 will later be consumed by the transcript-refinement LLM.

Do not call the refinement LLM in this phase.

---

# 3. ASR architecture

Start with a clean abstraction such as:

```python
class ASRProvider:
    def transcribe(self, audio_path) -> Transcript:
        ...
```

Make the implementation configurable.

Initially benchmark/implement the strongest practical open option available in the environment, such as:

- WhisperX / faster-whisper
- NVIDIA Parakeet

Do not blindly assume one is superior.

The configuration should make it possible to change the model later.

For example:

```yaml
asr:
  provider: whisperx
  model: large-v3
  device: cuda
  compute_type: float16
```

The exact configuration format is flexible.

---

# 4. Audio validation

Before inference, validate:

- file exists
- file is non-empty
- supported audio format
- file can actually be decoded
- duration is valid
- audio contains usable signal

Handle failures gracefully.

Examples:

```text
Unsupported file format
Corrupted audio
Zero-byte file
Unreadable audio
No detectable speech
```

Return a clear error to the UI/API instead of crashing.

Do not hide exceptions.

---

# 5. Audio preprocessing

Implement only preprocessing that is justified.

Potential operations:

- convert to mono when appropriate
- resample to ASR-compatible sampling rate
- normalize audio representation
- VAD
- silence handling
- chunking for long recordings

Avoid destructive preprocessing.

The original uploaded file must never be modified.

Store preprocessing metadata.

For example:

```json
{
  "original_file": "...",
  "duration_seconds": 1234.5,
  "sample_rate": 16000,
  "channels": 1,
  "processed_sample_rate": 16000
}
```

---

# 6. Raw transcript representation

Do NOT store only one giant string.

Use timestamped segments.

Minimum useful representation:

```json
{
  "segment_id": "seg_001",
  "speaker": "SPEAKER_00",
  "start": 12.42,
  "end": 18.91,
  "text": "..."
}
```

Also retain:

- word timestamps if available
- ASR confidence if available
- language
- segment-level metadata

Keep the original ASR output available for debugging.

---

# 7. Speaker diarization

If the chosen ASR stack supports diarization cleanly, integrate it.

Possible option:

- pyannote

But make diarization configurable.

If diarization fails, the pipeline should still be able to produce a transcript rather than completely failing unless diarization is explicitly required.

Clearly distinguish:

```text
speaker_known
speaker_unknown
```

Do not invent speaker names.

Use:

```text
SPEAKER_00
SPEAKER_01
```

unless actual identities are provided externally.

---

# 8. Transcript normalization

After ASR, create a normalized transcript while retaining the exact/raw ASR result.

Possible operations:

- remove accidental duplicated fragments where confidence is high
- normalize whitespace
- merge/split segments appropriately
- preserve timestamps
- preserve speaker boundaries
- preserve numbers exactly
- preserve negation
- preserve uncertainty
- preserve filler words in the raw transcript

IMPORTANT:

Do not make aggressive grammatical corrections here.

Phase 1 is not transcript refinement.

The raw transcript must remain faithful to what ASR produced.

---

# 9. Pre-LLM information extraction

Build a preprocessing layer that extracts useful information BEFORE the refinement LLM.

The purpose is to provide context and create invariants that can later be validated.

Possible fields:

```json
{
  "numbers": [],
  "dates": [],
  "times": [],
  "people": [],
  "organizations": [],
  "technical_terms": [],
  "acronyms": [],
  "negations": [],
  "candidate_questions": [],
  "candidate_commitments": [],
  "candidate_decisions": []
}
```

Do not assume every field must be populated.

Do not invent information.

---

# 10. Deterministic extraction

Use deterministic methods wherever they are reliable.

Examples:

### Numbers

Extract:

- integers
- decimals
- percentages
- measurements
- version numbers
- ranges
- monetary values

Example:

```text
81.73
2x
$200
v2.1
10–15
```

These should be preserved exactly or normalized in a way that retains the original value.

### Dates

Detect:

```text
Friday
7 October
October 7
next Monday
2026-10-07
```

Do not resolve relative dates unless the reference date is explicitly available.

### Times

Detect:

```text
3 PM
15:30
around noon
```

### Names / organizations

Use NER or other extraction tools where useful.

Keep the original surface form.

---

# 11. Semantic preprocessing

Some things cannot be safely extracted using regex alone.

Build candidate detectors for:

- commitments
- questions
- proposals
- possible decisions
- possible tasks
- uncertainty

These can initially be heuristic/NLP based.

If you use an LLM during preprocessing, isolate it behind an interface and make it optional.

Do not make the architecture dependent on an LLM being available at this phase.

---

# 12. Technical-term discovery

The meeting can belong to any domain.

Do NOT create a hardcoded vocabulary such as:

```text
machine learning terms only
```

Instead dynamically discover terms from the current meeting.

Candidate methods:

- frequency analysis
- TF-IDF
- KeyBERT
- embeddings
- GLiNER
- NER
- acronym detection
- optional LLM verification

Example:

```text
Meeting mentions:
"NAFNet"
"PSNR"
"LPIPS"
"super-resolution"
```

These become candidate context for the refinement stage.

But do not automatically assume every unusual token is a technical term.

Store confidence/source.

---

# 13. Negation detection

This is important.

The later refinement stage must not accidentally reverse:

```text
"We don't need X."
```

into:

```text
"We need X."
```

Detect negative constructions using an NLP parser or suitable rule system.

Store:

- segment ID
- text span
- negation cue
- affected phrase where possible

Example:

```json
{
  "segment_id": "seg_14",
  "cue": "don't",
  "scope": "need 81.73 percent accuracy"
}
```

This is a preprocessing signal, not a final semantic truth.

---

# 14. Protected facts

Create a structured intermediate object.

Suggested initial design:

```json
{
  "numbers": [],
  "dates": [],
  "times": [],
  "people": [],
  "organizations": [],
  "technical_terms": [],
  "acronyms": [],
  "negations": [],
  "commitments": [],
  "candidate_decisions": []
}
```

Keep this schema easy to modify.

Do not over-engineer it.

The important property is that the later refinement stage can receive:

```text
RAW TRANSCRIPT
+
MEETING CONTEXT
+
PROTECTED FACTS
```

and produce a refined transcript that can be validated against them.

---

# 15. Provenance

Every extracted item should, where practical, point back to the source segment.

For example:

```json
{
  "value": "81.73",
  "type": "number",
  "segment_id": "seg_14",
  "start": 421.2,
  "end": 426.8
}
```

This will later enable:

- evidence-backed decisions
- transcript highlighting
- audio jumping
- debugging
- validation

Do not lose provenance during preprocessing.

---

# 16. Confidence

Where tools expose confidence, retain it.

Do not manufacture confidence values.

For extracted information, distinguish:

```text
high confidence
medium confidence
low confidence
unknown
```

if the extraction method supports this.

---

# 17. Data model

Create clear schemas/classes for:

```text
AudioMetadata
Transcript
TranscriptSegment
Word
Entity
ProtectedFact
MeetingContext
ProcessingResult
```

Use typed models where appropriate, e.g. Pydantic.

Keep schemas modular.

---

# 18. API / backend

Expose a clean Phase-1 endpoint or service.

For example:

```text
POST /process-audio
```

or whatever API structure already exists.

The response should expose:

```json
{
  "status": "success",
  "audio_metadata": {},
  "raw_transcript": {},
  "meeting_context": {},
  "protected_facts": {}
}
```

Do not expose internal stack traces to the user.

Log them internally.

---

# 19. UI

If the repository already has a UI, extend it.

Otherwise create a minimal interface that can:

1. upload audio
2. start processing
3. show processing status
4. display raw transcript
5. show timestamps
6. show speakers if available
7. show extracted preprocessing information
8. show errors clearly

Do NOT spend most of Phase 1 on visual design.

Correctness comes first.

---

# 20. Testing

This is critical.

Create tests for:

### File handling

- valid audio
- empty file
- corrupted file
- unsupported format

### Transcript handling

- timestamps
- speaker labels
- segment ordering
- empty transcript
- long transcript

### Invariants

Test examples containing:

```text
81.73%
2x
Friday
Rahul
I don't agree
We should NOT deploy this
I'll prepare it
```

Verify that preprocessing captures the relevant facts.

### Edge cases

Test:

- overlapping speakers
- pauses
- background noise
- technical vocabulary
- acronyms
- numbers
- dates
- negation
- unfinished sentences

---

# 21. Benchmarking

Build a simple benchmark script.

Measure:

- total processing time
- ASR latency
- audio duration
- real-time factor
- VRAM usage where available
- segment count
- word count
- extraction latency

If multiple ASR models are available, allow:

```bash
python benchmark_asr.py --model whisper
python benchmark_asr.py --model parakeet
```

Do not declare a winner without measurements.

---

# 22. Logging

Log:

```text
file validation
audio preprocessing
ASR start/end
diarization start/end
normalization
extraction
total latency
errors
```

Do not log sensitive audio/transcript content unnecessarily.

---

# 23. Caching

Implement optional caching so repeated development runs do not recompute ASR unnecessarily.

A reasonable cache key can include:

```text
audio_hash
+
ASR model
+
ASR configuration
```

If the same audio/model/configuration is processed again, reuse the result.

Make caching configurable.

---

# 24. Repository organization

Prefer something like:

```text
project/
├── app/
│   ├── api/
│   ├── asr/
│   ├── audio/
│   ├── preprocessing/
│   ├── schemas/
│   ├── utils/
│   └── config/
├── tests/
├── scripts/
├── configs/
├── outputs/
├── docs/
├── requirements.txt
├── .env.example
└── README.md
```

Adapt this to the existing repository instead of blindly creating a new structure.

---

# 25. Environment / configuration

Do not hardcode:

- API keys
- model paths
- GPU device
- provider URLs
- credentials
- file paths

Use environment variables/config files.

Provide `.env.example`.

Example:

```text
ASR_PROVIDER=
ASR_MODEL=
DEVICE=cuda
HF_TOKEN=
```

Only include variables actually required.

---

# 26. Important architectural constraint

The later LLM stages must remain separate.

Phase 1 should therefore produce a clean contract:

```text
Phase 1 output
       ↓
┌─────────────────────────────┐
│ raw transcript              │
│ timestamps                  │
│ speaker labels              │
│ meeting context             │
│ protected facts             │
│ provenance                  │
│ confidence                  │
└─────────────────────────────┘
       ↓
Future Phase 2:
Transcript Refinement LLM
```

Do not combine Phase 1 and Phase 2 into one opaque LLM call.

---

# 27. What NOT to do

Do NOT:

- hardcode a meeting transcript
- hardcode expected outputs
- use an LLM to blindly rewrite the ASR output
- discard the raw transcript
- discard timestamps
- invent speaker names
- invent owners/deadlines
- force technical terms into a fixed vocabulary
- assume one model is permanently selected
- hardcode one API provider
- overbuild the UI
- add unnecessary agent frameworks
- add a database unless there is a concrete need
- hide processing errors
- silently swallow ASR failures

---

# 28. Definition of done

Phase 1 is complete only when:

- [ ] English audio can be uploaded.
- [ ] Invalid/empty/unreadable files fail cleanly.
- [ ] Audio is validated/preprocessed.
- [ ] ASR produces a raw transcript.
- [ ] Raw transcript remains available exactly as an intermediate artifact.
- [ ] Timestamped segments are retained.
- [ ] Speaker information is retained when available.
- [ ] Transcript preprocessing runs before any future refinement LLM.
- [ ] Numbers/dates/times/entities/technical terms are extracted where possible.
- [ ] Negation candidates are detected.
- [ ] Candidate semantic items can be represented without pretending they are confirmed decisions/tasks.
- [ ] Protected facts have provenance.
- [ ] Processing metadata and errors are logged.
- [ ] Tests exist for core preprocessing behavior.
- [ ] Configuration allows the ASR model to be swapped.
- [ ] No hardcoded output is used.
- [ ] The output contract is documented.
- [ ] README explains setup and Phase-1 execution.
- [ ] At least one real meeting recording is successfully processed end-to-end through Phase 1.

---

# 29. Final instruction

Do the implementation, not just the explanation.

First inspect the repository and existing code.

Then implement Phase 1 incrementally.

After implementation:

1. Run the tests.
2. Run the pipeline on a real/sample audio file if available.
3. Fix errors.
4. Report exactly what works.
5. Report what could not be tested.
6. Show the final Phase-1 architecture.
7. List the files changed.
8. Do not claim a benchmark or accuracy result unless it was actually measured.

Keep the architecture **floating and replaceable**. We are deliberately treating model selection, provider choice, extraction strategy, and optional components as experiment variables until benchmark results justify locking them.
