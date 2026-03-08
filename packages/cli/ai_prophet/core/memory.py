"""Memory - Read-only view over EventStore.

Provides formatted history for inclusion in prompts.
Never writes - only queries EventStore.
"""

from __future__ import annotations

from datetime import datetime

from .config import MemoryConfig
from .event_store import EventStore, EventType


class Memory:
    """Read-only view over EventStore for prompt context.

    Provides:
    - Recent tick history
    - Per-market history
    - Trade history
    - Formatted strings for prompt inclusion
    """

    def __init__(self, event_store: EventStore, config: MemoryConfig | None = None):
        """Initialize Memory.

        Args:
            event_store: EventStore to query (read-only)
            config: Optional explicit memory config
        """
        self.event_store = event_store
        self._config = config or MemoryConfig()

    def get_recent_ticks_summary(self, limit: int | None = None) -> str:
        """Get summary of recent ticks.

        Args:
            limit: Number of recent ticks to include (defaults from config.yaml)

        Returns:
            Formatted string for prompt inclusion
        """
        if limit is None:
            limit = self._config.recent_ticks_limit

        # Query all tick_complete events
        events = self.event_store.get_events(event_type=EventType.TICK_COMPLETE, limit=limit)

        if not events:
            return "No previous ticks completed yet."

        lines = ["Recent tick history:"]
        for event in reversed(events):  # Chronological order (oldest first)
            tick_ts = self._normalize_tick_ts(event["tick_ts"])

            # Get trade submission for this tick
            trade_event = self.event_store.get_trade_submission(tick_ts)
            if trade_event:
                payload = trade_event["payload"]
                num_intents = payload.get("num_intents", 0)
                accepted = payload.get("accepted", 0)
                rejected = payload.get("rejected", 0)
                lines.append(
                    f"  - {tick_ts.isoformat()}: {num_intents} intents submitted, "
                    f"{accepted} filled, {rejected} rejected"
                )
            else:
                lines.append(f"  - {tick_ts.isoformat()}: Completed")

        return "\n".join(lines)

    def get_market_history(self, market_id: str, limit: int | None = None) -> str:
        """Get history of decisions for a specific market.

        Args:
            market_id: Market identifier
            limit: Number of recent events to include (defaults from config.yaml)

        Returns:
            Formatted string for prompt inclusion
        """
        if limit is None:
            limit = self._config.market_history_limit

        # Get all events for this market
        events = self.event_store.get_events(market_id=market_id, limit=limit)

        if not events:
            return f"No previous history for market {market_id}."

        lines = [f"History for market {market_id}:"]
        for event in events:
            event_type = event["event_type"]
            tick_ts = event["tick_ts"]
            payload = event["payload"]

            if event_type == EventType.REVIEW_DECISION.value:
                priority = payload.get("priority", 0)
                rationale = payload.get("rationale", "")
                lines.append(f"  - {tick_ts}: Reviewed (priority={priority})")
                if rationale and rationale != "[REDACTED]":
                    lines.append(f"    Rationale: {rationale[:100]}...")

            elif event_type == EventType.FORECAST.value:
                p_yes = payload.get("p_yes", 0)
                lines.append(f"  - {tick_ts}: Forecast p_yes={p_yes:.2f}")

            elif event_type == EventType.ACTION.value:
                recommendation = payload.get("recommendation", "HOLD")
                size_usd = payload.get("size_usd", 0)
                lines.append(f"  - {tick_ts}: Action {recommendation} size_usd={size_usd}")

        return "\n".join(lines)

    def get_trade_summary(self, tick_ts: datetime) -> str:
        """Get summary of trades executed at a specific tick.

        Args:
            tick_ts: Tick timestamp

        Returns:
            Formatted string for prompt inclusion
        """
        trade_event = self.event_store.get_trade_submission(tick_ts)

        if not trade_event:
            return f"No trades submitted at {tick_ts.isoformat()}"

        payload = trade_event["payload"]
        num_intents = payload.get("num_intents", 0)
        accepted = payload.get("accepted", 0)
        rejected = payload.get("rejected", 0)
        fills = payload.get("fills", [])
        rejections = payload.get("rejections", [])

        lines = [f"Trade summary for {tick_ts.isoformat()}:"]
        lines.append(f"  Submitted: {num_intents} intents")
        lines.append(f"  Accepted: {accepted}")
        lines.append(f"  Rejected: {rejected}")

        if fills:
            lines.append("  Fills:")
            for fill in fills:
                market_id = fill.get("market_id", "")
                action = fill.get("action", "")
                side = fill.get("side", "")
                shares = fill.get("shares", "")
                price = fill.get("price", "")
                lines.append(f"    - {action} {side} {market_id}: {shares} @ {price}")

        if rejections:
            lines.append("  Rejections:")
            for rej in rejections:
                intent_id = rej.get("intent_id", "")
                reason = rej.get("reason", "")
                lines.append(f"    - {intent_id}: {reason}")

        return "\n".join(lines)

    def get_last_review_decisions(self, tick_ts: datetime) -> list[dict]:
        """Get review decisions from last tick.

        Args:
            tick_ts: Current tick timestamp

        Returns:
            List of review decision data
        """
        # Get previous completed tick
        last_tick = self.event_store.get_last_completed_tick()
        if not last_tick:
            return []

        # Get review decisions for that tick
        events = self.event_store.get_review_decisions(last_tick)
        return [event["payload"] for event in events]

    def get_last_forecasts(self, tick_ts: datetime) -> list[dict]:
        """Get forecasts from last tick.

        Args:
            tick_ts: Current tick timestamp

        Returns:
            List of forecast data
        """
        # Get previous completed tick
        last_tick = self.event_store.get_last_completed_tick()
        if not last_tick:
            return []

        # Get forecasts for that tick
        events = self.event_store.get_forecasts(last_tick)
        return [event["payload"] for event in events]

    def get_review_context(self, limit_recent_ticks: int | None = None) -> str:
        """Get context for REVIEW stage prompt.

        Args:
            limit_recent_ticks: Number of recent ticks (defaults from config.yaml)

        Returns:
            Formatted context string
        """
        if limit_recent_ticks is None:
            limit_recent_ticks = self._config.market_history_limit
        return self.get_recent_ticks_summary(limit=limit_recent_ticks)

    def get_forecast_context(self, market_id: str) -> str:
        """Get context for FORECAST stage prompt.

        Args:
            market_id: Market to get context for

        Returns:
            Formatted context string
        """
        return self.get_market_history(market_id)  # Uses config default

    def format_for_prompt(
        self,
        include_recent_ticks: bool = True,
        include_market_history: str | None = None,
        limit: int | None = None
    ) -> str:
        """Format memory for inclusion in prompt.

        Args:
            include_recent_ticks: Include recent tick summary
            include_market_history: Market ID to include history for
            limit: Limit for history items (defaults from config.yaml)

        Returns:
            Formatted string ready for prompt
        """
        if limit is None:
            limit = self._config.recent_ticks_limit
        sections = []

        if include_recent_ticks:
            recent = self.get_recent_ticks_summary(limit=limit)
            sections.append(recent)

        if include_market_history:
            history = self.get_market_history(include_market_history, limit=limit)
            sections.append(history)

        if not sections:
            return "No historical context available."

        return "\n\n".join(sections)

    def stats(self) -> dict:
        """Get statistics about stored events.

        Returns:
            Dictionary with event counts
        """
        return {
            "total_events": self.event_store.count_events(),
            "total_ticks": self.event_store.count_ticks(),
            "last_completed_tick": self.event_store.get_last_completed_tick(),
        }

    @staticmethod
    def _normalize_tick_ts(raw: datetime | str) -> datetime:
        """Normalize DB tick_ts values to datetime."""
        if isinstance(raw, datetime):
            return raw
        return datetime.fromisoformat(raw)
