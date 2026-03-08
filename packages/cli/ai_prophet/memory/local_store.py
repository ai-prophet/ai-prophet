"""Local JSONL store for per-participant reasoning memory."""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from ai_prophet_core.client_models import ReasoningEntry

logger = logging.getLogger(__name__)


class LocalReasoningStore:
    """Append/read compact reasoning history from local JSONL files."""

    def __init__(self, base_dir: Path, experiment_slug: str, max_rows: int = 1000):
        self.base_dir = base_dir
        self.experiment_slug = experiment_slug
        self.max_rows = max_rows

    def append_reasoning(
        self,
        participant_idx: int,
        tick_id: datetime | str,
        reasoning: dict,
    ) -> None:
        """Append one reasoning payload for a participant/tick."""
        tick_str = tick_id.isoformat() if isinstance(tick_id, datetime) else str(tick_id)
        path = self._participant_file(participant_idx)
        if self._contains_tick(path, tick_str):
            return
        record = {
            "schema_version": 1,
            "experiment_slug": self.experiment_slug,
            "participant_idx": participant_idx,
            "tick_id": tick_str,
            "reasoning": reasoning,
            "created_at": datetime.now(UTC).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        self._prune(path)

    def read_recent_reasoning(self, participant_idx: int, limit: int) -> list[ReasoningEntry]:
        """Read the most recent valid reasoning entries for a participant."""
        if limit <= 0:
            return []
        path = self._participant_file(participant_idx)
        if not path.exists():
            return []

        recent: deque[ReasoningEntry] = deque(maxlen=limit)
        with path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "Skipping malformed local memory row (%s:%d)",
                        path,
                        line_num,
                    )
                    continue
                try:
                    entry = ReasoningEntry.model_validate(
                        {
                            "participant_idx": participant_idx,
                            "tick_id": parsed.get("tick_id"),
                            "reasoning": parsed.get("reasoning") or {},
                        }
                    )
                except Exception:
                    logger.warning(
                        "Skipping invalid local memory row (%s:%d)",
                        path,
                        line_num,
                    )
                    continue
                recent.append(entry)
        return list(recent)

    def _participant_file(self, participant_idx: int) -> Path:
        return self.base_dir / self.experiment_slug / f"participant_{participant_idx}.jsonl"

    def _contains_tick(self, path: Path, tick_id: str) -> bool:
        if not path.exists():
            return False
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if parsed.get("tick_id") == tick_id:
                    return True
        return False

    def _prune(self, path: Path) -> None:
        if self.max_rows <= 0 or not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            rows = deque(f, maxlen=self.max_rows)
        with path.open("w", encoding="utf-8") as f:
            f.writelines(rows)
            f.flush()
            os.fsync(f.fileno())

