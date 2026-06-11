"""
Intelligent text chunking for RAG.

Implements recursive character-based chunking that is table-aware:
  - Keeps markdown tables as atomic units (never splits mid-table)
  - Preserves metadata: page number, chunk type, source position
  - Configurable chunk size and overlap
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from config import CHUNK_OVERLAP, CHUNK_SIZE, MIN_CHUNK_SIZE

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A single text chunk with metadata for the vector store."""

    text: str
    chunk_id: int
    page_num: int
    chunk_type: str = "text"  # "text" or "table"
    source_doc: str = ""
    metadata: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        """Convert to a Qdrant-compatible payload dict."""
        return {
            "text": self.text,
            "chunk_id": self.chunk_id,
            "page_num": self.page_num,
            "chunk_type": self.chunk_type,
            "source_doc": self.source_doc,
            **self.metadata,
        }


class RecursiveChunker:
    """
    Table-aware recursive character splitter.

    First separates content into table blocks and text blocks.
    Tables are kept whole (as single chunks). Text is recursively
    split using a hierarchy of separators.
    """

    SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

    def __init__(
        self,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
        min_chunk_size: int = MIN_CHUNK_SIZE,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def chunk_pages(
        self,
        pages: list[dict],
        source_doc: str = "",
    ) -> list[Chunk]:
        """
        Chunk a list of page content dicts into Chunk objects.

        Parameters
        ----------
        pages : list[dict]
            Each dict has keys: "text", "tables" (list of md strings),
            "page_num", "page_type".
        source_doc : str
            Name/path of the source PDF.

        Returns
        -------
        list[Chunk]
            All chunks across all pages.
        """
        all_chunks: list[Chunk] = []
        chunk_id = 0

        for page in pages:
            page_num = page["page_num"]
            page_type = page.get("page_type", "unknown")

            # ---- Table chunks (atomic — never split) -----------------------
            for table_md in page.get("tables", []):
                if len(table_md.strip()) < self.min_chunk_size:
                    continue
                all_chunks.append(
                    Chunk(
                        text=table_md.strip(),
                        chunk_id=chunk_id,
                        page_num=page_num,
                        chunk_type="table",
                        source_doc=source_doc,
                        metadata={"page_type": page_type},
                    )
                )
                chunk_id += 1

            # ---- Text chunks (recursive split) -----------------------------
            text = page.get("text", "").strip()
            if not text:
                continue

            # Remove any embedded table-like content from text
            # (tables already captured above)
            text = self._remove_table_blocks(text)

            text_splits = self._recursive_split(text)

            for split_text in text_splits:
                if len(split_text.strip()) < self.min_chunk_size:
                    continue
                all_chunks.append(
                    Chunk(
                        text=split_text.strip(),
                        chunk_id=chunk_id,
                        page_num=page_num,
                        chunk_type="text",
                        source_doc=source_doc,
                        metadata={"page_type": page_type},
                    )
                )
                chunk_id += 1

        logger.info(
            "Chunked %d pages into %d chunks (doc: %s)",
            len(pages),
            len(all_chunks),
            source_doc,
        )
        return all_chunks

    def _recursive_split(self, text: str) -> list[str]:
        """Recursively split text using separator hierarchy."""
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        return self._split_with_separators(text, self.SEPARATORS)

    def _split_with_separators(
        self, text: str, separators: list[str]
    ) -> list[str]:
        """Split text using the first applicable separator."""
        if not separators:
            # Base case: force-split at chunk_size boundaries
            return self._force_split(text)

        sep = separators[0]
        remaining_seps = separators[1:]

        if sep == "":
            return self._force_split(text)

        parts = text.split(sep)

        # Merge small parts back together with overlap
        chunks: list[str] = []
        current = ""

        for part in parts:
            candidate = (current + sep + part) if current else part

            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current:
                    if len(current) > self.chunk_size:
                        # Current is too big — recurse with finer separator
                        chunks.extend(
                            self._split_with_separators(
                                current, remaining_seps
                            )
                        )
                    else:
                        chunks.append(current)
                current = part

        if current:
            if len(current) > self.chunk_size:
                chunks.extend(
                    self._split_with_separators(current, remaining_seps)
                )
            else:
                chunks.append(current)

        # Apply overlap between consecutive chunks
        if self.chunk_overlap > 0 and len(chunks) > 1:
            chunks = self._apply_overlap(chunks)

        return chunks

    def _force_split(self, text: str) -> list[str]:
        """Force-split text at exact character boundaries."""
        chunks = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            chunks.append(text[start:end])
            start = end - self.chunk_overlap
        return chunks

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Add overlap from the end of each chunk to the start of the next."""
        if len(chunks) <= 1:
            return chunks

        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            overlap_text = prev[-self.chunk_overlap :] if len(prev) > self.chunk_overlap else ""
            result.append(overlap_text + chunks[i])
        return result

    @staticmethod
    def _remove_table_blocks(text: str) -> str:
        """Remove markdown table blocks from text (they're chunked separately)."""
        # Match markdown tables: lines starting with |
        lines = text.split("\n")
        non_table_lines = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                in_table = True
                continue
            if in_table and (not stripped or re.match(r"^[-|:\s]+$", stripped)):
                continue
            in_table = False
            non_table_lines.append(line)

        return "\n".join(non_table_lines)
