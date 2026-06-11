"""
Groq LLM generator for RAG responses.

Uses the OpenAI-compatible Groq API to generate answers from
retrieved context, with source attribution and streaming support.
"""

from __future__ import annotations

import logging
from typing import Generator

from openai import OpenAI

from config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MAX_TOKENS,
    GROQ_MODEL,
    GROQ_TEMPERATURE,
)
from rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# System prompt for the RAG pipeline
RAG_SYSTEM_PROMPT = """You are a highly accurate document analysis assistant. Your task is to answer questions based ONLY on the provided context from a PDF document.

Rules:
1. Answer based STRICTLY on the provided context. If the context doesn't contain enough information, say so clearly.
2. When referencing information, cite the page number(s) in brackets like [Page X].
3. If the context contains tables, interpret and present them clearly.
4. Be precise and thorough in your answers.
5. If multiple pieces of context are relevant, synthesize them into a coherent answer.
6. Never fabricate or hallucinate information not present in the context."""


def _build_context_prompt(chunks: list[RetrievedChunk]) -> str:
    """
    Build the context section of the RAG prompt from retrieved chunks.

    Formats each chunk with its metadata for clear source attribution.
    """
    context_parts = []

    for i, chunk in enumerate(chunks, 1):
        chunk_label = (
            f"[Source {i} | Page {chunk.page_num} | "
            f"Type: {chunk.chunk_type} | "
            f"Score: {chunk.score:.3f}]"
        )
        context_parts.append(f"{chunk_label}\n{chunk.text}")

    return "\n\n---\n\n".join(context_parts)


class GroqGenerator:
    """
    LLM response generator using Groq's API.

    Supports both streaming and non-streaming generation modes.
    """

    def __init__(
        self,
        api_key: str = GROQ_API_KEY,
        base_url: str = GROQ_BASE_URL,
        model: str = GROQ_MODEL,
        max_tokens: int = GROQ_MAX_TOKENS,
        temperature: float = GROQ_TEMPERATURE,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        logger.info("Groq generator initialized (model: %s)", self.model)

    def rephrase_query(self, query: str, history: list[dict]) -> str:
        """
        Rephrase a conversational follow-up question into a standalone, search-friendly query.
        """
        if not history:
            return query

        # Build context from recent history (last 5 messages to avoid blowing context window)
        history_str = ""
        for msg in history[-5:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            history_str += f"{role}: {msg['content']}\n"

        system_prompt = (
            "You are a search query optimizer. Given the conversation history and a follow-up query, "
            "rephrase it to be a standalone search query that contains all necessary context "
            "(e.g. resolve pronouns like 'he', 'its', 'their', 'them', 'that'). "
            "Respond ONLY with the rephrased search query. Do not add any conversational text, explanations, or quotes."
        )

        user_prompt = (
            f"Conversation History:\n{history_str}\n"
            f"Follow-up Query: {query}\n\n"
            f"Standalone Query:"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=64,
                temperature=0.0,  # Deterministic for rephrasing
            )
            rephrased = response.choices[0].message.content or ""
            return rephrased.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning("Failed to rephrase query, using original: %s", e)
            return query

    def generate(
        self,
        query: str,
        context_chunks: list[RetrievedChunk],
        history: list[dict] = None,
    ) -> str:
        """
        Generate a complete response from context, query, and chat history.

        Parameters
        ----------
        query : str
            The user's question.
        context_chunks : list[RetrievedChunk]
            Retrieved context chunks with metadata.
        history : list[dict] | None
            Conversation history list of dicts with role and content keys.

        Returns
        -------
        str
            The LLM's response.
        """
        if not context_chunks:
            return (
                "I couldn't find any relevant information in the document "
                "to answer your question. Please try rephrasing or ensure "
                "the document has been properly ingested."
            )

        context = _build_context_prompt(context_chunks)

        user_message = (
            f"Context from the document:\n\n{context}\n\n"
            f"---\n\n"
            f"Question: {query}\n\n"
            f"Please provide a detailed and accurate answer based on the "
            f"context above."
        )

        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error("Groq API call failed: %s", e, exc_info=True)
            return f"Error generating response: {e}"

    def generate_stream(
        self,
        query: str,
        context_chunks: list[RetrievedChunk],
        history: list[dict] = None,
    ) -> Generator[str, None, None]:
        """
        Stream a response token-by-token for real-time display.

        Parameters
        ----------
        query : str
            The user's question.
        context_chunks : list[RetrievedChunk]
            Retrieved context chunks with metadata.
        history : list[dict] | None
            Conversation history list of dicts with role and content keys.

        Yields
        ------
        str
            Individual response tokens/chunks.
        """
        if not context_chunks:
            yield (
                "I couldn't find any relevant information in the document "
                "to answer your question."
            )
            return

        context = _build_context_prompt(context_chunks)

        user_message = (
            f"Context from the document:\n\n{context}\n\n"
            f"---\n\n"
            f"Question: {query}\n\n"
            f"Please provide a detailed and accurate answer based on the "
            f"context above."
        )

        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True,
            )

            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as e:
            logger.error("Groq streaming failed: %s", e, exc_info=True)
            yield f"\nError during generation: {e}"
