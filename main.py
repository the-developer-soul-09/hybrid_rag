#!/usr/bin/env python3
"""
PDF RAG Pipeline — CLI Entry Point.

Commands:
  ingest <pdf_path>   Process a PDF and store in the vector database
  query  <question>   Ask a question about ingested documents
  interactive         Start an interactive Q&A session
  info                Show collection statistics
  reset               Clear all ingested data

Usage:
  python main.py ingest /path/to/document.pdf
  python main.py query "What is the revenue for Q3?"
  python main.py interactive
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# PDF Ingestion
# ---------------------------------------------------------------------------

def ingest_pdf(pdf_path: str, reset: bool = False) -> None:
    """
    Process a PDF file and store chunks in the Qdrant vector database.

    Steps:
      1. Classify each page as digital or scanned
      2. Extract text and tables (using appropriate method per page type)
      3. Chunk content into RAG-ready pieces
      4. Generate dense + sparse embeddings
      5. Upsert into Qdrant
    """
    # Validate the file path
    real_path = os.path.realpath(pdf_path)
    if not os.path.isfile(real_path):
        console.print(f"[red]Error:[/red] File not found: {pdf_path}")
        sys.exit(1)

    if not real_path.lower().endswith(".pdf"):
        console.print("[red]Error:[/red] File must be a .pdf")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]Ingesting:[/bold] {os.path.basename(real_path)}",
            title="📄 PDF RAG Pipeline",
            border_style="blue",
        )
    )

    # Lazy imports (so --help is fast)
    import pdfplumber
    from pdf2image import convert_from_path

    from config import DPI
    from pdf_processor.classifier import PageContent, classify_page
    from pdf_processor.digital_extractor import extract_digital_page
    from pdf_processor.ocr_extractor import extract_scanned_page
    from pdf_processor.table_extractor import extract_tables_from_image
    from rag.chunker import RecursiveChunker
    from rag.embeddings import DenseEmbedder, SparseEmbedder
    from rag.vector_store import QdrantStore

    # ---- Step 0: Reset if requested ---------------------------------------
    store = QdrantStore()
    if reset:
        console.print("[yellow]Resetting collection...[/yellow]")
        store.reset_collection()

    # ---- Step 1–2: Process pages ------------------------------------------
    pages_data: list[dict] = []

    with pdfplumber.open(real_path) as pdf:
        total_pages = len(pdf.pages)
        console.print(f"  Total pages: [cyan]{total_pages}[/cyan]")

        # Pre-convert all pages to images (for scanned page processing)
        console.print("  Converting pages to images...")
        try:
            page_images = convert_from_path(
                real_path, dpi=DPI, fmt="png"
            )
        except Exception as e:
            console.print(
                f"[yellow]Warning:[/yellow] pdf2image conversion failed: {e}\n"
                "  Falling back to digital-only extraction."
            )
            page_images = [None] * total_pages

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processing pages", total=total_pages)

            for i, page in enumerate(pdf.pages):
                page_num = page.page_number  # 1-based

                # Classify
                page_type = classify_page(page)
                progress.update(
                    task,
                    description=f"Page {page_num} [{page_type}]",
                )

                if page_type == "digital":
                    img = page_images[i] if (page_images and i < len(page_images)) else None
                    content: PageContent = extract_digital_page(page, img)
                else:
                    # Scanned: use OCR for text
                    img = page_images[i] if (page_images and i < len(page_images)) else None
                    if img is not None:
                        # Use clean tesseract functions directly to segment and avoid duplication
                        from pdf_processor.table_extractor import extract_text_with_tesseract, extract_tables_with_tesseract
                        
                        try:
                            from pdf_processor.ocr_extractor import preprocess_image
                            processed_img = preprocess_image(img)
                        except Exception:
                            processed_img = img

                        try:
                            scanned_tables = extract_tables_with_tesseract(processed_img)
                        except Exception:
                            scanned_tables = []

                        try:
                            scanned_text = extract_text_with_tesseract(processed_img)
                        except Exception:
                            try:
                                fallback_scanned = extract_scanned_page(img, page_num)
                                scanned_text = fallback_scanned.text
                            except Exception:
                                scanned_text = ""

                        content = PageContent(
                            page_num=page_num,
                            page_type="scanned",
                            text=scanned_text,
                            tables=scanned_tables,
                        )
                    else:
                        # Fallback: try digital extraction
                        content = extract_digital_page(page)

                pages_data.append(
                    {
                        "page_num": content.page_num,
                        "page_type": content.page_type,
                        "text": content.text,
                        "tables": content.tables,
                    }
                )

                progress.advance(task)

    # ---- Summary of extraction ---------------------------------------------
    digital_count = sum(1 for p in pages_data if p["page_type"] == "digital")
    scanned_count = sum(1 for p in pages_data if p["page_type"] == "scanned")
    total_tables = sum(len(p["tables"]) for p in pages_data)

    summary = Table(title="Extraction Summary", show_header=False)
    summary.add_row("Digital pages", str(digital_count))
    summary.add_row("Scanned pages", str(scanned_count))
    summary.add_row("Tables found", str(total_tables))
    console.print(summary)

    # ---- Save processed text to disk ---------------------------------------
    processed_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed")
    os.makedirs(processed_dir, exist_ok=True)

    pdf_basename = os.path.splitext(os.path.basename(real_path))[0]
    output_path = os.path.join(processed_dir, f"{pdf_basename}.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Processed: {os.path.basename(real_path)}\n\n")
        f.write(f"- Digital pages: {digital_count}\n")
        f.write(f"- Scanned pages: {scanned_count}\n")
        f.write(f"- Tables found: {total_tables}\n\n---\n\n")

        for page in pages_data:
            f.write(f"## Page {page['page_num']} ({page['page_type']})\n\n")
            if page["text"].strip():
                f.write(page["text"].strip())
                f.write("\n\n")
            for t_idx, table_md in enumerate(page["tables"], 1):
                f.write(f"### Table {t_idx}\n\n")
                f.write(table_md)
                f.write("\n\n")
            f.write("---\n\n")

    console.print(f"  Processed text saved to: [cyan]{output_path}[/cyan]")

    # ---- Step 3: Chunk content --------------------------------------------
    console.print("\n[bold]Chunking content...[/bold]")
    chunker = RecursiveChunker()
    chunks = chunker.chunk_pages(
        pages_data, source_doc=os.path.basename(real_path)
    )
    console.print(f"  Generated [cyan]{len(chunks)}[/cyan] chunks")

    if not chunks:
        console.print("[yellow]Warning:[/yellow] No content extracted from PDF")
        return

    # ---- Step 4: Generate embeddings --------------------------------------
    console.print("\n[bold]Generating embeddings...[/bold]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # Dense embeddings
        task_d = progress.add_task("Dense embeddings (MiniLM)...", total=1)
        dense_embedder = DenseEmbedder()
        texts = [c.text for c in chunks]
        dense_vectors = dense_embedder.embed(texts)
        progress.advance(task_d)

        # Sparse embeddings
        task_s = progress.add_task("Sparse embeddings (BM25)...", total=1)
        sparse_embedder = SparseEmbedder()
        sparse_vectors = sparse_embedder.embed(texts)
        progress.advance(task_s)

    # ---- Step 5: Store in Qdrant ------------------------------------------
    console.print("\n[bold]Storing in Qdrant...[/bold]")
    num_stored = store.add_documents(chunks, dense_vectors, sparse_vectors)

    # ---- Done! ------------------------------------------------------------
    console.print(
        Panel(
            f"[green]✓ Successfully ingested {os.path.basename(real_path)}[/green]\n"
            f"  Chunks stored: {num_stored}\n"
            f"  Collection: {store.collection_name}",
            title="✅ Ingestion Complete",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_document(question: str, stream: bool = True) -> None:
    """
    Query the ingested documents using hybrid RAG.
    """
    from rag.embeddings import DenseEmbedder, SparseEmbedder
    from rag.generator import GroqGenerator
    from rag.retriever import HybridRetriever

    console.print(
        Panel(
            f"[bold]Query:[/bold] {question}",
            title="🔍 Hybrid RAG Search",
            border_style="blue",
        )
    )

    # Initialize components
    with console.status("Loading models..."):
        dense_embedder = DenseEmbedder()
        sparse_embedder = SparseEmbedder()
        retriever = HybridRetriever(dense_embedder, sparse_embedder)
        generator = GroqGenerator()

    # Retrieve
    with console.status("Searching documents..."):
        start_time = time.time()
        chunks = retriever.retrieve(question)
        retrieval_time = time.time() - start_time

    if not chunks:
        console.print(
            "[yellow]No relevant content found.[/yellow] "
            "Make sure you've ingested a PDF first."
        )
        return

    # Show retrieved sources
    sources_table = Table(title=f"Retrieved Sources ({retrieval_time:.2f}s)")
    sources_table.add_column("#", style="dim")
    sources_table.add_column("Page", style="cyan")
    sources_table.add_column("Type", style="magenta")
    sources_table.add_column("Score", style="green")
    sources_table.add_column("Preview", max_width=60)

    for i, chunk in enumerate(chunks, 1):
        preview = chunk.text[:80].replace("\n", " ") + "..."
        sources_table.add_row(
            str(i),
            str(chunk.page_num),
            chunk.chunk_type,
            f"{chunk.score:.4f}",
            preview,
        )

    console.print(sources_table)
    console.print()

    # Generate response
    if stream:
        console.print("[bold]Answer:[/bold]")
        full_response = ""
        start_time = time.time()

        for token in generator.generate_stream(question, chunks):
            console.print(token, end="")
            full_response += token

        gen_time = time.time() - start_time
        console.print(f"\n\n[dim]Generation time: {gen_time:.2f}s[/dim]")
    else:
        with console.status("Generating answer..."):
            start_time = time.time()
            response = generator.generate(question, chunks)
            gen_time = time.time() - start_time

        console.print(
            Panel(
                Markdown(response),
                title="💡 Answer",
                border_style="green",
            )
        )
        console.print(f"[dim]Generation time: {gen_time:.2f}s[/dim]")


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive_mode() -> None:
    """Start an interactive Q&A REPL."""
    from rag.embeddings import DenseEmbedder, SparseEmbedder
    from rag.generator import GroqGenerator
    from rag.retriever import HybridRetriever

    console.print(
        Panel(
            "[bold]Interactive Mode[/bold]\n"
            "Ask questions about your ingested documents.\n"
            "Type [cyan]quit[/cyan] or [cyan]exit[/cyan] to leave.\n"
            "Type [cyan]info[/cyan] to see collection stats.",
            title="💬 PDF RAG Chat",
            border_style="blue",
        )
    )

    with console.status("Loading models..."):
        dense_embedder = DenseEmbedder()
        sparse_embedder = SparseEmbedder()
        retriever = HybridRetriever(dense_embedder, sparse_embedder)
        generator = GroqGenerator()

    console.print("[green]Models loaded. Ready for questions![/green]\n")

    while True:
        try:
            question = console.input("[bold cyan]You:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not question:
            continue

        if question.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        if question.lower() == "info":
            show_info()
            continue

        # Retrieve and generate
        try:
            chunks = retriever.retrieve(question)

            if not chunks:
                console.print(
                    "[yellow]No relevant content found for this query.[/yellow]\n"
                )
                continue

            console.print(f"[dim]Found {len(chunks)} relevant chunks[/dim]")
            console.print("[bold green]Assistant:[/bold green] ", end="")

            for token in generator.generate_stream(question, chunks):
                console.print(token, end="")

            console.print("\n")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")


# ---------------------------------------------------------------------------
# Info / Reset
# ---------------------------------------------------------------------------

def show_info() -> None:
    """Display collection statistics."""
    from rag.vector_store import QdrantStore

    store = QdrantStore()
    info = store.get_collection_info()

    table = Table(title="Qdrant Collection Info")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    for key, value in info.items():
        table.add_row(key, str(value))

    console.print(table)


def reset_collection() -> None:
    """Clear all ingested data."""
    from rag.vector_store import QdrantStore

    store = QdrantStore()
    store.reset_collection()
    console.print("[green]✓ Collection reset successfully[/green]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PDF RAG Pipeline — Process PDFs and query them with hybrid RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest
    ingest_parser = subparsers.add_parser(
        "ingest", help="Process a PDF and store in the vector database"
    )
    ingest_parser.add_argument("pdf_path", help="Path to the PDF file")
    ingest_parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset the collection before ingesting",
    )

    # query
    query_parser = subparsers.add_parser(
        "query", help="Ask a question about ingested documents"
    )
    query_parser.add_argument("question", help="Your question")
    query_parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output",
    )

    # interactive
    subparsers.add_parser(
        "interactive", help="Start an interactive Q&A session"
    )

    # info
    subparsers.add_parser(
        "info", help="Show collection statistics"
    )

    # reset
    subparsers.add_parser(
        "reset", help="Clear all ingested data"
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "ingest":
        ingest_pdf(args.pdf_path, reset=args.reset)
    elif args.command == "query":
        query_document(args.question, stream=not args.no_stream)
    elif args.command == "interactive":
        interactive_mode()
    elif args.command == "info":
        show_info()
    elif args.command == "reset":
        reset_collection()


if __name__ == "__main__":
    main()
