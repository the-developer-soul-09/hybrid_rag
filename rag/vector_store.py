"""
Qdrant vector store management.

Handles collection creation, document upsertion, and collection lifecycle
for the hybrid RAG pipeline using both dense and sparse vectors.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient, models
from tqdm import tqdm

from config import (
    DENSE_VECTOR_SIZE,
    QDRANT_COLLECTION,
    QDRANT_PATH,
)
from rag.chunker import Chunk
logger = logging.getLogger(__name__)

_GLOBAL_QDRANT_CLIENT: QdrantClient | None = None

def get_qdrant_client(path: str = QDRANT_PATH) -> QdrantClient:
    """Get or create the global QdrantClient instance (singleton pattern)."""
    global _GLOBAL_QDRANT_CLIENT
    if _GLOBAL_QDRANT_CLIENT is None:
        logger.info("Initializing global QdrantClient instance (path: %s)", path)
        _GLOBAL_QDRANT_CLIENT = QdrantClient(path=path)
    return _GLOBAL_QDRANT_CLIENT


class QdrantStore:
    """
    Manages a Qdrant collection with named dense and sparse vector spaces.

    Uses local disk-based storage for persistence without Docker.
    """

    def __init__(
        self,
        path: str = QDRANT_PATH,
        collection_name: str = QDRANT_COLLECTION,
    ):
        self.collection_name = collection_name
        self.client = get_qdrant_client(path)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create the collection if it doesn't exist."""
        collections = [
            c.name for c in self.client.get_collections().collections
        ]

        if self.collection_name in collections:
            info = self.client.get_collection(self.collection_name)
            logger.info(
                "Collection '%s' exists with %d points",
                self.collection_name,
                info.points_count,
            )
            return

        logger.info("Creating collection '%s'", self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=DENSE_VECTOR_SIZE,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False),
                ),
            },
        )
        logger.info("Collection '%s' created", self.collection_name)

    def add_documents(
        self,
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[dict[str, Any]],
        batch_size: int = 100,
    ) -> int:
        """
        Upsert chunks with their dense and sparse embeddings.

        Parameters
        ----------
        chunks : list[Chunk]
            The text chunks with metadata.
        dense_vectors : list[list[float]]
            Dense embedding vectors (same order as chunks).
        sparse_vectors : list[dict]
            Sparse vectors with "indices" and "values" keys.
        batch_size : int
            Number of points per upsert batch.

        Returns
        -------
        int
            Total number of points upserted.
        """
        if not chunks:
            return 0

        assert len(chunks) == len(dense_vectors) == len(sparse_vectors), (
            f"Mismatch: {len(chunks)} chunks, "
            f"{len(dense_vectors)} dense, {len(sparse_vectors)} sparse"
        )

        # Get current max ID to avoid collisions
        existing_count = self.get_point_count()

        points = []
        for i, (chunk, dense_vec, sparse_vec) in enumerate(
            zip(chunks, dense_vectors, sparse_vectors)
        ):
            point_id = existing_count + i
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector={
                        "dense": dense_vec,
                        "sparse": models.SparseVector(
                            indices=sparse_vec["indices"],
                            values=sparse_vec["values"],
                        ),
                    },
                    payload=chunk.to_payload(),
                )
            )

        # Batch upsert
        total = 0
        for batch_start in tqdm(
            range(0, len(points), batch_size),
            desc="Upserting to Qdrant",
            unit="batch",
        ):
            batch = points[batch_start : batch_start + batch_size]
            self.client.upsert(
                collection_name=self.collection_name,
                points=batch,
            )
            total += len(batch)

        logger.info("Upserted %d points to '%s'", total, self.collection_name)
        return total

    def get_point_count(self) -> int:
        """Get the current number of points in the collection."""
        try:
            info = self.client.get_collection(self.collection_name)
            return info.points_count or 0
        except Exception:
            return 0

    def get_collection_info(self) -> dict:
        """Return collection metadata."""
        info = self.client.get_collection(self.collection_name)
        return {
            "name": self.collection_name,
            "points_count": info.points_count,
            "status": str(info.status),
        }

    def delete_collection(self) -> None:
        """Delete the entire collection."""
        self.client.delete_collection(self.collection_name)
        logger.info("Deleted collection '%s'", self.collection_name)

    def reset_collection(self) -> None:
        """Delete and recreate the collection (fresh start)."""
        try:
            self.delete_collection()
        except Exception:
            pass
        self._ensure_collection()
