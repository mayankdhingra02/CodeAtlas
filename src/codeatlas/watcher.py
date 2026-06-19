from __future__ import annotations

import time
from pathlib import Path
from threading import Lock

from .indexer import RepositoryIndexer


class IncrementalIndexHandler:
    def __init__(self, repo_root: Path, indexer: RepositoryIndexer | None = None) -> None:
        self.repo_root = repo_root
        self.indexer = indexer or RepositoryIndexer()
        self._lock = Lock()
        self.last_report = None

    def on_any_event(self, event: object) -> None:
        src_path = getattr(event, "src_path", "")
        if src_path and not str(src_path).endswith(".py"):
            return
        if ".codeatlas" in Path(src_path).parts:
            return
        with self._lock:
            self.last_report = self.indexer.index(self.repo_root, incremental=True)


def watch_repository(repo_root: Path, indexer: RepositoryIndexer | None = None) -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception as exc:  # pragma: no cover - depends on optional environment.
        msg = "watchdog is required for `codeatlas watch`."
        raise RuntimeError(msg) from exc

    class Handler(FileSystemEventHandler):
        def __init__(self) -> None:
            self.delegate = IncrementalIndexHandler(repo_root, indexer)
            self._last_run = 0.0

        def on_any_event(self, event: object) -> None:
            now = time.monotonic()
            if now - self._last_run < 0.3:
                return
            self._last_run = now
            self.delegate.on_any_event(event)

    observer = Observer()
    observer.schedule(Handler(), str(repo_root), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
