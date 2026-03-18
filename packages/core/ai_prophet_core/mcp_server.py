"""Prophet Arena MCP Server.

Exposes the Core API as MCP tools so any MCP-compatible client
(Claude Desktop, Cursor, etc.) can run experiments and trade
on prediction markets through natural language.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from fastmcp import FastMCP

from .client import ServerAPIClient
from .client_models import TradeIntentRequest

load_dotenv()

DEFAULT_API_URL = "https://ai-prophet-core-api-998105805337.us-central1.run.app"

mcp = FastMCP(
    "Prophet Arena",
    instructions=(
        "You are connected to Prophet Arena, a benchmark for trading on real "
        "prediction markets. Use these tools to create experiments, browse "
        "live markets, submit trades, and check results. "
        "Typical workflow: health_check -> create_experiment -> add_participant "
        "-> claim_tick -> get_markets -> submit_trades -> finalize_tick -> "
        "(repeat). Each tick is a 15-minute decision window."
    ),
)

_lease_owner = str(uuid.uuid4())


def _get_client() -> ServerAPIClient:
    return ServerAPIClient(
        base_url=os.getenv("PA_SERVER_URL", DEFAULT_API_URL),
        api_key=os.getenv("PA_SERVER_API_KEY"),
    )


def _model_to_dict(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return dict(obj)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@mcp.tool
def health_check() -> dict:
    """Check if the Prophet Arena API is reachable. Call this first."""
    with _get_client() as api:
        return _model_to_dict(api.health_check())


# ---------------------------------------------------------------------------
# Experiment setup
# ---------------------------------------------------------------------------

@mcp.tool
def create_experiment(
    slug: str,
    n_ticks: int = 24,
    config_description: str = "",
) -> dict:
    """Create a new experiment (or resume an existing one by slug).

    Args:
        slug: Unique name for the experiment. Reuse to resume a stopped run.
        n_ticks: How many ticks to run. Each tick = 15 min. 24 ticks = 6 hours.
        config_description: Optional free-text description of your strategy.
    """
    with _get_client() as api:
        resp = api.create_or_get_experiment(
            slug=slug,
            config_hash=f"mcp-{slug}",
            config_json={"source": "mcp", "description": config_description},
            n_ticks=n_ticks,
        )
        return _model_to_dict(resp)


@mcp.tool
def add_participant(
    experiment_id: str,
    model_name: str = "mcp:interactive",
    starting_cash: float = 10000.0,
) -> dict:
    """Register a trading agent in an experiment.

    Args:
        experiment_id: From create_experiment.
        model_name: Label for this agent (e.g. "mcp:my-strategy").
        starting_cash: Starting balance in USD.
    """
    with _get_client() as api:
        resp = api.upsert_participant(
            experiment_id, model=model_name, rep=0, starting_cash=starting_cash,
        )
        return _model_to_dict(resp)


@mcp.tool
def get_progress(experiment_id: str) -> dict:
    """Check how many ticks are completed, in-progress, or remaining.

    Args:
        experiment_id: From create_experiment.
    """
    with _get_client() as api:
        return _model_to_dict(api.get_progress(experiment_id))


# ---------------------------------------------------------------------------
# Tick lifecycle
# ---------------------------------------------------------------------------

@mcp.tool
def claim_tick(experiment_id: str) -> dict:
    """Claim the next available tick. Returns tick_id and snapshot_id.

    If no tick is available, returns no_tick_available=true with a reason.
    If reason is "experiment_completed", the experiment is done.

    Args:
        experiment_id: From create_experiment.
    """
    with _get_client() as api:
        resp = api.claim_tick(experiment_id, _lease_owner)
        return _model_to_dict(resp)


@mcp.tool
def get_markets(tick_ts: str, snapshot_id: str | None = None) -> dict:
    """Get candidate prediction markets for a tick.

    Returns up to 256 live markets with current bid/ask prices.
    Use the tick_ts and snapshot_id from claim_tick.

    Args:
        tick_ts: ISO timestamp from claim_tick (e.g. "2026-03-16T09:30:00+00:00").
        snapshot_id: Snapshot ID from claim_tick.
    """
    with _get_client() as api:
        ts = datetime.fromisoformat(tick_ts)
        resp = api.get_candidates(ts, snapshot_id)
        markets = []
        for m in resp.markets:
            markets.append({
                "market_id": m.market_id,
                "question": m.question,
                "description": m.description,
                "resolution_time": m.resolution_time.isoformat(),
                "topic": m.topic,
                "best_bid": m.quote.best_bid,
                "best_ask": m.quote.best_ask,
                "volume_24h": m.quote.volume_24h,
            })
        return {
            "candidate_set_id": resp.candidate_set_id,
            "market_count": resp.market_count,
            "markets": markets,
        }


@mcp.tool
def submit_trades(
    experiment_id: str,
    participant_idx: int,
    tick_id: str,
    candidate_set_id: str,
    trades: list[dict],
) -> dict:
    """Submit trade intents for a tick.

    Each trade is a dict with: market_id, action (BUY/SELL), side (YES/NO),
    amount (dollar amount as string, e.g. "100").

    Trades fill at the snapshot's best bid/ask. Rejected trades are returned
    with a reason (e.g. constraint violation).

    Args:
        experiment_id: From create_experiment.
        participant_idx: From add_participant.
        tick_id: From claim_tick.
        candidate_set_id: From get_markets.
        trades: List of trade dicts. Each needs: market_id, action, side, amount.
    """
    intents = []
    for i, t in enumerate(trades):
        intents.append(TradeIntentRequest(
            market_id=t["market_id"],
            action=t["action"],
            side=t["side"],
            shares=str(t.get("amount", t.get("shares", "100"))),
            idempotency_key=f"{experiment_id}:{participant_idx}:{tick_id}:{i}",
        ))

    with _get_client() as api:
        resp = api.submit_trade_intents(
            experiment_id, participant_idx, tick_id, candidate_set_id, intents,
        )
        return _model_to_dict(resp)


@mcp.tool
def finalize_tick(
    experiment_id: str,
    participant_idx: int,
    tick_id: str,
) -> dict:
    """Finalize a participant and complete the tick. Call after submitting trades (or deciding to skip).

    Args:
        experiment_id: From create_experiment.
        participant_idx: From add_participant.
        tick_id: From claim_tick.
    """
    with _get_client() as api:
        api.finalize_participant(
            experiment_id, participant_idx, tick_id, status="COMPLETED",
        )
        resp = api.complete_tick(experiment_id, tick_id)
        return _model_to_dict(resp)


# ---------------------------------------------------------------------------
# Portfolio and reasoning
# ---------------------------------------------------------------------------

@mcp.tool
def get_portfolio(experiment_id: str, participant_idx: int) -> dict:
    """Get the current portfolio: cash, equity, and open positions.

    Args:
        experiment_id: From create_experiment.
        participant_idx: From add_participant.
    """
    with _get_client() as api:
        resp = api.get_portfolio(experiment_id, participant_idx)
        if resp is None:
            return {"status": "no_portfolio", "detail": "No trades yet."}
        return _model_to_dict(resp)


@mcp.tool
def get_reasoning(
    experiment_id: str,
    participant_idx: int | None = None,
    limit: int = 20,
) -> dict:
    """Get previously submitted reasoning/plans.

    Args:
        experiment_id: From create_experiment.
        participant_idx: Filter to a specific participant (optional).
        limit: Max entries to return.
    """
    with _get_client() as api:
        resp = api.get_reasoning(experiment_id, participant_idx, limit)
        return _model_to_dict(resp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
