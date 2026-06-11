"""
Embedding generators for the hybrid RAG pipeline.

Provides:
  - DenseEmbedder: sentence-transformers model for semantic embeddings
  - SparseEmbedder: FastEmbed BM25 model for lexical sparse vectors
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from fastembed import SparseTextEmbedding
from sentence_transformers import SentenceTransformer

from config import DENSE_MODEL_NAME, DENSE_VECTOR_SIZE, SPARSE_MODEL_NAME

logger = logging.getLogger(__name__)


class DenseEmbedder:
    """
    Dense embedding generator using sentence-transformers.

    Wraps the model with batch encoding and caching logic.
    """

    def __init__(self, model_name: str = DENSE_MODEL_NAME):
        logger.info("Loading dense embedding model: %s", model_name)
        self.model = SentenceTransformer(model_name)
        self.dimension = DENSE_VECTOR_SIZE

        # Verify dimension matches
        test_emb = self.model.encode(["test"])
        actual_dim = test_emb.shape[1]
        if actual_dim != self.dimension:
            logger.warning(
                "Model dimension %d differs from config %d — updating",
                actual_dim,
                self.dimension,
            )
            self.dimension = actual_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate dense embeddings for a list of texts.

        Parameters
        ----------
        texts : list[str]
            Input texts to embed.

        Returns
        -------
        list[list[float]]
            Dense vectors as lists of floats.
        """
        if not texts:
            return []

        embeddings = self.model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        return self.embed([query])[0]


class SparseEmbedder:
    """
    Sparse embedding generator using FastEmbed BM25.

    Produces sparse vectors (indices + values) compatible with Qdrant's
    sparse vector format.
    """

    def __init__(self, model_name: str = SPARSE_MODEL_NAME):
        logger.info("Loading sparse embedding model: %s", model_name)
        self.model = SparseTextEmbedding(model_name=model_name)

    def embed(
        self, texts: list[str]
    ) -> list[dict[str, Any]]:
        """
        Generate sparse embeddings for a list of texts.

        Parameters
        ----------
        texts : list[str]
            Input texts to embed.

        Returns
        -------
        list[dict]
            Each dict has "indices" (list[int]) and "values" (list[float]).
        """
        if not texts:
            return []

        results = []
        for sparse_vec in self.model.embed(texts):
            results.append(
                {
                    "indices": sparse_vec.indices.tolist(),
                    "values": sparse_vec.values.tolist(),
                }
            )
        return results

    def embed_query(self, query: str) -> dict[str, Any]:
        """Embed a single query string into a sparse vector."""
        return self.embed([query])[0]
