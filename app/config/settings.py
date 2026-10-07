"""All configuration comes from environment variables / .env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # speech-to-text (API; costs no language-model tokens)
    asr_provider: str = "auto"  # auto | deepgram | groq. auto = deepgram (it recognises voices) when its key is set, otherwise groq (text only)
    asr_model: str = ""
    groq_api_key: str = ""
    deepgram_api_key: str = ""
    nvidia_api_key: str = ""  # optional: models written as "nvidia:<model id>" (NVIDIA NIM, https://build.nvidia.com)
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    openrouter_api_key: str = ""  # optional: models written as "openrouter:<model id>" (https://openrouter.ai)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    groq_default_model: str = "whisper-large-v3"
    groq_fallback_model: str = "whisper-large-v3-turbo"
    deepgram_default_model: str = "nova-3"
    chunk_seconds: int = 600
    max_chunk_bytes: int = 24 * 1024 * 1024

    # audio validation
    min_duration_seconds: float = 1.0
    max_duration_seconds: float = 4 * 3600
    max_upload_mb: int = 500
    min_rms: float = 1e-4

    # transcript cleanup
    no_speech_drop_prob: float = 0.8
    low_logprob: float = -1.0
    spacy_model: str = "en_core_web_sm"  # only used to check that edits never change negations

    # language model (any OpenAI-compatible endpoint): three model calls, each with its own model setting
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"  # default used by the shared client
    speakers_enabled: bool = False  # OFF by default: guessing speakers from the text alone scored 28 to 40% on a real meeting (Deepgram, by voice: 86 to 96%). Opt-in only.
    speakers_model: str = "openai/gpt-oss-120b"
    speakers_effort: str = "medium"
    names_enabled: bool = True  # phase 2: find who the speakers are (names and roles said in the meeting)
    names_effort: str = "low"
    refine_enabled: bool = True
    verify_edits_enabled: bool = True  # an independent second check confirms a suggested fix that has no evidence in the recording itself
    verify_edits_model: str = "qwen/qwen3.8-27b"  # a different model family from the one that proposed the fix
    verify_edits_effort: str = "low"
    refine_model: str = "openai/gpt-oss-20b"  # step 2: only proposes small, exact fixes
    refine_effort: str = "medium"
    # One model per reader. Small models for easy readers, the big one for the hard ones; every model has its own rate limit and daily token cap, so spreading calls also spreads the load.
    fallback_model: str = ""  # optional, e.g. "nvidia:nvidia/nemotron-3-super-120b-a12b": answers a call whose own model is rate limited or down
    extract_model_decisions: str = "qwen/qwen3.8-27b"
    extract_model_actions: str = "openai/gpt-oss-120b"
    extract_model_issues: str = "qwen/qwen3.8-27b"
    extract_model_details: str = "qwen/qwen3.8-27b"
    extract_model_discussion: str = "qwen/qwen3.8-27b"
    names_model: str = "openai/gpt-oss-20b"
    extract_model: str = "openai/gpt-oss-120b"  # step 3: measured elsewhere: the 20b model returns nothing on this long structured prompt
    extract_effort: str = "medium"
    review_model: str = "openai/gpt-oss-120b"  # step 4: reviews records, writes the summary and topic bullets
    review_effort: str = "low"
    review_scope: str = "decisions,actions"  # which kinds get a faithfulness verdict: "decisions,actions" | "all" | "none"
    window_chars: int = 14000  # transcript characters per refine/extract call (about 3,500 tokens); longer meetings use several windows
    window_overlap_units: int = 4
    extraction_lenses: int = 5  # readers per window: 5 (decisions / tasks / issues / figures+topics / discussion points), 2, or 1 combined. The richest set that fits phase3_max_calls is used
    phase3_max_calls: int = 6  # hard limit on model calls that got an answer in phase 3 (extraction calls + the review call). Retries of refused calls do not count
    max_run_tokens: int = 80000
    max_call_tokens: int = 7000  # largest single model call; Groq's free tier allows 8,000 tokens per minute per model
    run_timeout_seconds: int = 600
    llm_read_timeout_seconds: int = 60  # one model answer; a slower provider is treated as unavailable so the backup (or a retry) can take over
    llm_slow_read_timeout_seconds: int = 150  # NVIDIA / OpenRouter reasoning models answer in 40 to 60 s; a 60 s limit made the backup model useless
    llm_retries: int = 2

    # edit gate (Phase-2 style rules, applied in code)
    refine_max_changed_fraction: float = 0.50
    refine_min_changed_chars: int = 30
    refine_max_edits_per_segment: int = 6
    refine_max_words: int = 8
    refine_min_similarity: float = 0.55

    outputs_dir: str = str(ROOT / "outputs")
    cache_enabled: bool = False  # off: every run transcribes and calls the models fresh; set CACHE_ENABLED=true to reuse saved answers


settings = Settings()
