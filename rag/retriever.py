"""
Hybrid retriever using Qdrant's Query API.

Combines dense (semantic) and sparse (BM25) search with
Reciprocal Rank Fusion (RRF) for high-quality retrieval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from config import (
    PREFETCH_LIMIT,
    QDRANT_COLLECTION,
    QDRANT_PATH,
    RETRIEVAL_TOP_K,
)
from rag.embeddings import DenseEmbedder, SparseEmbedder

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """A chunk retrieved from the vector store with its relevance score."""

    text: str
    score: float
    page_num: int
    chunk_type: str
    source_doc: str
    metadata: dict


class HybridRetriever:
    """
    Performs hybrid search using dense + sparse retrieval with RRF fusion.

    Uses Qdrant's prefetch API to run both searches in parallel on the
    server side, then fuses results using Reciprocal Rank Fusion.
    """

    def __init__(
        self,
        dense_embedder: DenseEmbedder,
        sparse_embedder: SparseEmbedder,
        qdrant_path: str = QDRANT_PATH,
        collection_name: str = QDRANT_COLLECTION,
        top_k: int = RETRIEVAL_TOP_K,
        prefetch_limit: int = PREFETCH_LIMIT,
    ):
        self.dense_embedder = dense_embedder
        self.sparse_embedder = sparse_embedder
        from rag.vector_store import get_qdrant_client
        self.client = get_qdrant_client(qdrant_path)
        self.collection_name = collection_name
        self.top_k = top_k
        self.prefetch_limit = prefetch_limit

    def retrieve(self, query: str) -> list[RetrievedChunk]:
        """
        Perform hybrid retrieval for a query.

        Parameters
        ----------
        query : str
            The user's natural language query.

        Returns
        -------
        list[RetrievedChunk]
            Top-K most relevant chunks, sorted by fused relevance score.
        """
        # Generate query embeddings
        dense_query = self.dense_embedder.embed_query(query)
        sparse_query = self.sparse_embedder.embed_query(query)

        # Execute hybrid search with server-side RRF fusion
        try:
            results = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    # Dense (semantic) search leg
                    models.Prefetch(
                        query=dense_query,
                        using="dense",
                        limit=self.prefetch_limit,
                    ),
                    # Sparse (BM25) search leg
                    models.Prefetch(
                        query=models.SparseVector(
                            indices=sparse_query["indices"],
                            values=sparse_query["values"],
                        ),
                        using="sparse",
                        limit=self.prefetch_limit,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=self.top_k,
            )
        except Exception:
            logger.error("Hybrid search failed", exc_info=True)
            return []

        # Convert results to RetrievedChunk objects
        chunks = []
        for point in results.points:
            payload = point.payload or {}
            chunks.append(
                RetrievedChunk(
                    text=payload.get("text", ""),
                    score=point.score if point.score is not None else 0.0,
                    page_num=payload.get("page_num", 0),
                    chunk_type=payload.get("chunk_type", "text"),
                    source_doc=payload.get("source_doc", ""),
                    metadata={
                        k: v
                        for k, v in payload.items()
                        if k not in ("text", "page_num", "chunk_type", "source_doc")
                    },
                )
            )

        logger.info(
            "Retrieved %d chunks for query: '%s...'",
            len(chunks),
            query[:50],
        )
        return chunks

    def retrieve_dense_only(self, query: str) -> list[RetrievedChunk]:
        """Fallback: dense-only semantic search."""
        dense_query = self.dense_embedder.embed_query(query)

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=dense_query,
            using="dense",
            limit=self.top_k,
        )

        return [
            RetrievedChunk(
                text=(point.payload or {}).get("text", ""),
                score=point.score if point.score is not None else 0.0,
                page_num=(point.payload or {}).get("page_num", 0),
                chunk_type=(point.payload or {}).get("chunk_type", "text"),
                source_doc=(point.payload or {}).get("source_doc", ""),
                metadata={},
            )
            for point in results.points
        ]
