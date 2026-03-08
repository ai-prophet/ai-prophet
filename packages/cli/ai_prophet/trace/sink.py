"""Local trace sink -- non-blocking JSONL.gz writer.

Writes structured events to:
  {base_dir}/{experiment_slug}/traces/{participant_idx}/tick={tick_id}.jsonl.gz

Design:
- write() is non-blocking: appends to an in-memory queue.
- Background thread flushes to disk periodically.
- Per-tick file handles are cached (not opened/closed per event).
- end_tick() flushes, closes the handle, and writes a manifest.
- Payload is bounded (MAX_PAYLOAD_BYTES) to protect disk.
"""

import gzip
import json
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, TextIO

MAX_PAYLOAD_BYTES = 512_000  # 512 KB per event payload


class TraceSink:
    """Append-only JSONL.gz trace writer with background flushing."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self._queue: Queue = Queue(maxsize=10_000)
        self._handles: dict[Path, TextIO] = {}
        self._counts: dict[str, int] = defaultdict(int)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    def _path(self, experiment_slug: str, participant_idx: int, tick_id: str) -> Path:
        safe = tick_id.replace(":", "").replace("+", "")
        d = self.base_dir / experiment_slug / "traces" / str(participant_idx)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"tick={safe}.jsonl.gz"

    def write(
        self,
        experiment_slug: str,
        experiment_id: str,
        participant_idx: int,
        tick_id: str,
        stage: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Append one event. Non-blocking; drops silently if queue full."""
        record = {
            "experiment_id": experiment_id,
            "participant_idx": participant_idx,
            "tick_id": tick_id,
            "stage": stage,
            "event_type": event_type,
            "ts_utc": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        line = json.dumps(record, default=str)
        if len(line) > MAX_PAYLOAD_BYTES:
            line = json.dumps({**record, "payload": {"_truncated": True}}, default=str)
        path = self._path(experiment_slug, participant_idx, tick_id)
        try:
            self._queue.put_nowait((path, line))
        except Full:
            # Drop events under sustained backpressure to keep write() non-blocking.
            return

    def end_tick(self, experiment_slug: str, participant_idx: int, tick_id: str) -> None:
        """Flush and close handle for this tick. Write manifest."""
        self._flush_all()
        path = self._path(experiment_slug, participant_idx, tick_id)
        handle = self._handles.pop(path, None)
        if handle:
            handle.close()
        manifest = {
            "tick_id": tick_id,
            "participant_idx": participant_idx,
            "trace_file": path.name,
            "event_count": self._counts.get(str(path), 0),
            "completed_at": datetime.now(UTC).isoformat(),
        }
        manifest_path = path.with_suffix(".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2))

    def close(self) -> None:
        """Stop background thread and flush."""
        self._stop.set()
        self._thread.join(timeout=5)
        self._flush_all()
        for h in self._handles.values():
            h.close()

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self._flush_all()
            self._stop.wait(timeout=1.0)

    def _flush_all(self) -> None:
        while True:
            try:
                path, line = self._queue.get_nowait()
            except Empty:
                break
            if path not in self._handles:
                self._handles[path] = gzip.open(path, "at", encoding="utf-8")
            self._handles[path].write(line + "\n")
            self._counts[str(path)] += 1
