import os
from functools import lru_cache

import numpy as np

# Local ONNX model: $0, no quota, works offline after a one-time ~70 MB download.
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    # Keep the model next to the DB; the default temp-dir cache gets wiped and forces a re-download.
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=os.getenv("MODEL_CACHE", "data/models"))


def embed(texts: list[str]) -> np.ndarray:
    """Return unit-length float32 vectors, one row per text."""
    vectors = np.array(list(_model().embed(texts)), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
