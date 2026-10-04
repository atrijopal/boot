from app.asr.base import ASRProvider
from app.asr.deepgram_provider import DeepgramProvider
from app.asr.groq_provider import GroqProvider
from app.config.settings import settings
from app.utils.errors import PipelineError

PROVIDERS = {"groq": GroqProvider, "deepgram": DeepgramProvider}


def get_provider(name: str | None = None) -> ASRProvider:
    name = (name or settings.asr_provider).lower()
    if name not in PROVIDERS:
        raise PipelineError("UNKNOWN_PROVIDER", f"Unknown ASR provider '{name}'. Available: {', '.join(PROVIDERS)}.")
    return PROVIDERS[name]()
