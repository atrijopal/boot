"""CPU embedding adapters. No model download or lexical fallback is implicit."""
import numpy as np

from app.preprocessing.nlp import get_nlp
from app.utils.errors import PipelineError


def embed_units(units, backend="spacy") -> np.ndarray:
    texts = [u.text for u in units]
    if backend == "tfidf":
        from sklearn.feature_extraction.text import TfidfVectorizer
        try:
            vectors = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=4096).fit_transform(texts).toarray()
        except ValueError:
            vectors = np.zeros((len(texts), 1))
    elif backend == "spacy":
        nlp = get_nlp()
        if not nlp.vocab.vectors_length:
            raise PipelineError("PHASE3_VECTORS_MISSING", "Phase 3 needs a spaCy model with word vectors (en_core_web_md).")
        vectors = []
        # Existing medium model: mean content-word vectors, not a sentence transformer.
        for doc in nlp.pipe(texts, batch_size=64, disable=nlp.pipe_names):
            rows = [t.vector for t in doc if t.has_vector and not t.is_stop and not t.is_punct and not t.like_num]
            vectors.append(np.mean(rows, axis=0) if rows else np.zeros(nlp.vocab.vectors_length))
        vectors = np.asarray(vectors)
    else:
        raise PipelineError("PHASE3_CONFIG", "Unknown embedding backend.")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)

