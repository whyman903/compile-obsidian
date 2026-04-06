from __future__ import annotations

import time
from pathlib import Path
from threading import Timer

from rich.console import Console
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
from watchdog.observers import Observer

from compile.compiler import Compiler
from compile.config import Config
from compile.ingest import ingest_sources, run_synthesis_pass
from compile.text import is_supported
from compile.workspace import get_unprocessed

console = Console()


class _IngestHandler(FileSystemEventHandler):
    """Debounced handler that queues files for ingestion."""

    def __init__(self, config: Config, compiler: Compiler) -> None:
        self.config = config
        self.compiler = compiler
        self.debounce_seconds = config.debounce_seconds
        self._pending: set[Path] = set()
        self._timer: Timer | None = None

    def on_created(self, event: FileCreatedEvent) -> None:
        self._handle(event.src_path)

    def on_modified(self, event: FileModifiedEvent) -> None:
        self._handle(event.src_path)

    def _handle(self, src_path: str) -> None:
        path = Path(src_path)
        if not path.is_file() or not is_supported(path):
            return
        self._pending.add(path)
        # Reset debounce timer
        if self._timer:
            self._timer.cancel()
        self._timer = Timer(self.debounce_seconds, self._flush)
        self._timer.start()

    def _flush(self) -> None:
        paths = list(self._pending)
        self._pending.clear()
        if not paths:
            return
        try:
            ingest_sources(self.config, self.compiler, paths)
            if len(paths) >= 2:
                console.print("[dim]Running synthesis pass after batch ingest...[/dim]")
                run_synthesis_pass(self.config, self.compiler)
        except Exception as e:
            console.print(f"  [red]Error processing batch of {len(paths)} files: {e}[/red]")


def watch_raw(config: Config, compiler: Compiler) -> None:
    """Watch the raw/ directory and auto-ingest new files."""
    raw_dir = config.raw_dir
    if not raw_dir.exists():
        console.print("[red]raw/ directory not found.[/red]")
        return

    # First, process any already-unprocessed files
    unprocessed = get_unprocessed(config)
    if unprocessed:
        console.print(f"[bold]Processing {len(unprocessed)} unprocessed files first...[/bold]")
        try:
            ingest_sources(config, compiler, unprocessed)
            if len(unprocessed) >= 2:
                console.print("[dim]Running synthesis pass after batch ingest...[/dim]")
                run_synthesis_pass(config, compiler)
        except Exception as e:
            console.print(f"  [red]Error processing batch: {e}[/red]")

    console.print(f"\n[bold]Watching {raw_dir} for new files...[/bold]")
    console.print("Press Ctrl+C to stop.\n")

    handler = _IngestHandler(config, compiler)
    observer = Observer()
    observer.schedule(handler, str(raw_dir), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[dim]Watcher stopped.[/dim]")
    observer.join()
