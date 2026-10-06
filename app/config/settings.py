"""All configuration comes from environment variables / .env. Nothing is hardcoded in code paths."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # ASR
    asr_provider: str = "groq"  # groq | deepgram
    asr_model: str = ""  # empty -> provider default
    groq_api_key: str = Field("", repr=False, exclude=True)
    deepgram_api_key: str = Field("", repr=False, exclude=True)
    groq_default_model: str = "whisper-large-v3"
    groq_fallback_model: str = "whisper-large-v3-turbo"
    deepgram_default_model: str = "nova-3"
    chunk_seconds: int = 600
    max_chunk_bytes: int = 24 * 1024 * 1024  # Groq free tier limit is 25 MB

    # Audio validation
    min_duration_seconds: float = 1.0
    max_duration_seconds: float = 4 * 3600
    max_upload_mb: int = 500
    min_rms: float = 1e-4  # below this the audio is treated as silent

    # Normalization / extraction thresholds
    no_speech_drop_prob: float = 0.8
    low_logprob: float = -1.0
    asr_suspect_logprob: float = -0.8
    asr_suspect_word_conf: float = 0.6
    rare_zipf: float = 3.0
    spacy_model: str = "en_core_web_md"

    # Candidate detection (LLM API). "off" disables it; there is no rule-based fallback.
    candidate_detector: str = "llm"  # llm | off
    llm_model: str = "openai/gpt-oss-120b"
    llm_batch_segments: int = 40
    llm_context_segments: int = 2

    # Phase 2: refinement (small refiner, larger verifier)
    llm_base_url: str = "https://api.groq.com/openai/v1"  # any OpenAI-compatible endpoint
    llm_api_key: str = Field("", repr=False, exclude=True)  # optional separate chat-provider key
    refine_model: str = "openai/gpt-oss-20b"
    verify_model: str = "openai/gpt-oss-120b"
    refine_reasoning_effort: str = "medium"  # low | medium | high (gpt-oss). Measured on the eval fixture: low 9/13, medium 11/13, high 11/11 completed but slow + one empty reply
    verify_enabled: bool = True
    refine_batch_segments: int = 25
    refine_context_segments: int = 2
    refine_use_second_asr: bool = True
    refine_max_edits_per_segment: int = 6
    refine_max_changed_fraction: float = 0.50
    refine_min_changed_chars: int = 30  # floor so short phrase fixes in short segments are not blocked
    refine_max_words: int = 8
    refine_min_similarity: float = 0.55

    # Phase 3: existing Groq chat models and spaCy vectors; no new key required.
    phase3_model: str = ""  # empty -> llm_model
    phase3_verify_model: str = ""  # empty -> verify_model (verification is mandatory)
    phase3_embedding_backend: Literal["spacy", "tfidf"] = "spacy"
    phase3_topic_threshold: float = Field(0.62, ge=0, le=1)
    phase3_merge_threshold: float = Field(0.92, ge=0, le=1)
    phase3_temperature: float = Field(0.12, gt=0, le=2)
    phase3_batch_chars: int = Field(14000, ge=6000, le=80000)
    phase3_unit_chars: int = Field(1200, ge=200, le=2000)
    phase3_summary_words: int = Field(900, ge=100, le=5000)
    phase3_max_relation_pairs: int = Field(5000, ge=1)

    # Storage
    outputs_dir: str = str(ROOT / "outputs")
    cache_enabled: bool = True


settings = Settings()
