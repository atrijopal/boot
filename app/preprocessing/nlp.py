"""Lazy spaCy loader. One parse per segment is shared by all NLP-based extractors."""
from functools import lru_cache

from app.config.settings import settings
from app.utils.errors import PipelineError


@lru_cache(maxsize=1)
def get_nlp():
    import spacy

    try:
        return spacy.load(settings.spacy_model, disable=["lemmatizer"])
    except OSError:
        raise PipelineError(
            "NLP_MODEL_MISSING",
            f"spaCy model '{settings.spacy_model}' is not installed. Run: python -m spacy download {settings.spacy_model}",
        ) from None


def parse_segments(texts: list[str]):
    return list(get_nlp().pipe(texts, batch_size=64))
