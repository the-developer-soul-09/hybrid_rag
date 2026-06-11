#!/usr/bin/env python3
"""
ChatDoc CLI — Chat with a digital PDF directly via the Groq LLM.

Usage:
  python main.py <path/to/document.pdf>
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

console = Console()

MAX_DOC_CHARS = 100_000   # ~25k tokens hard limit
MAX_HISTORY   = 20        # keep last 10 exchanges (20 messages)


def main() -> None:
    load_dotenv()

    if len(sys.argv) < 2:
        console.print("[bold]Usage:[/bold] python main.py <path/to/document.pdf>")
        sys.exit(0)

    pdf_path = sys.argv[1]
    real_path = os.path.realpath(pdf_path)

    if not os.path.isfile(real_path) or not real_path.lower().endswith(".pdf"):
        console.print(f"[red]Error:[/red] Not a valid PDF file: {pdf_path}")
        sys.exit(1)

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        console.print("[red]Error:[/red] GROQ_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    # -- Extract PDF text --------------------------------------------------
    console.print(f"\n[bold]Loading:[/bold] {os.path.basename(real_path)}")
    with console.status("Extracting text..."):
        from pdf_processor.digital_extractor import extract_pdf_text
        doc_text, truncated = extract_pdf_text(real_path, max_chars=MAX_DOC_CHARS)

    status = f"[cyan]{len(doc_text):,}[/cyan] characters"
    if truncated:
        status += " [yellow](truncated to fit context)[/yellow]"
    console.print(f"  Extracted {status}")

    # -- Groq client -------------------------------------------------------
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    system_prompt = (
        f"You are a helpful assistant that answers questions about the document below.\n"
        f"Answer strictly based on the document. "
        f"Cite page numbers (e.g. [Page 3]) when referencing content.\n\n"
        f"--- DOCUMENT: {os.path.basename(real_path)} ---\n"
        f"{doc_text}\n"
        f"--- END OF DOCUMENT ---"
    )

    console.print(
        Panel(
            "Type your questions. [cyan]quit[/cyan] to exit.",
            title=f"💬 Chatting with: {os.path.basename(real_path)}",
            border_style="blue",
        )
    )

    history: list[dict] = []

    while True:
        try:
            question = console.input("\n[bold cyan]You:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})

        try:
            console.print("\n[bold green]Assistant:[/bold green] ", end="")
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=4096,
                temperature=0.2,
                stream=True,
            )
            full_response = ""
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    console.print(delta.content, end="")
                    full_response += delta.content
            console.print()

            # Update history, cap at MAX_HISTORY messages
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": full_response})
            if len(history) > MAX_HISTORY:
                history = history[-MAX_HISTORY:]

        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


if __name__ == "__main__":
    main()
