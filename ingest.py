"""LegalMind Pro — command-line ingestor.

A thin front-end over ingest_engine.ingest_folder. It deliberately owns no
extraction, chunking or database logic of its own: this used to be a second,
divergent copy of the pipeline, which meant fixes to crash-safety and the
incremental diff had to be made twice and in practice were not.

Run:  python ingest.py
"""
import os
import sys
import time

import tkinter as tk
from tkinter import filedialog, simpledialog

from rich.console import Console
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn,
                           TaskProgressColumn, TextColumn)

import store
from ingest_engine import ingest_folder

console = Console()


def main() -> int:
    console.clear()
    console.print(Panel.fit(
        "[bold magenta]LegalMind Pro Ingestor[/bold magenta]\n"
        "[italic]LanceDB backend[/italic]",
        border_style="magenta"))

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    case_name = simpledialog.askstring("Case Selection", "Enter Case Name:")
    if not case_name:
        root.destroy()
        return 0
    target_dir = filedialog.askdirectory(title=f"Select folder for {case_name}")
    root.destroy()
    if not target_dir:
        return 0

    console.print(f"[cyan]Case:[/cyan]   {case_name}")
    console.print(f"[cyan]Folder:[/cyan] {target_dir}")

    start = time.time()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TaskProgressColumn(),
                  console=console) as progress:
        task = progress.add_task("[cyan]Starting...", total=None)

        def on_progress(p):
            if p.total_files:
                progress.update(task, total=p.total_files,
                                completed=p.file_index or 0)
            progress.update(task, description=f"[cyan]{p.message}")

        try:
            result = ingest_folder(target_dir, case_name,
                                   progress_cb=on_progress)
        except store.StoreError as e:
            # Fail loudly. Treating an unreadable index as "nothing indexed"
            # is what caused a full 2,525-file duplicate run on 2026-07-21.
            console.print(f"\n[bold red]Vector store error:[/bold red] {e}")
            console.print("[red]Aborted before touching any documents.[/red]")
            return 1
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Completed files are "
                          "recorded and will be skipped next run.[/yellow]")
            return 130

    elapsed = time.time() - start
    console.print(
        f"\n[green]Done[/green] — {result.files_processed} file(s), "
        f"{result.total_chunks:,} chunks in {elapsed:.1f}s")
    if result.files_skipped:
        console.print(f"[dim]{result.files_skipped} already indexed, "
                      f"skipped.[/dim]")
    if result.errors:
        console.print(f"[yellow]{len(result.errors)} file(s) had errors "
                      f"and will be retried next run:[/yellow]")
        for msg in result.errors[:10]:
            console.print(f"  [dim]- {msg}[/dim]")
        if len(result.errors) > 10:
            console.print(f"  [dim]... and {len(result.errors) - 10} more[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
